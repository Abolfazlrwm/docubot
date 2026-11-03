"""Embedding provider abstraction.

Implementations:
- FakeEmbeddingProvider: deterministic hashed bag-of-words vectors, no network
  (tests and offline demos: related texts land close together).
- LocalEmbeddingProvider: fastembed ONNX models, loaded once per process.
- OpenAICompatibleEmbeddingProvider: any OpenAI-style /embeddings HTTP endpoint.

The active provider is chosen by the EMBEDDING_PROVIDER setting via
get_embedding_provider(); callers embed through embed_texts() which batches.
"""

import hashlib
import math
import re
import struct
from abc import ABC, abstractmethod

import httpx
from django.conf import settings

from apps.common.exceptions import PermanentProcessingError, TransientProcessingError


class EmbeddingProvider(ABC):
    """Common interface for text embedding backends."""

    dimensions: int

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts (passages/chunks); returns one vector per text."""

    def embed_query(self, text: str) -> list[float]:
        """Embed a single search query.

        Some model families (e5, BGE) require different prefixes or inputs for
        queries vs passages; providers override this where the backend offers a
        query-specific path. The default treats queries like passages.
        """
        return self.embed([text])[0]


class FakeEmbeddingProvider(EmbeddingProvider):
    """Deterministic hashed bag-of-words vectors; tests and offline demos.

    Every token contributes a stable pseudo-random vector; the normalized sum
    is the text's embedding. Identical texts embed identically and texts that
    share words land close together, so retrieval behaves sensibly without a
    neural model. This is NOT semantic quality — production deployments should
    use the local (fastembed) or openai provider.
    """

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_deterministic_vector(text, self.dimensions) for text in texts]


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _deterministic_vector(text: str, dimensions: int) -> list[float]:
    """A stable, normalized vector derived only from the text content."""
    tokens = _TOKEN_RE.findall(text.lower()) or [text or "\x00empty"]
    vector = [0.0] * dimensions
    for token in tokens:
        for index, value in enumerate(_token_hash_vector(token, dimensions)):
            vector[index] += value
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:  # practically impossible, but keep the contract
        return [1.0] + [0.0] * (dimensions - 1)
    return [value / norm for value in vector]


def _token_hash_vector(token: str, dimensions: int) -> list[float]:
    """A deterministic pseudo-random vector for one token (hash-expansion)."""
    digest = b""
    counter = 0
    while len(digest) < dimensions * 4:
        digest += hashlib.sha256(f"token:{counter}:{token}".encode()).digest()
        counter += 1
    values = struct.unpack(f"<{dimensions}I", digest[: dimensions * 4])
    return [value / 4294967295.0 * 2.0 - 1.0 for value in values]


# One model instance per (model, cache dir) per process: workers load once.
_MODEL_CACHE: dict[tuple[str, str], object] = {}


def _load_fastembed_model(model_name: str, cache_dir: str):
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:  # dependency missing: a configuration problem
        raise PermanentProcessingError("The fastembed package is not installed.") from exc

    key = (model_name, cache_dir)
    if key not in _MODEL_CACHE:
        try:
            # First use downloads model weights from the network.
            _MODEL_CACHE[key] = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
        except Exception as exc:
            raise TransientProcessingError(
                f"Embedding model '{model_name}' failed to load: {exc}"
            ) from exc
    return _MODEL_CACHE[key]


class LocalEmbeddingProvider(EmbeddingProvider):
    """Local ONNX embeddings via fastembed; the default provider."""

    def __init__(self, *, model_name: str, cache_dir: str, dimensions: int) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.dimensions = dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed passage texts; uses fastembed's passage-specific path if any."""
        model = _load_fastembed_model(self.model_name, self.cache_dir)
        method = getattr(model, "passage_embed", None) or model.embed
        return _run_model(method, texts, self.dimensions)

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query; uses fastembed's query-specific path if any.

        For symmetric models like paraphrase-multilingual-MiniLM-L12-v2 this is
        identical to embed(); asymmetric models (e5/BGE) need it for prefixes.
        """
        model = _load_fastembed_model(self.model_name, self.cache_dir)
        method = getattr(model, "query_embed", None) or model.embed
        return _run_model(method, [text], self.dimensions)[0]

    def _check_dimensions(self, vectors: list[list[float]]) -> None:
        if vectors and len(vectors[0]) != self.dimensions:
            raise PermanentProcessingError(
                f"Embedding model returned {len(vectors[0])} dimensions, "
                f"expected {self.dimensions}."
            )


def _run_model(method, texts: list[str], dimensions: int) -> list[list[float]]:
    try:
        vectors = [_as_list(vector) for vector in method(texts)]
    except ValueError as exc:  # the model rejected the input
        raise PermanentProcessingError(f"Embedding input was rejected: {exc}") from exc
    except Exception as exc:  # ONNX runtime and IO failures are worth a retry
        raise TransientProcessingError(f"Embedding inference failed: {exc}") from exc
    if vectors and len(vectors[0]) != dimensions:
        raise PermanentProcessingError(
            f"Embedding model returned {len(vectors[0])} dimensions, expected {dimensions}."
        )
    return vectors


def _as_list(vector) -> list[float]:
    return vector.tolist() if hasattr(vector, "tolist") else list(vector)


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """Minimal client for an OpenAI-compatible POST /embeddings endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimensions: int,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.dimensions = dimensions
        self._model = model
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.post("/embeddings", json={"model": self._model, "input": texts})
        except httpx.HTTPError as exc:  # connect/timeout/network problems
            raise TransientProcessingError(f"Embedding request failed: {exc}") from exc
        if response.status_code == 429 or 500 <= response.status_code < 600:
            raise TransientProcessingError(
                f"Embedding service unavailable (HTTP {response.status_code})."
            )
        if response.is_error:
            raise PermanentProcessingError(
                f"Embedding request rejected (HTTP {response.status_code}): {response.text[:200]}"
            )
        vectors = [
            item["embedding"]
            for item in sorted(response.json()["data"], key=lambda item: item["index"])
        ]
        if len(vectors) != len(texts):
            raise PermanentProcessingError(
                f"Embedding service returned {len(vectors)} vectors for {len(texts)} texts."
            )
        if vectors and len(vectors[0]) != self.dimensions:
            raise PermanentProcessingError(
                f"Embedding service returned {len(vectors[0])} dimensions, "
                f"expected {self.dimensions}."
            )
        return vectors


