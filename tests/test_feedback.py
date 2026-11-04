"""Tests for the thumbs up/down feedback endpoint."""

import pytest
from django.urls import reverse
from rest_framework import status

from apps.chat.models import Conversation, Message

pytestmark = pytest.mark.django_db


def feedback_url(conversation_pk: int, message_pk: int) -> str:
    return reverse(
        "chat:message-feedback",
        kwargs={"conversation_pk": conversation_pk, "message_pk": message_pk},
    )


def make_assistant_message(conversation: Conversation, content: str = "The answer [1].") -> Message:
    return Message.objects.create(
        conversation=conversation, role=Message.Role.ASSISTANT, content=content
    )


class TestSetFeedback:
    def test_rate_up(self, auth_api, user):
        conversation = Conversation.objects.create(owner=user)
        message = make_assistant_message(conversation)

        response = auth_api.post(feedback_url(conversation.pk, message.pk), {"rating": "up"})

        assert response.status_code == status.HTTP_200_OK
        message.refresh_from_db()
        assert message.rating == "up"
        assert message.rating_comment == ""
        assert message.rated_at is not None
        assert response.data["rating"] == "up"
        conversation = Conversation.objects.create(owner=user)
        message = make_assistant_message(conversation)

        response = auth_api.post(
            feedback_url(conversation.pk, message.pk),
            {"rating": "down", "comment": "missed the second clause"},
        )

        assert response.status_code == status.HTTP_200_OK
        message.refresh_from_db()
        assert message.rating == "down"
        assert message.rating_comment == "missed the second clause"

    def test_update_existing_rating(self, auth_api, user):
        conversation = Conversation.objects.create(owner=user)
        message = make_assistant_message(conversation)
        auth_api.post(feedback_url(conversation.pk, message.pk), {"rating": "up"})

        response = auth_api.post(feedback_url(conversation.pk, message.pk), {"rating": "down"})

        assert response.status_code == status.HTTP_200_OK
        message.refresh_from_db()
        assert message.rating == "down"

    def test_comment_is_cleared_when_rating_changes_without_one(self, auth_api, user):
        conversation = Conversation.objects.create(owner=user)
        message = make_assistant_message(conversation)
        auth_api.post(
            feedback_url(conversation.pk, message.pk),
            {"rating": "down", "comment": "wrong"},
        )

        auth_api.post(feedback_url(conversation.pk, message.pk), {"rating": "up"})

        message.refresh_from_db()
        assert message.rating_comment == ""


class TestFeedbackValidation:
    def test_invalid_rating_rejected(self, auth_api, user):
        conversation = Conversation.objects.create(owner=user)
        message = make_assistant_message(conversation)

        response = auth_api.post(feedback_url(conversation.pk, message.pk), {"rating": "meh"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        message.refresh_from_db()
        assert message.rating == ""

    def test_missing_rating_rejected(self, auth_api, user):
        conversation = Conversation.objects.create(owner=user)
        message = make_assistant_message(conversation)

        response = auth_api.post(feedback_url(conversation.pk, message.pk), {})

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_only_assistant_messages_are_rateable(self, auth_api, user):
        conversation = Conversation.objects.create(owner=user)
        message = conversation.messages.create(role=Message.Role.USER, content="question")

        response = auth_api.post(feedback_url(conversation.pk, message.pk), {"rating": "up"})

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestFeedbackPermissions:
    def test_foreign_conversation_is_404(self, auth_api, user, django_user_model):
        other = django_user_model.objects.create_user(username="other", password="x")
        conversation = Conversation.objects.create(owner=other)
        message = make_assistant_message(conversation)

        response = auth_api.post(feedback_url(conversation.pk, message.pk), {"rating": "up"})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_own_message_in_foreign_conversation_is_404(self, auth_api, user, django_user_model):
        """The message must live in the conversation named in the URL."""
        other = django_user_model.objects.create_user(username="other2", password="x")
        other_conversation = Conversation.objects.create(owner=other)
        own_conversation = Conversation.objects.create(owner=user)
        message = make_assistant_message(own_conversation)

        response = auth_api.post(feedback_url(other_conversation.pk, message.pk), {"rating": "up"})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated_is_401(self, api, user):
        conversation = Conversation.objects.create(owner=user)
        message = make_assistant_message(conversation)

        response = api.post(feedback_url(conversation.pk, message.pk), {"rating": "up"})

        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestClearFeedback:
    def test_delete_clears_the_rating(self, auth_api, user):
        conversation = Conversation.objects.create(owner=user)
        message = make_assistant_message(conversation)
        auth_api.post(
            feedback_url(conversation.pk, message.pk),
            {"rating": "down", "comment": "nope"},
        )

        response = auth_api.delete(feedback_url(conversation.pk, message.pk))

        assert response.status_code == status.HTTP_204_NO_CONTENT
        message.refresh_from_db()
        assert message.rating == ""
        assert message.rating_comment == ""
        assert message.rated_at is None

    def test_delete_without_existing_rating_is_still_204(self, auth_api, user):
        conversation = Conversation.objects.create(owner=user)
        message = make_assistant_message(conversation)

        response = auth_api.delete(feedback_url(conversation.pk, message.pk))

        assert response.status_code == status.HTTP_204_NO_CONTENT


class TestFeedbackInMessageList:
    def test_message_serializer_exposes_rating_fields(self, auth_api, user):
        conversation = Conversation.objects.create(owner=user)
        message = make_assistant_message(conversation)
        auth_api.post(
            feedback_url(conversation.pk, message.pk),
            {"rating": "up", "comment": "great"},
        )

        response = auth_api.get(
            reverse("chat:conversation-messages", kwargs={"conversation_pk": conversation.pk})
        )

        assistant = next(m for m in response.data["results"] if m["role"] == "assistant")
        assert assistant["rating"] == "up"
        assert assistant["rating_comment"] == "great"
        assert assistant["rated_at"] is not None
