"""Tests for the SSE streaming answer endpoint and stream-capable providers."""

import json

import httpx
import pytest
from django.urls import reverse
from rest_framework import status

from apps.chat.models import Message
from apps.chat.services import NO_CONTEXT_ANSWER
from apps.common.ai.llm import (
    AnthropicLLMProvider,
    FakeLLMProvider,
    LLMTransientError,
    OpenAICompatibleLLMProvider,
)
from tests.factories import DocumentFactory

pytestmark = pytest.mark.django_db


def stream_url(conversation_pk: int) -> str:
    return reverse("chat:conversation-messages-stream", args=[conversation_pk])


def make_ready_document(owner, title="Doc", chunks_data=None):
    from apps.common.ai.embeddings import FakeEmbeddingProvider
    from apps.documents.models import Chunk, Document

    chunks_data = chunks_data if chunks_data is not None else ["The capital of France is Paris."]
    document = Document.objects.create(
        owner=owner,
        title=title,
        file="test.txt",
        original_filename="test.txt",
        content_type="text/plain",
        size_bytes=sum(len(c) for c in chunks_data),
        status=Document.Status.READY,
    )
    provider = FakeEmbeddingProvider(dimensions=384)
    for index, content in enumerate(chunks_data):
        Chunk.objects.create(
            document=document,
            chunk_index=index,
            content=content,
            embedding=provider.embed([content])[0],
        )
    return document


def read_sse(response) -> list[tuple[str, dict]]:
    """Consume a StreamingHttpResponse and return parsed (event, payload) pairs."""
    assert response.streaming
    body = b"".join(chunk for chunk in response.streaming_content).decode("utf-8")
    events = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event_name = ""
        payload = {}
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line[len("event: ") :]
            elif line.startswith("data: "):
                payload = json.loads(line[len("data: ") :])
        events.append((event_name, payload))
    return events


def patch_provider(monkeypatch, provider):
    from apps.chat import services as chat_services

    monkeypatch.setattr(chat_services, "get_llm_provider", lambda: provider)
    return provider