def get_embedding_provider() -> EmbeddingProvider:
    """Build the provider selected by the EMBEDDING_PROVIDER setting."""
    provider = settings.EMBEDDING_PROVIDER
    if provider == "fake":
        return FakeEmbeddingProvider(dimensions=settings.EMBEDDING_DIMENSIONS)
    if provider == "local":
        return LocalEmbeddingProvider(
            model_name=settings.EMBEDDING_MODEL,
            cache_dir=settings.EMBEDDING_CACHE_DIR,
            dimensions=settings.EMBEDDING_DIMENSIONS,
        )
    if provider == "openai":
        return OpenAICompatibleEmbeddingProvider(
            base_url=settings.EMBEDDING_API_BASE,
            api_key=settings.EMBEDDING_API_KEY,
            model=settings.EMBEDDING_API_MODEL,
            dimensions=settings.EMBEDDING_DIMENSIONS,
            timeout=settings.EMBEDDING_TIMEOUT_SECONDS,
        )
    raise PermanentProcessingError(f"Unknown EMBEDDING_PROVIDER '{provider}'.")


def embed_texts(
    provider: EmbeddingProvider, texts: list[str], batch_size: int | None = None
) -> list[list[float]]:
    """Embed texts in batches; returns vectors in input order."""
    batch_size = batch_size if batch_size is not None else settings.EMBEDDING_BATCH_SIZE
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(provider.embed(texts[start : start + batch_size]))
    return vectors
