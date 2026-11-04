"""Owner-scoped retrieval over document chunks: vector, keyword and hybrid.

Three signals, one interface:

- ``_vector_search``   cosine distance over pgvector embeddings (semantic match).
- ``keyword_search``   Postgres full-text search over a generated tsvector
                       column (literal terms: names, identifiers, error codes).
- ``hybrid_retrieve``  both of the above, fused with Reciprocal Rank Fusion.

Every query is scoped by owner and document status inside the WHERE clause, so
results can only ever shrink to chunks the caller already owns — never leak
others'. One SQL statement per signal, ``select_related`` pulls the document
title in the same query: no N+1 anywhere.
"""

import functools
import re
from collections import defaultdict
from dataclasses import dataclass, replace

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db import connection, transaction
from pgvector import Vector
from pgvector.django import CosineDistance

from apps.common.ai.embeddings import get_embedding_provider
from apps.documents.models import Chunk, Document

VECTOR_MODE = "vector"
KEYWORD_MODE = "keyword"
HYBRID_MODE = "hybrid"
MODES = (VECTOR_MODE, KEYWORD_MODE, HYBRID_MODE)


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    document_id: int
    document_title: str
    chunk_index: int
    content: str
    score: float  # vector mode: cosine similarity in [-1, 1]; keyword mode: FTS rank >= 0
    match: str = VECTOR_MODE  # which signal matched: "vector", "keyword" or "both"
    page: int | None = None  # 1-based source page; None = chunk predates page tracking


@functools.lru_cache(maxsize=1)
def _pgvector_version() -> tuple[int, int]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        row = cursor.fetchone()
    version = row[0] if row else "0.0.0"
    match = re.match(r"(\d+)\.(\d+)", version)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _tune_hnsw_for_filtered_queries(cursor) -> None:
    """Relax HNSW so a WHERE filter can still return enough rows.

    HNSW walks the graph first and applies the SQL filter to the candidates
    afterwards, so selective filters can legitimately return fewer rows than
    requested. pgvector >= 0.8.0 fixes this with iterative scans; on older
    versions the only lever is a larger ef_search.
    """
    if _pgvector_version() >= (0, 8):
        cursor.execute("SET LOCAL hnsw.iterative_scan = %s", [settings.HNSW_ITERATIVE_SCAN])
        cursor.execute("SET LOCAL hnsw.max_scan_tuples = %s", [settings.HNSW_MAX_SCAN_TUPLES])
    else:
        cursor.execute("SET LOCAL hnsw.ef_search = %s", [settings.HNSW_EF_SEARCH])


def _base_filters(user, document_ids: list[int] | None) -> dict:
    """The WHERE clause every signal shares: only this owner's ready documents."""
    filters = {
        "document__owner": user,
        "document__status": Document.Status.READY,
    }
    if document_ids:
        filters["document_id__in"] = document_ids
    return filters


def retrieve(
    user,
    query: str,
    *,
    top_k: int | None = None,
    document_ids: list[int] | None = None,
    min_score: float | None = None,
    mode: str | None = None,
) -> list[RetrievedChunk]:
    """Return the top chunks of `user`'s ready documents for `query`.

    `mode` selects the signal; ``None`` falls back to settings.RETRIEVAL_MODE
    ("hybrid" by default for RAG answers). `min_score` only gates the fuzzy
    vector signal — a literal keyword hit is evidence on its own.
    """
    top_k = top_k if top_k is not None else settings.RETRIEVAL_TOP_K
    min_score = min_score if min_score is not None else settings.RETRIEVAL_MIN_SCORE
    mode = mode or settings.RETRIEVAL_MODE

    if mode == KEYWORD_MODE:
        return keyword_search(user, query, top_k=top_k, document_ids=document_ids)
    if mode == HYBRID_MODE:
        return hybrid_retrieve(
            user, query, top_k=top_k, document_ids=document_ids, min_score=min_score
        )
    if mode != VECTOR_MODE:
        raise ValueError(f"Unknown retrieval mode: {mode!r}")

    hits = _vector_search(user, query, top_k=top_k, document_ids=document_ids)
    return [hit for hit in hits if hit.score >= min_score]


