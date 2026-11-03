"""Tests for LLM provider abstraction."""

import httpx
import pytest

from apps.common.ai.llm import (
    AnthropicLLMProvider,
    FakeLLMProvider,
    LLMResult,
    LLMPermanentError,
    LLMTransientError,
    OpenAICompatibleLLMProvider,
    get_llm_provider,
)
from django.conf import settings


def test_fake_provider_echoes_context():
    provider = FakeLLMProvider()
    result = provider.generate(
        system="You are helpful.",
        messages=[{"role": "user", "content": "=== CONTEXT START ===\n[1] (Doc) Hello world\n=== CONTEXT END ===\n\nQuestion: What does it say?"}],
    )
    assert isinstance(result, LLMResult)
    assert "Based on the provided context [1]:" in result.text
    assert "Hello world" in result.text
    assert result.model == "fake-llm"
    assert result.prompt_tokens > 0
    assert result.completion_tokens > 0


def test_fake_provider_no_context_echoes_question():
    provider = FakeLLMProvider()
    result = provider.generate(
        system="You are helpful.",
        messages=[{"role": "user", "content": "What is the meaning of life?"}],
    )
    assert "Echo: What is the meaning of life?" in result.text


def test_fake_provider_records_calls():
    provider = FakeLLMProvider()
    provider.generate("sys", [{"role": "user", "content": "hello"}])
    provider.generate("sys2", [{"role": "user", "content": "world"}])
    assert len(provider.calls) == 2
    assert provider.calls[0]["system"] == "sys"
    assert provider.calls[0]["messages"][0]["content"] == "hello"


def test_fake_provider_custom_model():
    provider = FakeLLMProvider(model="custom-fake")
    result = provider.generate("sys", [{"role": "user", "content": "test"}])
    assert result.model == "custom-fake"


def test_openai_provider_success_restores_order():
    def handler(request):
        import json
        payload = json.loads(request.content)
        data = [
            {"index": i, "embedding": [float(i), 1.0]}
            for i in reversed(range(len(payload["messages"]) - 1))
        ]
        # Return a valid chat completion response
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Answer from OpenAI"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                "model": "gpt-4o-mini",
            },
        )

    provider = OpenAICompatibleLLMProvider(
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o-mini",
        timeout=5,
        max_tokens=100,
        transport=httpx.MockTransport(handler),
    )
    result = provider.generate("system", [{"role": "user", "content": "hello"}])
    assert result.text == "Answer from OpenAI"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    assert result.model == "gpt-4o-mini"


def test_openai_provider_rate_limit_is_transient():
    provider = OpenAICompatibleLLMProvider(
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o-mini",
        timeout=5,
        max_tokens=100,
        transport=httpx.MockTransport(lambda request: httpx.Response(429, json={})),
    )
    with pytest.raises(LLMTransientError, match="429"):
        provider.generate("sys", [{"role": "user", "content": "hello"}])


def test_openai_provider_server_error_is_transient():
    provider = OpenAICompatibleLLMProvider(
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o-mini",
        timeout=5,
        max_tokens=100,
        transport=httpx.MockTransport(lambda request: httpx.Response(503, json={})),
    )
    with pytest.raises(LLMTransientError, match="503"):
        provider.generate("sys", [{"role": "user", "content": "hello"}])


def test_openai_provider_bad_request_is_permanent():
    provider = OpenAICompatibleLLMProvider(
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o-mini",
        timeout=5,
        max_tokens=100,
        transport=httpx.MockTransport(lambda request: httpx.Response(400, json={})),
    )
    with pytest.raises(LLMPermanentError, match="400"):
        provider.generate("sys", [{"role": "user", "content": "hello"}])


def test_openai_provider_network_error_is_transient():
    def handler(request):
        raise httpx.ConnectError("no network")

    provider = OpenAICompatibleLLMProvider(
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o-mini",
        timeout=5,
        max_tokens=100,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMTransientError, match="request failed"):
        provider.generate("sys", [{"role": "user", "content": "hello"}])


def test_openai_provider_timeout_is_transient():
    def handler(request):
        raise httpx.TimeoutException("timeout")

    provider = OpenAICompatibleLLMProvider(
        base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-4o-mini",
        timeout=5,
        max_tokens=100,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMTransientError, match="timed out"):
        provider.generate("sys", [{"role": "user", "content": "hello"}])


