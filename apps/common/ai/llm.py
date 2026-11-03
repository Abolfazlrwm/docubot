"""LLM provider abstraction for RAG answers.

Implementations:
- FakeLLMProvider: deterministic offline answers built from the context (tests,
  and the default when no API key is configured).
- OpenAICompatibleLLMProvider: any OpenAI-style /chat/completions endpoint.
- AnthropicLLMProvider: the Anthropic /v1/messages API.

The active provider is chosen by the LLM_PROVIDER setting via get_llm_provider().
Error contract: anything worth retrying raises LLMTransientError, anything not
raises LLMPermanentError. Messages contain lengths and ids only — never API
keys, prompts or document contents.
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Base class for LLM failures."""


class LLMTransientError(LLMError):
    """Temporary failure (timeout, connection, 429, 5xx); retry may succeed."""


class LLMPermanentError(LLMError):
    """The provider rejected the request (auth, bad request); do not retry."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    model: str


# Streaming contract: generate_stream() yields text deltas and, when the
# backend reports usage, fills the shared `meta` dict (model name + token
# counts) so callers can persist exactly what a non-streaming call would.
Meta = dict  # {"model": str, "prompt_tokens": int | None, "completion_tokens": int | None}


def _chunk_text(text: str, size: int = 24) -> Iterator[str]:
    """Split text into small deterministic pieces for pseudo-streaming."""
    return (text[i : i + size] for i in range(0, len(text), size))


def _classify_http_error(provider: str, response: httpx.Response) -> LLMError:
    """Map an HTTP error status onto the transient/permanent contract."""
    if response.status_code == 429 or 500 <= response.status_code < 600:
        return LLMTransientError(f"{provider} unavailable (HTTP {response.status_code}).")
    return LLMPermanentError(f"{provider} rejected the request (HTTP {response.status_code}).")


class LLMProvider(ABC):
    """Common interface for chat-completion style backends."""

    @abstractmethod
    def generate(self, system: str, messages: list[dict]) -> LLMResult:
        """Answer `messages` under the `system` prompt."""

    def generate_stream(self, system: str, messages: list[dict], meta: Meta) -> Iterator[str]:
        """Yield the answer as text deltas; fill `meta` with usage when known.

        The default streams a non-streaming answer in chunks, so providers
        without a streaming API still work with the SSE endpoint. Backends
        with real streaming override this.
        """
        result = self.generate(system, messages)
        meta["model"] = result.model
        meta["prompt_tokens"] = result.prompt_tokens
        meta["completion_tokens"] = result.completion_tokens
        yield from _chunk_text(result.text)


class FakeLLMProvider(LLMProvider):
    """Deterministic offline provider: echoes context, cites [1].

    Records every call so tests can inspect the exact system prompt and
    message history the RAG service built.
    """

    def __init__(self, model: str = "fake-llm") -> None:
        self.model = model
        self.calls: list[dict] = []

    def generate(self, system: str, messages: list[dict]) -> LLMResult:
        self.calls.append({"system": system, "messages": [dict(m) for m in messages]})
        user_prompt = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )
        context = _first_context_snippet(user_prompt)
        if context:
            text = f"Based on the provided context [1]: {context}"
        else:
            text = f"Echo: {user_prompt[:80]}"
        prompt_tokens = (len(system) + sum(len(m["content"]) for m in messages)) // 4
        return LLMResult(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=len(text) // 4,
            model=self.model,
        )

    def generate_stream(self, system: str, messages: list[dict], meta: Meta) -> Iterator[str]:
        """Stream the deterministic answer word by word (tests, offline demo)."""
        result = self.generate(system, messages)
        meta["model"] = result.model
        meta["prompt_tokens"] = result.prompt_tokens
        meta["completion_tokens"] = result.completion_tokens
        yield from re.findall(r"\S+\s*", result.text) or [result.text]


def _first_context_snippet(user_prompt: str) -> str:
    """Pull the first context block's text out of the RAG user prompt."""
    match = re.search(r"\[1\][^\[=]+", user_prompt, re.DOTALL)
    if not match:
        return ""
    return match.group(0)[3:].strip()[:120]