def _vector_search(
    user, query: str, *, top_k: int, document_ids: list[int] | None
) -> list[RetrievedChunk]:
    """Cosine-similarity search over embeddings, best first (no score cutoff)."""
    query_vector = get_embedding_provider().embed_query(query)

    with transaction.atomic():
        with connection.cursor() as cursor:
            _tune_hnsw_for_filtered_queries(cursor)
        rows = (
            Chunk.objects.annotate(distance=CosineDistance("embedding", Vector(query_vector)))
            .filter(**_base_filters(user, document_ids))
            .order_by("distance")
            .select_related("document")[:top_k]
        )
        results = [
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                document_title=chunk.document.title,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                score=1.0 - chunk.distance,
                page=chunk.page,
            )
            for chunk in rows
        ]
    return results


def keyword_search(
    user,
    query: str,
    *,
    top_k: int | None = None,
    document_ids: list[int] | None = None,
) -> list[RetrievedChunk]:
    """Full-text search over the generated search vectors, best rank first.

    Uses websearch syntax, so users may quote phrases or exclude terms with
    minus signs. A query with no usable terms matches nothing (the vector
    signal still applies in hybrid mode).
    """
    top_k = top_k if top_k is not None else settings.RETRIEVAL_TOP_K
    tsquery = SearchQuery(query, search_type="websearch", config=settings.FTS_CONFIG)
    rows = (
        Chunk.objects.annotate(rank=SearchRank("search", tsquery))
        .filter(search=tsquery, **_base_filters(user, document_ids))
        .order_by("-rank", "document_id", "chunk_index")
        .select_related("document")[:top_k]
    )
    return [
        RetrievedChunk(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            document_title=chunk.document.title,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            score=chunk.rank,
            match=KEYWORD_MODE,
            page=chunk.page,
        )
        for chunk in rows
    ]


def hybrid_retrieve(
    user,
    query: str,
    *,
    top_k: int,
    document_ids: list[int] | None,
    min_score: float,
) -> list[RetrievedChunk]:
    """Fuse the vector and keyword signals with Reciprocal Rank Fusion.

    Each signal contributes its own candidate pool (larger than top_k so the
    fusion has room to work with); a chunk seen by both signals accumulates
    RRF weight from both rankings and surfaces on top.
    """
    pool = max(top_k * settings.HYBRID_CANDIDATE_MULTIPLIER, top_k)
    vector_hits = _vector_search(user, query, top_k=pool, document_ids=document_ids)
    vector_hits = [hit for hit in vector_hits if hit.score >= min_score]
    keyword_hits = keyword_search(user, query, top_k=pool, document_ids=document_ids)
    return _rrf_fuse([vector_hits, keyword_hits], top_k=top_k, k=settings.HYBRID_RRF_K)


def _rrf_fuse(rankings: list[list[RetrievedChunk]], *, top_k: int, k: int) -> list[RetrievedChunk]:
    """Reciprocal Rank Fusion over chunk ids; deterministic on ties (by id).

    score(chunk) = sum over rankings of 1 / (k + rank), with `k` damping the
    head ranks. Chunks found by both signals collect from both rankings, which
    is exactly the behaviour that makes hybrid retrieval beat each signal.
    """
    weights: dict[int, float] = defaultdict(float)
    best: dict[int, RetrievedChunk] = {}
    signals: dict[int, set[str]] = defaultdict(set)
    for ranking in rankings:
        for rank, chunk in enumerate(ranking, start=1):
            weights[chunk.chunk_id] += 1.0 / (k + rank)
            signals[chunk.chunk_id].add(chunk.match)
            best.setdefault(chunk.chunk_id, chunk)

    fused_ids = sorted(weights, key=lambda cid: (-weights[cid], cid))[:top_k]
    fused = []
    for chunk_id in fused_ids:
        hit = best[chunk_id]
        match = "both" if len(signals[chunk_id]) > 1 else next(iter(signals[chunk_id]))
        fused.append(replace(hit, score=round(weights[chunk_id], 6), match=match))
    return fused