class TestStreamEndpoint:
    """POST /conversations/{id}/messages/stream/"""

    def test_requires_authentication(self, api, user):
        conversation = make_ready_document(user) and None
        from apps.chat.models import Conversation

        conversation = Conversation.objects.create(owner=user)
        response = api.post(stream_url(conversation.pk), {"content": "Paris?"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_other_users_conversation_returns_404(self, auth_api, user):
        from django.contrib.auth import get_user_model

        from apps.chat.models import Conversation

        other = get_user_model().objects.create_user(
            username="other", email="other@example.com", password="pass"
        )
        conversation = Conversation.objects.create(owner=other)
        response = auth_api.post(stream_url(conversation.pk), {"content": "Paris?"})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_streams_sources_then_deltas_then_done(self, auth_api, user, monkeypatch):
        make_ready_document(user, "Doc", ["The capital of France is Paris."])
        from apps.chat.models import Conversation

        conversation = Conversation.objects.create(owner=user)
        provider = patch_provider(monkeypatch, FakeLLMProvider())

        response = auth_api.post(
            stream_url(conversation.pk), {"content": "What is the capital of France?"}
        )

        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"] == "text/event-stream"
        assert response["Cache-Control"] == "no-cache"

        events = read_sse(response)
        names = [name for name, _ in events]
        assert names[0] == "sources"
        assert names[-1] == "done"
        assert names[1:-1] == ["delta"] * len(names[1:-1]) and len(names) > 2

        # sources event: numbered snapshot matching the retrieval result
        sources = events[0][1]["sources"]
        assert sources[0]["document_title"] == "Doc"
        assert "Paris" in sources[0]["snippet"]

        # deltas concatenate to the persisted assistant content
        streamed_text = "".join(payload["text"] for name, payload in events if name == "delta")
        assert streamed_text
        done = events[-1][1]
        assert done["content"] == streamed_text
        assert done["message_id"] > 0
        assert done["model"] == "fake-llm"
        assert done["prompt_tokens"] > 0

        # the turn was persisted exactly like the JSON endpoint
        assert conversation.messages.count() == 2
        assistant = conversation.messages.filter(role=Message.Role.ASSISTANT).get()
        assert assistant.content == streamed_text
        assert assistant.sources[0]["document_title"] == "Doc"
        assert provider.calls  # the LLM was actually called

    def test_no_context_streams_single_done_event(self, auth_api, user, monkeypatch):
        make_ready_document(user, "Doc", ["unrelated content"])
        from apps.chat.models import Conversation

        conversation = Conversation.objects.create(owner=user)
        provider = patch_provider(monkeypatch, FakeLLMProvider())

        response = auth_api.post(
            stream_url(conversation.pk), {"content": "completely different topic"}
        )

        events = read_sse(response)
        names = [name for name, _ in events]
        assert names == ["done"]
        assert events[0][1]["content"] == NO_CONTEXT_ANSWER
        assert events[0][1]["sources"] == []
        assert len(provider.calls) == 0  # LLM never called without context

    def test_llm_error_yields_error_event_and_persists_nothing(self, auth_api, user, monkeypatch):
        make_ready_document(user, "Doc", ["The capital of France is Paris."])
        from apps.chat.models import Conversation

        conversation = Conversation.objects.create(owner=user)

        class FailingProvider(FakeLLMProvider):
            def generate(self, system, messages):
                raise LLMTransientError("Service unavailable")

        patch_provider(monkeypatch, FailingProvider())

        response = auth_api.post(
            stream_url(conversation.pk), {"content": "What is the capital of France?"}
        )

        events = read_sse(response)
        names = [name for name, _ in events]
        assert "sources" in names
        assert names[-1] == "error"
        assert "unavailable" in events[-1][1]["detail"].lower()
        assert conversation.messages.count() == 0

    def test_stream_is_throttled_by_chat_scope(self, auth_api, user, settings, monkeypatch):
        settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["chat"] = "1/min"
        make_ready_document(user, "Doc", ["The capital of France is Paris."])
        from apps.chat.models import Conversation

        conversation = Conversation.objects.create(owner=user)
        patch_provider(monkeypatch, FakeLLMProvider())

        first = auth_api.post(
            stream_url(conversation.pk), {"content": "What is the capital of France?"}
        )
        second = auth_api.post(
            stream_url(conversation.pk), {"content": "What is the capital of France?"}
        )

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    def test_validates_payload(self, auth_api, user):
        from apps.chat.models import Conversation

        conversation = Conversation.objects.create(owner=user)
        response = auth_api.post(stream_url(conversation.pk), {})
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestProviderStreaming:
    """generate_stream() contracts per provider."""

    def test_fake_provider_streams_words_and_meta(self):
        provider = FakeLLMProvider()
        meta = {"model": "", "prompt_tokens": None, "completion_tokens": None}

        deltas = list(provider.generate_stream("sys", [{"role": "user", "content": "hi"}], meta))

        assert deltas
        assert "".join(deltas) == provider.generate("sys", [{"role": "user", "content": "hi"}]).text
        assert meta["model"] == "fake-llm"
        assert meta["prompt_tokens"] > 0
        assert meta["completion_tokens"] > 0

    def test_base_provider_streams_non_streaming_backends(self):
        class LegacyProvider(FakeLLMProvider):
            """A provider that only implements generate()."""

            generate_stream = None  # sentinel: must fall back to the base class

        provider = LegacyProvider()
        meta = {"model": "", "prompt_tokens": None, "completion_tokens": None}
        # Call through the ABC explicitly, bypassing the deleted attribute.
        deltas = list(FakeLLMProvider.generate_stream(provider, "sys", [], meta))
        assert "".join(deltas)
        assert meta["model"] == "fake-llm"

    def _openai_provider(self, handler) -> OpenAICompatibleLLMProvider:
        return OpenAICompatibleLLMProvider(
            base_url="https://llm.example/v1",
            api_key="k",
            model="g-test",
            timeout=5,
            max_tokens=16,
            transport=httpx.MockTransport(handler),
        )

    def test_openai_stream_parses_deltas_and_usage(self):
        sse_body = (
            'data: {"model":"g-test","choices":[{"delta":{"content":"Hel"}}]}\n\n'
            'data: {"model":"g-test","choices":[{"delta":{"content":"lo"}}]}\n\n'
            'data: {"model":"g-test","choices":[],'
            '"usage":{"prompt_tokens":7,"completion_tokens":2}}\n\n'
            "data: [DONE]\n\n"
        )

        def handler(request):
            assert json.loads(request.content)["stream"] is True
            return httpx.Response(200, content=sse_body.encode())

        provider = self._openai_provider(handler)
        meta = {"model": "", "prompt_tokens": None, "completion_tokens": None}

        deltas = list(provider.generate_stream("sys", [{"role": "user", "content": "q"}], meta))

        assert deltas == ["Hel", "lo"]
        assert meta == {
            "model": "g-test",
            "prompt_tokens": 7,
            "completion_tokens": 2,
        }

    def test_openai_stream_maps_http_error(self):
        def handler(request):
            return httpx.Response(429, json={"error": "slow down"})

        provider = self._openai_provider(handler)
        meta = {}

        with pytest.raises(LLMTransientError):
            list(provider.generate_stream("sys", [{"role": "user", "content": "q"}], meta))

    def test_anthropic_stream_parses_events(self):
        sse_body = (
            'event: message_start\ndata: {"type":"message_start","message":'
            '{"model":"claude-test","usage":{"input_tokens":11}}}\n\n'
            'event: content_block_delta\ndata: {"type":"content_block_delta",'
            '"delta":{"type":"text_delta","text":"Hi"}}\n\n'
            'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":1}}\n\n'
        )

        def handler(request):
            return httpx.Response(200, content=sse_body.encode())

        provider = AnthropicLLMProvider(
            base_url="https://api.anthropic.example",
            api_key="k",
            model="claude-test",
            timeout=5,
            max_tokens=16,
            transport=httpx.MockTransport(handler),
        )
        meta = {"model": "", "prompt_tokens": None, "completion_tokens": None}

        deltas = list(provider.generate_stream("sys", [{"role": "user", "content": "q"}], meta))

        assert deltas == ["Hi"]
        assert meta == {"model": "claude-test", "prompt_tokens": 11, "completion_tokens": 1}


class TestDocumentFactorySanity:
    """Guard the shared factory helper used by the streaming tests."""

    def test_ready_document_is_searchable(self, user):
        document = make_ready_document(user, "Fact", ["Paris is the capital of France."])
        document.refresh_from_db()
        assert document.status == "ready"
        assert document.chunks.count() == 1


def test_document_factory_import_still_works():
    """DocumentFactory stays importable for other suites."""
    assert DocumentFactory is not None
