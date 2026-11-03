import sys

import httpx
import pytest

from apps.common.ai import embeddings
from apps.common.ai.embeddings import (
    FakeEmbeddingProvider,
    LocalEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    embed_texts,
    get_embedding_provider,
)
from apps.common.exceptions import PermanentProcessingError, TransientProcessingError


def test_fake_provider_is_deterministic_and_normalized():
    provider = FakeEmbeddingProvider(dimensions=384)

    first, second = provider.embed(["hello", "hello"])
    again = provider.embed(["hello"])[0]

    assert first == second == again
    assert len(first) == 384
    assert first != provider.embed(["something else"])[0]
    norm = sum(value * value for value in first) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-9)


def test_embed_texts_batches_requests():
    class SpyProvider(FakeEmbeddingProvider):
        batch_sizes: list[int] = []

        def embed(self, texts):
            self.batch_sizes.append(len(texts))
            return super().embed(texts)

    spy = SpyProvider(dimensions=8)

    vectors = embed_texts(spy, [f"text-{i}" for i in range(70)], batch_size=32)

    assert spy.batch_sizes == [32, 32, 6]
    assert len(vectors) == 70
    assert all(len(vector) == 8 for vector in vectors)


def test_factory_returns_fake(settings):
    settings.EMBEDDING_PROVIDER = "fake"

    assert isinstance(get_embedding_provider(), FakeEmbeddingProvider)


def test_factory_returns_local(settings):
    settings.EMBEDDING_PROVIDER = "local"
    settings.EMBEDDING_MODEL = "some-model"
    settings.EMBEDDING_CACHE_DIR = "/cache"

    provider = get_embedding_provider()

    assert isinstance(provider, LocalEmbeddingProvider)
    assert provider.model_name == "some-model"
    assert provider.cache_dir == "/cache"


def test_factory_returns_openai(settings):
    settings.EMBEDDING_PROVIDER = "openai"
    settings.EMBEDDING_API_BASE = "https://embed.example.com/v1"
    settings.EMBEDDING_API_KEY = "secret"
    settings.EMBEDDING_API_MODEL = "embed-v1"

    provider = get_embedding_provider()

    assert isinstance(provider, OpenAICompatibleEmbeddingProvider)


def test_factory_rejects_unknown_provider(settings):
    settings.EMBEDDING_PROVIDER = "nonsense"

    with pytest.raises(PermanentProcessingError, match="nonsense"):
        get_embedding_provider()


def make_openai_provider(handler, dimensions=2):
    return OpenAICompatibleEmbeddingProvider(
        base_url="https://embed.example.com/v1",
        api_key="secret",
        model="embed-v1",
        dimensions=dimensions,
        timeout=5,
        transport=httpx.MockTransport(handler),
    )


def test_openai_provider_success_restores_order():
    def handler(request):
        import json

        payload = json.loads(request.content)
        # Deliberately scrambled: vectors arrive in reverse list order, each
        # tagged with its index — the client must sort by index.
        data = [
            {"index": i, "embedding": [float(i), 1.0]}
            for i in reversed(range(len(payload["input"])))
        ]
        return httpx.Response(200, json={"data": data})

    provider = make_openai_provider(handler)

    assert provider.embed(["a", "b", "c"]) == [[0.0, 1.0], [1.0, 1.0], [2.0, 1.0]]


def test_openai_rate_limit_is_transient():
    provider = make_openai_provider(lambda request: httpx.Response(429, json={}))

    with pytest.raises(TransientProcessingError, match="429"):
        provider.embed(["a"])


def test_openai_server_error_is_transient():
    provider = make_openai_provider(lambda request: httpx.Response(503, json={}))

    with pytest.raises(TransientProcessingError, match="503"):
        provider.embed(["a"])


def test_openai_bad_request_is_permanent():
    provider = make_openai_provider(lambda request: httpx.Response(400, json={}))

    with pytest.raises(PermanentProcessingError, match="400"):
        provider.embed(["a"])