class _HttpLLMProvider(LLMProvider):
    """Shared plumbing for the HTTP-based providers."""

    provider_name = "llm"
    _path = ""

    def __init__(self, *, base_url, api_key, model, timeout, max_tokens, transport=None):
        self._model = model
        self._max_tokens = max_tokens
        self._client = self._build_client(base_url, api_key, timeout, transport)

    def _build_client(self, base_url, api_key, timeout, transport):
        raise NotImplementedError

    def generate(self, system: str, messages: list[dict]) -> LLMResult:
        payload = self._build_payload(system, messages)
        try:
            response = self._client.post(self._path, json=payload)
        except httpx.TimeoutException as exc:
            raise LLMTransientError(f"{self.provider_name} timed out after request.") from exc
        except httpx.HTTPError as exc:  # connect/read/transport problems
            raise LLMTransientError(
                f"{self.provider_name} request failed ({type(exc).__name__})."
            ) from exc
        if response.is_error:
            error = _classify_http_error(self.provider_name, response)
            logger.warning(
                "%s returned HTTP %s for model %s (prompt %d chars, %d messages)",
                self.provider_name,
                response.status_code,
                self._model,
                len(system),
                len(messages),
            )
            raise error
        return self._parse_response(response)

    def generate_stream(self, system: str, messages: list[dict], meta: Meta) -> Iterator[str]:
        """Yield text deltas from the backend's own SSE stream."""
        payload = self._build_payload(system, messages)
        payload["stream"] = True
        try:
            with self._client.stream("POST", self._path, json=payload) as response:
                if response.is_error:
                    response.read()
                    raise _classify_http_error(self.provider_name, response)
                yield from self._parse_stream(response, meta)
        except httpx.TimeoutException as exc:
            raise LLMTransientError(f"{self.provider_name} timed out mid-stream.") from exc
        except httpx.HTTPError as exc:
            raise LLMTransientError(
                f"{self.provider_name} stream failed ({type(exc).__name__})."
            ) from exc

    def _parse_stream(self, response: httpx.Response, meta: Meta) -> Iterator[str]:
        raise NotImplementedError

    @staticmethod
    def _sse_data_lines(line: str) -> Iterator[str]:
        """Yield the payload of `data:` lines; empty for other SSE lines."""
        if line.startswith("data:"):
            data = line[len("data:") :].strip()
            if data and data != "[DONE]":
                yield data

    def _build_payload(self, system: str, messages: list[dict]) -> dict:
        raise NotImplementedError

    def _parse_response(self, response: httpx.Response) -> LLMResult:
        raise NotImplementedError


class OpenAICompatibleLLMProvider(_HttpLLMProvider):
    """Any OpenAI-compatible POST /chat/completions endpoint."""

    provider_name = "openai-compatible"
    _path = "/chat/completions"

    def _build_client(self, base_url, api_key, timeout, transport):
        return httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def _build_payload(self, system, messages):
        return {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [{"role": "system", "content": system}, *messages],
        }

    def _parse_response(self, response):
        data = response.json()
        usage = data.get("usage") or {}
        return LLMResult(
            text=data["choices"][0]["message"]["content"],
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            model=data.get("model") or self._model,
        )

    def _parse_stream(self, response, meta):
        """Read OpenAI-style chunks; the final chunk may carry usage only."""
        for line in response.iter_lines():
            for data in self._sse_data_lines(line):
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    meta["prompt_tokens"] = chunk["usage"].get("prompt_tokens")
                    meta["completion_tokens"] = chunk["usage"].get("completion_tokens")
                if chunk.get("model"):
                    meta["model"] = chunk["model"]
                choices = chunk.get("choices") or []
                delta = (choices[0].get("delta") or {}).get("content") if choices else None
                if delta:
                    yield delta


class AnthropicLLMProvider(_HttpLLMProvider):
    """POST /v1/messages on api.anthropic.com."""

    provider_name = "anthropic"
    _path = "/v1/messages"

    def _build_client(self, base_url, api_key, timeout, transport):
        return httpx.Client(
            base_url=base_url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=timeout,
            transport=transport,
        )

    def _build_payload(self, system, messages):
        return {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": messages,
        }

    def _parse_response(self, response):
        data = response.json()
        usage = data.get("usage") or {}
        text = "".join(block["text"] for block in data["content"] if block.get("type") == "text")
        return LLMResult(
            text=text,
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
            model=data.get("model") or self._model,
        )

    def _parse_stream(self, response, meta):
        """Read Anthropic SSE events: message_start, content_block_delta, message_delta."""
        for line in response.iter_lines():
            for data in self._sse_data_lines(line):
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                event_type = event.get("type")
                if event_type == "message_start":
                    usage = (event.get("message") or {}).get("usage") or {}
                    meta["prompt_tokens"] = usage.get("input_tokens")
                    if (event.get("message") or {}).get("model"):
                        meta["model"] = event["message"]["model"]
                elif event_type == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        yield delta["text"]
                elif event_type == "message_delta":
                    usage = event.get("usage") or {}
                    if usage.get("output_tokens") is not None:
                        meta["completion_tokens"] = usage["output_tokens"]


def get_llm_provider() -> LLMProvider:
    """Build the provider selected by the LLM_PROVIDER setting."""
    provider = settings.LLM_PROVIDER
    if provider == "fake":
        return FakeLLMProvider()
    if provider == "openai":
        return OpenAICompatibleLLMProvider(
            base_url=settings.LLM_BASE_URL or "https://api.openai.com/v1",
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_tokens=settings.LLM_MAX_TOKENS,
        )
    if provider == "anthropic":
        return AnthropicLLMProvider(
            base_url=settings.LLM_BASE_URL or "https://api.anthropic.com",
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_tokens=settings.LLM_MAX_TOKENS,
        )
    raise LLMPermanentError(f"Unknown LLM_PROVIDER '{provider}'.")
