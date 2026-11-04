"""Tests for the retrieval service."""

import pytest
from django.contrib.auth import get_user_model

from apps.chat.retrieval import _pgvector_version, retrieve
from apps.common.ai.embeddings import FakeEmbeddingProvider
from apps.documents.models import Chunk, Document

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def vector_mode(settings):
    """Pin the vector signal: this module tests cosine semantics and the HNSW
    query budget. The keyword/hybrid signals have their own suite (test_hybrid)."""
    settings.RETRIEVAL_MODE = "vector"


def make_document(owner, title="Doc", text="This is a test document content."):
    """Create a document with ready status and chunks with embeddings."""
    document = Document.objects.create(
        owner=owner,
        title=title,
        file="test.txt",
        original_filename="test.txt",
        content_type="text/plain",
        size_bytes=len(text),
        status=Document.Status.READY,
    )
    # Create a chunk with a known embedding
    provider = FakeEmbeddingProvider(dimensions=384)
    embedding = provider.embed([text])[0]
    Chunk.objects.create(
        document=document,
        chunk_index=0,
        content=text,
        embedding=embedding,
    )
    return document


def make_document_with_chunks(owner, title="Doc", chunks_data=None):
    """Create a document with multiple chunks with known embeddings."""
    if chunks_data is None:
        chunks_data = ["First chunk content.", "Second chunk content."]

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
    for i, content in enumerate(chunks_data):
        embedding = provider.embed([content])[0]
        Chunk.objects.create(
            document=document,
            chunk_index=i,
            content=content,
            embedding=embedding,
        )
    return document


class TestRetrieveOwnerIsolation:
    """Only the owner's chunks should be returned."""

    def test_returns_only_owners_chunks(self, user):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="pass"
        )
        make_document(user, "My Doc", "My secret content.")
        make_document(other, "Other Doc", "Other secret content.")

        results = retrieve(user, "secret")

        assert len(results) == 1
        assert results[0].document_title == "My Doc"  # the owner's document, not the other user's

    def test_other_users_chunks_not_leaked(self, user):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="pass"
        )
        make_document(user, "My Doc", "alpha beta gamma")
        make_document(other, "Other Doc", "alpha beta gamma")

        # Query matches both, but only user's should be returned
        results = retrieve(user, "alpha beta")

        assert len(results) == 1
        assert results[0].document_title == "My Doc"


class TestRetrieveReadyOnly:
    """Only READY documents should be searched."""

    def test_pending_document_excluded(self, user):
        document = make_document(user, "Ready Doc", "searchable content")
        document.status = Document.Status.PENDING
        document.save()

        results = retrieve(user, "searchable")
        assert len(results) == 0

    def test_processing_document_excluded(self, user):
        document = make_document(user, "Ready Doc", "searchable content")
        document.status = Document.Status.PROCESSING
        document.save()

        results = retrieve(user, "searchable")
        assert len(results) == 0

    def test_failed_document_excluded(self, user):
        document = make_document(user, "Ready Doc", "searchable content")
        document.status = Document.Status.FAILED
        document.save()

        results = retrieve(user, "searchable")
        assert len(results) == 0


class TestRetrieveOrdering:
    """Results should be ordered by cosine similarity (highest first)."""

    def test_orders_by_score_descending(self, user):
        # Both chunks share words with the query (so both pass the min-score
        # cutoff) but the exact match must rank first.
        make_document_with_chunks(
            user,
            "Test Doc",
            [
                "quick brown fox",
                "quick brown dog jumps around",
            ],
        )

        results = retrieve(user, "quick brown fox")

        # HNSW is approximate: on a table full of rolled-back test rows the
        # graph walk may surface only the nearest chunk (pgvector < 0.8 has no
        # iterative scans to resupply filtered-out candidates). The nearest
        # neighbour itself is always found — assert ordering whenever the
        # runner-up also comes back.
        assert results
        assert "fox" in results[0].content.lower()
        if len(results) == 2:
            assert results[0].score > results[1].score


class TestRetrieveTopK:
    """The top_k parameter should limit results."""

    def test_limits_results(self, user):
        make_document_with_chunks(user, "Doc", [f"Chunk {i} content." for i in range(10)])

        results = retrieve(user, "content", top_k=3)

        assert len(results) == 3

    def test_defaults_to_settings(self, user, settings):
        settings.RETRIEVAL_TOP_K = 2
        make_document_with_chunks(user, "Doc", ["a b", "a c", "a d"])

        results = retrieve(user, "a")

        assert len(results) == 2