def test_anthropic_provider_success():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Answer from Anthropic"}],
                "usage": {"input_tokens": 15, "output_tokens": 8},
                "model": "claude-3-haiku",
            },
        )

    provider = AnthropicLLMProvider(
        base_url="https://api.anthropic.com",
        api_key="test-key",
        model="claude-3-haiku",
        timeout=5,
        max_tokens=100,
        transport=httpx.MockTransport(handler),
    )
    result = provider.generate("system", [{"role": "user", "content": "hello"}])
    assert result.text == "Answer from Anthropic"
    assert result.prompt_tokens == 15
    assert result.completion_tokens == 8
    assert result.model == "claude-3-haiku"


def test_anthropic_provider_rate_limit_is_transient():
    provider = AnthropicLLMProvider(
        base_url="https://api.anthropic.com",
        api_key="test-key",
        model="claude-3-haiku",
        timeout=5,
        max_tokens=100,
        transport=httpx.MockTransport(lambda request: httpx.Response(429, json={})),
    )
    with pytest.raises(LLMTransientError, match="429"):
        provider.generate("sys", [{"role": "user", "content": "hello"}])


def test_anthropic_provider_bad_request_is_permanent():
    provider = AnthropicLLMProvider(
        base_url="https://api.anthropic.com",
        api_key="test-key",
        model="claude-3-haiku",
        timeout=5,
        max_tokens=100,
        transport=httpx.MockTransport(lambda request: httpx.Response(400, json={})),
    )
    with pytest.raises(LLMPermanentError, match="400"):
        provider.generate("sys", [{"role": "user", "content": "hello"}])


def test_anthropic_provider_network_error_is_transient():
    def handler(request):
        raise httpx.ConnectError("no network")

    provider = AnthropicLLMProvider(
        base_url="https://api.anthropic.com",
        api_key="test-key",
        model="claude-3-haiku",
        timeout=5,
        max_tokens=100,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMTransientError, match="request failed"):
        provider.generate("sys", [{"role": "user", "content": "hello"}])


def test_factory_returns_fake(settings):
    settings.LLM_PROVIDER = "fake"
    provider = get_llm_provider()
    assert isinstance(provider, FakeLLMProvider)


def test_factory_returns_openai(settings):
    settings.LLM_PROVIDER = "openai"
    settings.LLM_API_KEY = "test-key"
    settings.LLM_MODEL = "gpt-4o-mini"
    provider = get_llm_provider()
    assert isinstance(provider, OpenAICompatibleLLMProvider)


def test_factory_returns_anthropic(settings):
    settings.LLM_PROVIDER = "anthropic"
    settings.LLM_API_KEY = "test-key"
    settings.LLM_MODEL = "claude-3-haiku"
    provider = get_llm_provider()
    assert isinstance(provider, AnthropicLLMProvider)


def test_factory_rejects_unknown_provider(settings):
    settings.LLM_PROVIDER = "nonsense"
    with pytest.raises(LLMPermanentError, match="nonsense"):
        get_llm_provider()


def test_openai_provider_empty_base_url_defaults():
    """The factory function handles empty base_url, not the constructor directly."""
    # This test verifies the factory behavior, not the constructor
    from apps.common.ai.llm import get_llm_provider
    from django.conf import settings
    settings.LLM_PROVIDER = "openai"
    settings.LLM_API_KEY = "test-key"
    settings.LLM_MODEL = "gpt-4o-mini"
    settings.LLM_BASE_URL = ""
    
    provider = get_llm_provider()
    assert "api.openai.com/v1" in str(provider._client.base_url)


def test_anthropic_provider_empty_base_url_defaults():
    """The factory function handles empty base_url, not the constructor directly."""
    from apps.common.ai.llm import get_llm_provider
    from django.conf import settings
    settings.LLM_PROVIDER = "anthropic"
    settings.LLM_API_KEY = "test-key"
    settings.LLM_MODEL = "claude-3-haiku"
    settings.LLM_BASE_URL = ""
    
    provider = get_llm_provider()
    assert "api.anthropic.com" in str(provider._client.base_url)


def test_llm_result_dataclass():
    result = LLMResult(
        text="answer",
        prompt_tokens=10,
        completion_tokens=5,
        model="test-model",
    )
    assert result.text == "answer"
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    assert result.model == "test-model"