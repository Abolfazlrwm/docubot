"""Tests for keyword (FTS) and hybrid retrieval, and the search mode API."""

import pytest
from django.urls import reverse
from rest_framework import status

from apps.chat.retrieval import RetrievedChunk, _rrf_fuse, hybrid_retrieve, keyword_search
from tests.test_search import make_ready_document

pytestmark = pytest.mark.django_db

SEARCH_URL = reverse("chat:search")


def chunk(i, title="Doc", content="content", score=0.9, match="vector"):
    return RetrievedChunk(
        chunk_id=i,
        document_id=100 + i,
        document_title=title,
        chunk_index=0,
        content=content,
        score=score,
        match=match,
    )


class TestKeywordSearch:
    """The FTS signal finds literal terms the vector signal can miss."""

    def test_finds_exact_keyword(self, user):
        make_ready_document(user, "Contract", ["The termination clause is 14.3.2b."])
        hits = keyword_search(user, "14.3.2b")
        assert len(hits) == 1
        assert "14.3.2b" in hits[0].content
        assert hits[0].match == "keyword"

    def test_ranks_multi_term_matches_first(self, user):
        make_ready_document(user, "Doc", ["apple banana cherry", "apple dog elephant"])
        hits = keyword_search(user, "apple banana")
        assert hits[0].content == "apple banana cherry"

    def test_respects_owner_isolation(self, user, django_user_model):
        make_ready_document(user, "Mine", ["confidential keyword"])
        other = django_user_model.objects.create_user(username="o", password="x")
        make_ready_document(other, "Theirs", ["confidential keyword"])
        hits = keyword_search(user, "confidential")
        assert [h.document_title for h in hits] == ["Mine"]

    def test_respects_document_ids_filter(self, user):
        doc1 = make_ready_document(user, "Doc 1", ["orange juice"])
        make_ready_document(user, "Doc 2", ["orange juice"])
        hits = keyword_search(user, "orange", document_ids=[doc1.id])
        assert [h.document_id for h in hits] == [doc1.id]

    def test_ignores_non_ready_documents(self, user):
        document = make_ready_document(user, "Draft", ["quantum flux capacitor"])
        document.status = "processing"
        document.save(update_fields=["status"])
        assert keyword_search(user, "quantum") == []

    def test_no_match_returns_empty(self, user):
        make_ready_document(user, "Doc", ["ordinary text"])
        assert keyword_search(user, "zzzqqq") == []

    def test_top_k_limits_results(self, user, settings):
        settings.RETRIEVAL_TOP_K = 2
        make_ready_document(user, "Doc", [f"shared term {i}" for i in range(5)])
        assert len(keyword_search(user, "shared term")) == 2

    def test_quoted_phrase_excludes_terms(self, user):
        make_ready_document(user, "Doc", ["apple pie recipe", "apple crumble recipe"])
        hits = keyword_search(user, 'apple -"apple pie"')
        assert [h.content for h in hits] == ["apple crumble recipe"]


class TestReciprocalRankFusion:
    """Pure fusion logic: deterministic, both-signals boost, top_k respected."""

    def test_both_signals_float_to_the_top(self):
        fused = _rrf_fuse(
            [
                [chunk(1), chunk(2), chunk(3)],
                [chunk(2, match="keyword"), chunk(4, match="keyword")],
            ],
            top_k=5,
            k=60,
        )
        assert fused[0].chunk_id == 2  # seen by both rankings
        assert fused[0].match == "both"
        assert fused[1].chunk_id == 1  # rank 1 of the first ranking

    def test_fusion_is_deterministic_on_ties(self):
        left = _rrf_fuse([[chunk(7), chunk(9)], []], top_k=5, k=60)
        right = _rrf_fuse([[chunk(7), chunk(9)], []], top_k=5, k=60)
        assert [c.chunk_id for c in left] == [c.chunk_id for c in right]

    def test_top_k_truncates(self):
        ranking = [chunk(i) for i in range(10)]
        assert len(_rrf_fuse([ranking, []], top_k=3, k=60)) == 3

    def test_fused_score_is_sum_of_reciprocal_ranks(self):
        fused = _rrf_fuse([[chunk(1), chunk(2)], [chunk(1, match="keyword")]], top_k=5, k=60)
        expected = round(1 / 61 + 1 / 61, 6)  # rank 1 in both rankings; API scores are rounded
        assert fused[0].chunk_id == 1
        assert fused[0].score == pytest.approx(expected, abs=1e-9)

    def test_single_signal_result_keeps_its_origin(self):
        fused = _rrf_fuse(
            [[chunk(1)], [chunk(2, match="keyword"), chunk(3, match="keyword")]], top_k=3, k=60
        )
        assert fused[0].match == "vector"
        assert fused[1].match == "keyword"


