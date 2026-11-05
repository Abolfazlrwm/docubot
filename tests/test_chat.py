"""Tests for Conversation and Message APIs."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status

from apps.chat.models import Conversation, Message
from apps.common.ai.embeddings import FakeEmbeddingProvider
from apps.documents.models import Chunk, Document

User = get_user_model()

pytestmark = pytest.mark.django_db

CONVERSATIONS_URL = reverse("chat:conversation-list")


def make_ready_document(owner, title="Doc", chunks_data=None):
    if chunks_data is None:
        chunks_data = ["Default content."]
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
        Chunk.objects.create(document=document, chunk_index=i, content=content, embedding=embedding)
    return document


def conversation_detail_url(pk):
    return reverse("chat:conversation-detail", args=[pk])


def conversation_messages_url(conversation_pk):
    return reverse("chat:conversation-messages", args=[conversation_pk])


class TestConversationList:
    """GET /api/v1/chat/conversations/"""

    def test_requires_authentication(self, api):
        response = api.get(CONVERSATIONS_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_lists_own_conversations(self, auth_api, user):
        Conversation.objects.create(owner=user, title="First")
        Conversation.objects.create(owner=user, title="Second")
        other = User.objects.create_user(
            username="other", email="other@example.com", password="pass"
        )
        Conversation.objects.create(owner=other, title="Not mine")

        response = auth_api.get(CONVERSATIONS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 2
        titles = {c["title"] for c in response.data["results"]}
        assert titles == {"First", "Second"}

    def test_includes_message_count(self, auth_api, user):
        conv = Conversation.objects.create(owner=user, title="With messages")
        Message.objects.create(conversation=conv, role=Message.Role.USER, content="Q1")
        Message.objects.create(conversation=conv, role=Message.Role.ASSISTANT, content="A1")
        Message.objects.create(conversation=conv, role=Message.Role.USER, content="Q2")

        response = auth_api.get(CONVERSATIONS_URL)

        conv_data = next(c for c in response.data["results"] if c["title"] == "With messages")
        assert conv_data["message_count"] == 3

    def test_ordered_by_updated_at_desc(self, auth_api, user):
        c1 = Conversation.objects.create(owner=user, title="Older")
        Conversation.objects.create(owner=user, title="Newer")
        # Touch c1 to update its updated_at
        c1.save()

        response = auth_api.get(CONVERSATIONS_URL)

        assert response.data["results"][0]["title"] == "Older"
        assert response.data["results"][1]["title"] == "Newer"

    def test_pagination(self, auth_api, user):
        for i in range(25):
            Conversation.objects.create(owner=user, title=f"Conv {i}")

        response = auth_api.get(CONVERSATIONS_URL)

        assert response.status_code == status.HTTP_200_OK
        assert "results" in response.data
        assert len(response.data["results"]) <= 20  # default page size


class TestConversationCreate:
    """POST /api/v1/chat/conversations/"""

    def test_requires_authentication(self, api):
        response = api.post(CONVERSATIONS_URL, {"title": "New"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_creates_conversation(self, auth_api, user):
        response = auth_api.post(CONVERSATIONS_URL, {"title": "My Chat"})

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["title"] == "My Chat"
        assert Conversation.objects.filter(owner=user, title="My Chat").exists()

    def test_title_optional(self, auth_api, user):
        response = auth_api.post(CONVERSATIONS_URL, {})

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["title"] == ""

    def test_owner_set_to_request_user(self, auth_api, user):
        response = auth_api.post(CONVERSATIONS_URL, {"title": "Test"})

        conv = Conversation.objects.get(pk=response.data["id"])
        assert conv.owner == user


class TestConversationRetrieve:
    """GET /api/v1/chat/conversations/{id}/"""

    def test_requires_authentication(self, api, user):
        conv = Conversation.objects.create(owner=user)
        response = api.get(conversation_detail_url(conv.pk))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_returns_own_conversation(self, auth_api, user):
        conv = Conversation.objects.create(owner=user, title="My Conv")
        response = auth_api.get(conversation_detail_url(conv.pk))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["title"] == "My Conv"

    def test_other_users_conversation_returns_404(self, auth_api, user):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="pass"
        )
        conv = Conversation.objects.create(owner=other, title="Not mine")
        response = auth_api.get(conversation_detail_url(conv.pk))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_nonexistent_conversation_returns_404(self, auth_api):
        response = auth_api.get(conversation_detail_url(99999))
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestConversationDestroy:
    """DELETE /api/v1/chat/conversations/{id}/"""

    def test_requires_authentication(self, api, user):
        conv = Conversation.objects.create(owner=user)
        response = api.delete(conversation_detail_url(conv.pk))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_deletes_own_conversation(self, auth_api, user):
        conv = Conversation.objects.create(owner=user)
        response = auth_api.delete(conversation_detail_url(conv.pk))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Conversation.objects.filter(pk=conv.pk).exists()

    def test_other_users_conversation_returns_404(self, auth_api, user):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="pass"
        )
        conv = Conversation.objects.create(owner=other)
        response = auth_api.delete(conversation_detail_url(conv.pk))

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert Conversation.objects.filter(pk=conv.pk).exists()

    def test_cascade_deletes_messages(self, auth_api, user):
        conv = Conversation.objects.create(owner=user)
        Message.objects.create(conversation=conv, role=Message.Role.USER, content="Q")
        Message.objects.create(conversation=conv, role=Message.Role.ASSISTANT, content="A")

        auth_api.delete(conversation_detail_url(conv.pk))

        assert Message.objects.filter(conversation=conv).count() == 0


class TestMessageList:
    """GET /api/v1/chat/conversations/{id}/messages/"""

    def test_requires_authentication(self, api, user):
        conv = Conversation.objects.create(owner=user)
        response = api.get(conversation_messages_url(conv.pk))
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_lists_messages_chronological(self, auth_api, user):
        conv = Conversation.objects.create(owner=user)
        Message.objects.create(conversation=conv, role=Message.Role.USER, content="First")
        Message.objects.create(conversation=conv, role=Message.Role.ASSISTANT, content="Second")
        Message.objects.create(conversation=conv, role=Message.Role.USER, content="Third")

        response = auth_api.get(conversation_messages_url(conv.pk))

        assert response.status_code == status.HTTP_200_OK
        contents = [m["content"] for m in response.data["results"]]
        assert contents == ["First", "Second", "Third"]

    def test_other_users_conversation_returns_404(self, auth_api, user):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="pass"
        )
        conv = Conversation.objects.create(owner=other)
        response = auth_api.get(conversation_messages_url(conv.pk))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_pagination(self, auth_api, user):
        conv = Conversation.objects.create(owner=user)
        for i in range(25):
            Message.objects.create(conversation=conv, role=Message.Role.USER, content=f"Msg {i}")

        response = auth_api.get(conversation_messages_url(conv.pk))

        assert len(response.data["results"]) <= 20

    def test_no_n_plus_one_for_sources(self, auth_api, user, django_assert_num_queries):
        """Message list should not N+1 on sources (JSON field, no relation)."""
        conv = Conversation.objects.create(owner=user)
        for i in range(5):
            Message.objects.create(
                conversation=conv,
                role=Message.Role.ASSISTANT,
                content=f"Answer {i}",
                sources=[{"document_id": 1, "snippet": "x"}],
            )

        # 4 queries: JWT user, conversation fetch, pagination COUNT, message list —
        # independent of the 5 messages stored, so sources can never N+1.
        with django_assert_num_queries(4):
            response = auth_api.get(conversation_messages_url(conv.pk))
            _ = response.data["results"]


class TestMessageCreate:
    """POST /api/v1/chat/conversations/{id}/messages/ - Ask a question."""

    def test_requires_authentication(self, api, user):
        conv = Conversation.objects.create(owner=user)
        response = api.post(conversation_messages_url(conv.pk), {"content": "Hello"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_other_users_conversation_returns_404(self, auth_api, user):
        other = User.objects.create_user(
            username="other", email="other@example.com", password="pass"
        )
        conv = Conversation.objects.create(owner=other)
        response = auth_api.post(conversation_messages_url(conv.pk), {"content": "Hello"})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_creates_user_and_assistant_messages(self, auth_api, user):
        conv = Conversation.objects.create(owner=user)
        make_ready_document(user, "Doc", ["content about apples"])

        response = auth_api.post(
            conversation_messages_url(conv.pk), {"content": "What about apples?"}
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["role"] == Message.Role.ASSISTANT
        # Verify both messages created
        messages = list(conv.messages.order_by("created_at"))
        assert len(messages) == 2
        assert messages[0].role == Message.Role.USER
        assert messages[1].role == Message.Role.ASSISTANT

    def test_returns_assistant_message_with_sources(self, auth_api, user):
        conv = Conversation.objects.create(owner=user)
        doc = make_ready_document(user, "Doc", ["The answer is 42."])

        response = auth_api.post(
            conversation_messages_url(conv.pk), {"content": "What is the answer?"}
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert "sources" in response.data
        assert len(response.data["sources"]) > 0
        assert response.data["sources"][0]["document_id"] == doc.id

    def test_returns_model_and_token_counts(self, auth_api, user):
        conv = Conversation.objects.create(owner=user)
        make_ready_document(user, "Doc", ["content about apples"])

        response = auth_api.post(
            conversation_messages_url(conv.pk), {"content": "What about apples?"}
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["model"]
        assert response.data["prompt_tokens"] is not None
        assert response.data["completion_tokens"] is not None

    def test_validates_content_required(self, auth_api, user):
        conv = Conversation.objects.create(owner=user)
        response = auth_api.post(conversation_messages_url(conv.pk), {})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_validates_content_max_length(self, auth_api, user):
        conv = Conversation.objects.create(owner=user)
        response = auth_api.post(conversation_messages_url(conv.pk), {"content": "x" * 2001})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_document_ids_filter(self, auth_api, user):
        conv = Conversation.objects.create(owner=user)
        doc1 = make_ready_document(user, "Doc 1", ["apple"])
        make_ready_document(user, "Doc 2", ["banana"])

        response = auth_api.post(
            conversation_messages_url(conv.pk), {"content": "fruit", "document_ids": [doc1.id]}
        )

        assert response.status_code == status.HTTP_201_CREATED
        # Only doc1 should be searched
        if response.data["sources"]:
            assert all(s["document_id"] == doc1.id for s in response.data["sources"])

    def test_llm_transient_error_returns_503(self, auth_api, user, monkeypatch):
        conv = Conversation.objects.create(owner=user)
        make_ready_document(user, "Doc", ["content about apples"])

        class TransientProvider(FakeEmbeddingProvider):
            pass

        from apps.common.ai.llm import FakeLLMProvider, LLMTransientError

        class FailingProvider(FakeLLMProvider):
            def generate(self, system, messages):
                raise LLMTransientError("Temporarily unavailable")

        import apps.chat.services as chat_services

        monkeypatch.setattr(chat_services, "get_llm_provider", lambda: FailingProvider())

        response = auth_api.post(
            conversation_messages_url(conv.pk), {"content": "What about apples?"}
        )

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "temporarily unavailable" in response.data["detail"].lower()

    def test_llm_permanent_error_returns_502(self, auth_api, user, monkeypatch):
        conv = Conversation.objects.create(owner=user)
        make_ready_document(user, "Doc", ["content about apples"])

        from apps.common.ai.llm import FakeLLMProvider, LLMPermanentError

        class FailingProvider(FakeLLMProvider):
            def generate(self, system, messages):
                raise LLMPermanentError("Bad request")

        import apps.chat.services as chat_services

        monkeypatch.setattr(chat_services, "get_llm_provider", lambda: FailingProvider())

        response = auth_api.post(
            conversation_messages_url(conv.pk), {"content": "What about apples?"}
        )

        assert response.status_code == status.HTTP_502_BAD_GATEWAY
        assert "rejected" in response.data["detail"].lower()

    def test_no_context_returns_no_context_answer(self, auth_api, user):
        conv = Conversation.objects.create(owner=user)
        make_ready_document(user, "Doc", ["unrelated content"])

        response = auth_api.post(
            conversation_messages_url(conv.pk), {"content": "completely different"}
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert (
            response.data["content"] == "I could not find relevant information in your documents."
        )
        assert response.data["sources"] == []
        assert response.data["model"] == ""


class TestMessageSerializer:
    """Message serializer output format."""

    def test_includes_all_fields(self, auth_api, user):
        conv = Conversation.objects.create(owner=user)
        make_ready_document(user, "Doc", ["content about apples"])
        response = auth_api.post(
            conversation_messages_url(conv.pk), {"content": "What about apples?"}
        )

        fields = {
            "id",
            "role",
            "content",
            "sources",
            "citations",
            "model",
            "prompt_tokens",
            "completion_tokens",
            "created_at",
        }
        assert set(response.data.keys()) == fields


class TestConversationMessageNPlusOne:
    """Ensure no N+1 queries in conversation/message APIs."""

    def test_list_conversations_no_n_plus_one(self, auth_api, user, django_assert_num_queries):
        for i in range(5):
            conv = Conversation.objects.create(owner=user, title=f"Conv {i}")
            for j in range(3):
                Message.objects.create(
                    conversation=conv, role=Message.Role.USER, content=f"Msg {j}"
                )

        # 3 queries: JWT user, paginated COUNT, conversation list (annotation
        # rides along) — independent of the number of conversations/messages.
        with django_assert_num_queries(3):  # JWT user + COUNT + list (DRF pagination)
            response = auth_api.get(CONVERSATIONS_URL)
            _ = response.data["results"]

    def test_retrieve_conversation_no_n_plus_one(self, auth_api, user, django_assert_num_queries):
        conv = Conversation.objects.create(owner=user)
        for i in range(3):
            Message.objects.create(conversation=conv, role=Message.Role.USER, content=f"Msg {i}")

        # 2 queries: JWT user + the conversation row; messages are never touched.
        with django_assert_num_queries(2):
            response = auth_api.get(conversation_detail_url(conv.pk))
            _ = response.data

    def test_list_messages_no_n_plus_one(self, auth_api, user, django_assert_num_queries):
        conv = Conversation.objects.create(owner=user)
        for i in range(10):
            Message.objects.create(
                conversation=conv,
                role=Message.Role.ASSISTANT,
                content=f"Msg {i}",
                sources=[{"doc_id": 1, "snippet": "x"}],
            )

        # 4 queries: JWT user, conversation fetch, pagination COUNT, message list.
        with django_assert_num_queries(4):
            response = auth_api.get(conversation_messages_url(conv.pk))
            _ = response.data["results"]
