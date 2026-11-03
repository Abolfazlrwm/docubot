"""Tests for the search API endpoint."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status

from apps.common.ai.embeddings import FakeEmbeddingProvider
from apps.documents.models import Chunk, Document

User = get_user_model()

pytestmark = pytest.mark.django_db

SEARCH_URL = reverse("chat:search")


def make_ready_document(owner, title="Doc", chunks_data=None):
    """Create a READY document with embedded chunks."""
    if chunks_data is None:
        chunks_data = ["Default chunk content."]

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


class TestSearchAuthentication:
    """Search endpoint requires authentication."""

    def test_unauthenticated_request_rejected(self, api):
        response = api.post(SEARCH_URL, {"query": "test"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_authenticated_request_allowed(self, auth_api, user):
        make_ready_document(user, "Doc", ["searchable content"])
        response = auth_api.post(SEARCH_URL, {"query": "searchable"})
        assert response.status_code == status.HTTP_200_OK


class TestSearchValidation:
    """Request validation for the search endpoint."""

    def test_empty_query_rejected(self, auth_api):
        response = auth_api.post(SEARCH_URL, {"query": ""})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_query_rejected(self, auth_api):
        response = auth_api.post(SEARCH_URL, {})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_query_too_long_rejected(self, auth_api, settings):
        settings.QUERY_MAX_LENGTH = 10
        response = auth_api.post(SEARCH_URL, {"query": "x" * 11})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_top_k_min_value(self, auth_api):
        response = auth_api.post(SEARCH_URL, {"query": "test", "top_k": 0})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_top_k_max_value(self, auth_api, settings):
        settings.RETRIEVAL_MAX_TOP_K = 50
        response = auth_api.post(SEARCH_URL, {"query": "test", "top_k": 51})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_document_ids_accepts_empty_list(self, auth_api, user):
        make_ready_document(user, "Doc", ["content"])
        response = auth_api.post(SEARCH_URL, {"query": "content", "document_ids": []})
        assert response.status_code == status.HTTP_200_OK

    def test_document_ids_accepts_valid_ids(self, auth_api, user):
        doc = make_ready_document(user, "Doc", ["content"])
        response = auth_api.post(SEARCH_URL, {"query": "content", "document_ids": [doc.id]})
        assert response.status_code == status.HTTP_200_OK


class TestSearchResultsShape:
    """Response structure and content."""

    def test_returns_list_of_results(self, auth_api, user):
        make_ready_document(user, "Doc", ["first content", "second content"])
        response = auth_api.post(SEARCH_URL, {"query": "content"})

        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.data, list)
        assert len(response.data) == 2

    def test_result_has_all_required_fields(self, auth_api, user):
        make_ready_document(user, "My Document", ["chunk text"])
        response = auth_api.post(SEARCH_URL, {"query": "chunk"})

        result = response.data[0]
        assert "document_id" in result
        assert "document_title" in result
        assert "chunk_index" in result
        assert "content" in result
        assert "score" in result

    def test_score_is_float_between_negative_one_and_one(self, auth_api, user):
        make_ready_document(user, "Doc", ["content"])
        response = auth_api.post(SEARCH_URL, {"query": "content"})

        score = response.data[0]["score"]
        assert isinstance(score, float)
        assert -1.0 <= score <= 1.0

    def test_results_ordered_by_score_descending(self, auth_api, user):
        make_ready_document(user, "Doc", ["apple banana", "orange pear"])
        response = auth_api.post(SEARCH_URL, {"query": "apple"})

        scores = [r["score"] for r in response.data]
        assert scores == sorted(scores, reverse=True)

    def test_document_title_in_result(self, auth_api, user):
        make_ready_document(user, "Specific Title", ["content"])
        response = auth_api.post(SEARCH_URL, {"query": "content"})

        assert response.data[0]["document_title"] == "Specific Title"

    def test_chunk_index_in_result(self, auth_api, user):
        make_ready_document(user, "Doc", ["first", "second", "third"])
        response = auth_api.post(SEARCH_URL, {"query": "second"})

        assert response.data[0]["chunk_index"] == 1


class TestSearchOwnerIsolation:
    """Users can only search their own documents."""

    def test_only_returns_own_documents(self, auth_api, user):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="pass"
        )
        make_ready_document(user, "My Doc", ["my secret"])
        make_ready_document(other, "Other Doc", ["my secret"])

        response = auth_api.post(SEARCH_URL, {"query": "secret"})

        assert len(response.data) == 1
        assert response.data[0]["document_title"] == "My Doc"

    def test_document_ids_filter_respects_ownership(self, auth_api, user):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="pass"
        )
        make_ready_document(user, "My Doc", ["content"])
        other_doc = make_ready_document(other, "Other Doc", ["content"])

        # Try to request other user's document ID
        response = auth_api.post(SEARCH_URL, {"query": "content", "document_ids": [other_doc.id]})

        assert len(response.data) == 0


class TestSearchTopK:
    """top_k parameter limits results."""

    def test_top_k_limits_results(self, auth_api, user):
        make_ready_document(user, "Doc", [f"chunk {i}" for i in range(10)])
        response = auth_api.post(SEARCH_URL, {"query": "chunk", "top_k": 3})

        assert len(response.data) == 3

    def test_defaults_to_settings(self, auth_api, user, settings):
        settings.RETRIEVAL_TOP_K = 2
        make_ready_document(user, "Doc", ["a b", "a c", "a d"])
        response = auth_api.post(SEARCH_URL, {"query": "a"})

        assert len(response.data) == 2


class TestSearchDocumentIdsFilter:
    """document_ids parameter filters results."""

    def test_filters_to_specified_documents(self, auth_api, user):
        doc1 = make_ready_document(user, "Doc 1", ["apple"])
        doc2 = make_ready_document(user, "Doc 2", ["apple"])
        make_ready_document(user, "Doc 3", ["apple"])

        response = auth_api.post(SEARCH_URL, {"query": "apple", "document_ids": [doc1.id, doc2.id]})

        assert len(response.data) == 2
        returned_ids = {r["document_id"] for r in response.data}
        assert returned_ids == {doc1.id, doc2.id}

    def test_empty_list_returns_all(self, auth_api, user):
        make_ready_document(user, "Doc 1", ["apple"])
        make_ready_document(user, "Doc 2", ["apple"])
        response = auth_api.post(SEARCH_URL, {"query": "apple", "document_ids": []})

        assert len(response.data) == 2


class TestSearchMinScore:
    """min_score filters low-similarity results."""

    def test_min_score_filters_results(self, auth_api, user, settings):
        settings.RETRIEVAL_MIN_SCORE = 0.5
        make_ready_document(
            user, "Doc", ["relevant content", "unrelated text about something else"]
        )
        response = auth_api.post(SEARCH_URL, {"query": "relevant"})

        # Should only return the highly relevant chunk
        assert len(response.data) == 1
        assert "relevant" in response.data[0]["content"]


class TestSearchEmptyResults:
    """Edge cases with no results."""

    def test_no_matching_documents_returns_empty_list(self, auth_api, user):
        make_ready_document(user, "Doc", ["completely different"])
        response = auth_api.post(SEARCH_URL, {"query": "no match"})

        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_no_documents_returns_empty_list(self, auth_api):
        response = auth_api.post(SEARCH_URL, {"query": "anything"})

        assert response.status_code == status.HTTP_200_OK
        assert response.data == []