class TestHybridRetrieve:
    """The hybrid path combines pools and honours min_score for vector hits."""

    def test_keyword_hit_survives_low_vector_score(self, user, settings):
        settings.RETRIEVAL_MIN_SCORE = 0.99  # vector signal filtered out entirely
        make_ready_document(user, "Doc", ["the qwertyxiom protocol"])
        hits = hybrid_retrieve(user, "qwertyxiom", top_k=5, document_ids=None, min_score=0.99)
        assert len(hits) == 1
        assert hits[0].match == "keyword"

    def test_owner_isolation(self, user, django_user_model):
        make_ready_document(user, "Mine", ["hybrid pie recipe"])
        other = django_user_model.objects.create_user(username="o2", password="x")
        make_ready_document(other, "Theirs", ["hybrid pie recipe"])
        hits = hybrid_retrieve(user, "pie recipe", top_k=5, document_ids=None, min_score=0.0)
        assert [h.document_title for h in hits] == ["Mine"]

    def test_no_hits_at_all(self, user):
        assert (
            hybrid_retrieve(user, "nothing matches this", top_k=5, document_ids=None, min_score=0.9)
            == []
        )


class TestSearchModeApi:
    """The search endpoint exposes mode=vector|keyword|hybrid."""

    def test_keyword_mode_finds_exact_term(self, auth_api, user):
        make_ready_document(user, "Doc", ["clause 14.3.2b governs termination"])
        response = auth_api.post(SEARCH_URL, {"query": "14.3.2b", "mode": "keyword"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1
        assert response.data[0]["match"] == "keyword"

    def test_invalid_mode_rejected(self, auth_api):
        response = auth_api.post(SEARCH_URL, {"query": "x", "mode": "magic"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_default_mode_is_settings_controlled(self, auth_api, user, settings):
        settings.SEARCH_MODE = "keyword"
        make_ready_document(user, "Doc", ["clause 14.3.2b governs termination"])
        response = auth_api.post(SEARCH_URL, {"query": "14.3.2b"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 1

    def test_vector_mode_still_reports_match_vector(self, auth_api, user):
        make_ready_document(user, "Doc", ["nearest neighbour text"])
        response = auth_api.post(SEARCH_URL, {"query": "neighbour", "mode": "vector"})
        assert response.status_code == status.HTTP_200_OK
        assert response.data[0]["match"] == "vector"

    def test_hybrid_mode_returns_results(self, auth_api, user):
        make_ready_document(user, "Doc", ["pineapple segmentation fault"])
        response = auth_api.post(SEARCH_URL, {"query": "pineapple", "mode": "hybrid"})
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) >= 1


class TestUnknownModeValue:
    """retrieve() rejects bogus modes loudly (programmer error, not a 500)."""

    def test_retrieve_raises_on_unknown_mode(self, user):
        from apps.chat import retrieval

        with pytest.raises(ValueError, match="Unknown retrieval mode"):
            retrieval.retrieve(user, "query", mode="fuzzy")
