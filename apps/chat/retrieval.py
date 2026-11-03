"""Owner-scoped vector retrieval over document chunks.

One SQL query does everything: cosine distance to the query vector, owner and
status filtering, ordering and limit — no N+1 (select_related pulls the
document title in the same query).
"""

import functools
import re
from dataclasses import dataclass

from django.conf import settings
from django.db import connection, transaction
from pgvector.django import CosineDistance
from pgvector import Vector

from apps.common.ai.embeddings import get_embedding_provider
from apps.documents.models import Chunk, Document


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: int
    document_id: int
    document_title: str
    chunk_index: int
    content: str
    score: float  # cosine similarity in [-1, 1]: 1 - cosine distance


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


def retrieve(
    user,
    query: str,
    *,
    top_k: int | None = None,
    document_ids: list[int] | None = None,
    min_score: float | None = None,
) -> list[RetrievedChunk]:
    """Return the top chunks of `user`'s ready documents by cosine similarity.

    Foreign document_ids are simply part of the WHERE clause, so they can only
    ever filter down to chunks the caller already owns — never leak others'.
    """
    top_k = top_k if top_k is not None else settings.RETRIEVAL_TOP_K
    min_score = min_score if min_score is not None else settings.RETRIEVAL_MIN_SCORE
    query_vector = get_embedding_provider().embed_query(query)

    filters = {
        "document__owner": user,
        "document__status": Document.Status.READY,
    }
    if document_ids:
        filters["document_id__in"] = document_ids

    with transaction.atomic():
        with connection.cursor() as cursor:
            _tune_hnsw_for_filtered_queries(cursor)
        rows = (
            Chunk.objects.annotate(distance=CosineDistance("embedding", Vector(query_vector)))
            .filter(**filters)
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
            )
            for chunk in rows
        ]
    return [result for result in results if result.score >= min_score]