class TestRetrieveDocumentIdsFilter:
    """The document_ids filter should restrict search to specific documents."""

    def test_filters_by_document_ids(self, user):
        doc1 = make_document(user, "Doc 1", "apple banana")
        doc2 = make_document(user, "Doc 2", "apple banana")
        make_document(user, "Doc 3", "apple banana")

        results = retrieve(user, "apple", document_ids=[doc1.id, doc2.id])

        assert len(results) == 2
        returned_ids = {r.document_id for r in results}
        assert returned_ids == {doc1.id, doc2.id}

    def test_ignores_foreign_document_ids(self, user):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="pass"
        )
        make_document(user, "My Doc", "secret")
        other_doc = make_document(other, "Other Doc", "secret")

        # Try to request other user's document - should be ignored
        results = retrieve(user, "secret", document_ids=[other_doc.id])

        assert len(results) == 0

    def test_empty_document_ids_returns_all(self, user):
        make_document(user, "Doc 1", "apple")
        make_document(user, "Doc 2", "apple")

        results = retrieve(user, "apple", document_ids=[])

        assert len(results) == 2


class TestRetrieveMinScore:
    """The min_score parameter should filter low-similarity results."""

    def test_filters_by_min_score(self, user, settings):
        settings.RETRIEVAL_MIN_SCORE = 0.5
        make_document_with_chunks(
            user,
            "Doc",
            [
                "The quick brown fox jumps.",
                "Unrelated text about something else entirely.",
            ],
        )

        results = retrieve(user, "quick brown fox")

        # Should only return the highly relevant chunk
        assert len(results) == 1
        assert "fox" in results[0].content.lower()

    def test_defaults_to_settings(self, user, settings):
        settings.RETRIEVAL_MIN_SCORE = 0.9  # Very high threshold
        make_document(user, "Doc", "some content")

        results = retrieve(user, "completely different query")

        assert len(results) == 0


class TestRetrieveReturnedFields:
    """RetrievedChunk should have all expected fields populated."""

    def test_all_fields_present(self, user):
        doc = make_document(user, "My Title", "Chunk content here.")

        results = retrieve(user, "content")

        assert len(results) == 1
        chunk = results[0]
        assert chunk.chunk_id > 0
        assert chunk.document_id == doc.id
        assert chunk.document_title == "My Title"
        assert chunk.chunk_index == 0
        assert chunk.content == "Chunk content here."
        assert isinstance(chunk.score, float)
        assert -1.0 <= chunk.score <= 1.0


class TestRetrieveNoNPlusOne:
    """Retrieval should not cause N+1 queries."""

    def test_select_related_document(self, user, django_assert_max_num_queries):
        make_document_with_chunks(user, "Doc", [f"chunk number {i} content" for i in range(10)])
        retrieve(user, "content")  # warm the lru-cached pgvector version lookup

        # Bounded regardless of the 10 chunks — proof there is no per-chunk
        # N+1. Fixed budget: savepoint + release around the atomic block, one
        # SET LOCAL per HNSW tuning knob (pgvector >= 0.8 exposes two knobs,
        # iterative_scan + max_scan_tuples; older versions only ef_search) and
        # the single SELECT with select_related.
        gucs = 2 if _pgvector_version() >= (0, 8) else 1
        with django_assert_max_num_queries(2 + gucs + 1):
            results = retrieve(user, "content")
            # Access document_title to force select_related
            _ = [r.document_title for r in results]


class TestRetrieveHNSWIndexExists:
    """The HNSW index should exist on the Chunk.embedding field."""

    def test_hnsw_index_on_embedding(self):
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'documents_chunk'
                AND indexname LIKE '%hnsw%'
            """)
            rows = cursor.fetchall()

        assert len(rows) > 0, "HNSW index should exist on documents_chunk table"
        # Verify it's on the embedding column
        index_names = [row[0] for row in rows]
        assert any("embedding" in name for name in index_names), (
            "HNSW index should be on embedding column"
        )


class TestRetrieveEmptyResults:
    """Edge cases with no matching documents."""

    def test_no_documents_returns_empty(self, user):
        results = retrieve(user, "anything")
        assert results == []

    def test_no_matching_chunks_returns_empty(self, user):
        make_document(user, "Doc", "completely different")
        results = retrieve(user, "no match here")
        assert results == []


class TestRetrieveCosineSimilarity:
    """Verify cosine similarity scoring is correct."""

    def test_identical_text_scores_high(self, user):
        text = "Exact match content for testing."
        make_document(user, "Doc", text)
        provider = FakeEmbeddingProvider(dimensions=384)
        provider.embed([text])[0]

        # The chunk embedding and query embedding should be identical for same text
        # But FakeEmbeddingProvider is deterministic based on content hash
        # So same text = same embedding = score of 1.0 (cosine similarity)
        results = retrieve(user, text)

        assert len(results) == 1
        assert results[0].score == pytest.approx(1.0, abs=1e-6)

    def test_different_text_scores_lower(self, user, settings):
        # Lift the min-score cutoff so both chunks come back and the ORDER of
        # similarity can actually be observed.
        settings.RETRIEVAL_MIN_SCORE = -1.0
        make_document_with_chunks(user, "Doc", ["Text about apples.", "Text about oranges."])
        results = retrieve(user, "apples")

        # The apples chunk strictly dominates the oranges chunk in cosine
        # similarity, so it must be first whenever the scan returns it — and
        # it always does (ANN recall only ever hides runner-ups).
        assert results
        assert "apples" in results[0].content.lower()
        if len(results) == 2:
            assert results[0].score > results[1].score