def test_openai_network_error_is_transient():
    def handler(request):
        raise httpx.ConnectError("no network")

    provider = make_openai_provider(handler)

    with pytest.raises(TransientProcessingError, match="request failed"):
        provider.embed(["a"])


def test_openai_dimension_mismatch_is_permanent():
    provider = make_openai_provider(
        lambda request: httpx.Response(
            200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]}
        )
    )

    with pytest.raises(PermanentProcessingError, match="3 dimensions"):
        provider.embed(["a"])


def test_openai_count_mismatch_is_permanent():
    provider = make_openai_provider(
        lambda request: httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})
    )

    with pytest.raises(PermanentProcessingError, match="1 vectors"):
        provider.embed(["a", "b"])


class StubModel:
    """Stands in for a loaded fastembed model."""

    def __init__(self, vectors=None, error=None):
        self.vectors = vectors or [[0.1, 0.2]]
        self.error = error
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        if self.error:
            raise self.error
        return [list(vector) for vector in self.vectors[: len(texts)]]


@pytest.fixture
def clean_model_cache(monkeypatch):
    monkeypatch.setattr(embeddings, "_MODEL_CACHE", {})
    return embeddings._MODEL_CACHE


def test_local_provider_loads_model_once_and_embeds(monkeypatch, clean_model_cache):
    stub = StubModel(vectors=[[0.5, 0.5]])
    seen = {}

    def fake_loader(model_name, cache_dir):
        seen["key"] = (model_name, cache_dir)
        return stub

    monkeypatch.setattr(embeddings, "_load_fastembed_model", fake_loader)
    provider = LocalEmbeddingProvider(model_name="m", cache_dir="/cache", dimensions=2)

    assert provider.embed(["a"]) == [[0.5, 0.5]]
    assert provider.embed(["b"]) == [[0.5, 0.5]]
    assert stub.calls == 2
    assert seen["key"] == ("m", "/cache")


def test_local_provider_dimension_mismatch_is_permanent(monkeypatch, clean_model_cache):
    monkeypatch.setattr(
        embeddings, "_MODEL_CACHE", {("m", "/cache"): StubModel(vectors=[[0.1, 0.2, 0.3]])}
    )
    provider = LocalEmbeddingProvider(model_name="m", cache_dir="/cache", dimensions=2)

    with pytest.raises(PermanentProcessingError, match="3 dimensions"):
        provider.embed(["a"])


def test_local_provider_value_error_is_permanent(monkeypatch, clean_model_cache):
    monkeypatch.setattr(
        embeddings, "_MODEL_CACHE", {("m", "/cache"): StubModel(error=ValueError("bad input"))}
    )
    provider = LocalEmbeddingProvider(model_name="m", cache_dir="/cache", dimensions=2)

    with pytest.raises(PermanentProcessingError, match="bad input"):
        provider.embed(["a"])


def test_local_provider_runtime_error_is_transient(monkeypatch, clean_model_cache):
    monkeypatch.setattr(
        embeddings, "_MODEL_CACHE", {("m", "/cache"): StubModel(error=RuntimeError("onnx blew up"))}
    )
    provider = LocalEmbeddingProvider(model_name="m", cache_dir="/cache", dimensions=2)

    with pytest.raises(TransientProcessingError, match="inference failed"):
        provider.embed(["a"])


def test_model_download_failure_is_transient(monkeypatch, clean_model_cache):
    import types

    fake_module = types.ModuleType("fastembed")

    def broken_text_embedding(*args, **kwargs):
        raise ConnectionError("download interrupted")

    fake_module.TextEmbedding = broken_text_embedding
    monkeypatch.setitem(sys.modules, "fastembed", fake_module)

    with pytest.raises(TransientProcessingError, match="failed to load"):
        embeddings._load_fastembed_model("m", "/cache")
