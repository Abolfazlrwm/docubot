"""Tests for structured citations: [n] marker resolution, pages in sources."""

import json

import pytest

from apps.chat.models import Conversation
from apps.chat.retrieval import RetrievedChunk
from apps.chat.services import (
    _build_user_prompt,
    _source_snapshot,
    ask_question,
    extract_citations,
    stream_answer,
)
from apps.common.ai.embeddings import FakeEmbeddingProvider
from apps.documents.models import Chunk, Document

pytestmark = pytest.mark.django_db


def make_ready_document(owner, title="Doc", chunks_data=None, page=1):
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
        Chunk.objects.create(
            document=document,
            chunk_index=i,
            page=page,
            content=content,
            embedding=provider.embed([content])[0],
        )
    return document


class TestExtractCitations:
    """The [n] resolver only surfaces markers backed by a real source."""

    def test_resolves_marker_to_its_source(self):
        sources = [{"document_title": "A"}, {"document_title": "B"}]
        cited = extract_citations("Sky is blue [2] and vast [1].", sources)
        assert [c["number"] for c in cited] == [2, 1]  # first-mention order
        assert cited[0]["document_title"] == "B"

    def test_deduplicates_repeated_markers(self):
        sources = [{"document_title": "A"}]
        cited = extract_citations("Seen here [1], again [1] and [1].", sources)
        assert len(cited) == 1
        assert cited[0]["number"] == 1

    def test_ignores_out_of_range_markers(self):
        sources = [{"document_title": "A"}]
        assert extract_citations("Hallucinated [4], zero [0], negative [-1].", sources) == []

    def test_no_markers_yields_empty(self):
        assert extract_citations("No citations in this answer.", [{"document_title": "A"}]) == []
        assert extract_citations("[1] but nothing else", []) == []

    def test_citation_carries_page_when_present(self):
        sources = [{"document_title": "A", "page": 7}]
        cited = extract_citations("The claim [1].", sources)
        assert cited[0]["page"] == 7


class TestSourceSnapshots:
    """Source snapshots expose the chunk's page for UI deep links."""

    def test_snapshot_includes_page(self):
        chunk = RetrievedChunk(
            chunk_id=1,
            document_id=2,
            document_title="Report",
            chunk_index=0,
            content="text",
            score=0.9,
            page=5,
        )
        assert _source_snapshot(chunk)["page"] == 5

    def test_prompt_block_names_the_page(self):
        chunk = RetrievedChunk(
            chunk_id=1,
            document_id=2,
            document_title="Report",
            chunk_index=0,
            content="text",
            score=0.9,
            page=5,
        )
        prompt = _build_user_prompt("why?", [chunk])
        assert "[1] (Report, p. 5) text" in prompt

    def test_prompt_block_without_page_stays_clean(self):
        chunk = RetrievedChunk(
            chunk_id=1,
            document_id=2,
            document_title="Report",
            chunk_index=0,
            content="text",
            score=0.9,
        )
        assert "[1] (Report) text" in _build_user_prompt("why?", [chunk])


class TestAskCitations:
    """End to end: a cited answer exposes resolved citations with pages."""

    def test_assistant_message_carries_citations_and_page(self, user):
        conversation = Conversation.objects.create(owner=user)
        make_ready_document(user, "Handbook", ["The quuxx protocol has three phases."], page=4)

        assistant = ask_question(user, conversation, "quuxx protocol phases")

        assert assistant.sources, "context should have been retrieved"
        assert assistant.sources[0]["page"] == 4
        # FakeLLMProvider answers "Based on the provided context [1]: ..." —
        # the citation resolver must map that back to source 1.
        assert assistant.sources[0]["document_title"] == "Handbook"

    def test_serializer_exposes_citations(self, auth_api, user):
        conversation = Conversation.objects.create(owner=user)
        make_ready_document(user, "Handbook", ["The quuxx protocol has three phases."], page=4)
        ask_question(user, conversation, "quuxx protocol phases")

        messages = conversation.messages.order_by("id")
        assistant = messages.filter(role="assistant").first()
        from apps.chat.serializers import MessageSerializer

        data = MessageSerializer(assistant).data
        assert data["citations"], "the [1] marker must resolve"
        assert data["citations"][0]["number"] == 1
        assert data["citations"][0]["page"] == 4
        assert data["citations"][0]["document_title"] == "Handbook"

    def test_user_messages_have_no_citations(self, user):
        conversation = Conversation.objects.create(owner=user)
        from apps.chat.serializers import MessageSerializer

        user_message = conversation.messages.create(role="user", content="hi")
        assert MessageSerializer(user_message).data["citations"] == []


class TestStreamCitations:
    """The SSE done event carries the resolved citations too."""

    def test_done_event_includes_citations(self, user):
        conversation = Conversation.objects.create(owner=user)
        make_ready_document(user, "Handbook", ["The quuxx protocol has three phases."], page=2)

        events = list(stream_answer(user, conversation, "quuxx protocol phases"))
        done = next(e for e in events if e.startswith("event: done"))
        payload = json.loads(done.split("data: ", 1)[1])

        assert payload["citations"], "done event must carry citations"
        assert payload["citations"][0]["number"] == 1
        assert payload["citations"][0]["page"] == 2
        # sources event and done event agree on the source list
        sources_event = next(e for e in events if e.startswith("event: sources"))
        sources_payload = json.loads(sources_event.split("data: ", 1)[1])
        assert payload["sources"] == sources_payload["sources"]
