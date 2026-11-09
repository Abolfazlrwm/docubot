"""Tests for the admin usage analytics endpoint."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.apikeys.models import APIKey
from apps.chat.models import Conversation, Message
from apps.documents.models import Document
from tests.factories import PASSWORD, DocumentFactory, UserFactory

pytestmark = pytest.mark.django_db

STATS_URL = reverse("stats")


@pytest.fixture
def staff_api(api, db):
    staff = UserFactory(is_staff=True)
    response = api.post(
        reverse("users:login"),
        {"username": staff.username, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200, response.data
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    return api


class TestStatsAccess:
    def test_requires_staff(self, auth_api):
        response = auth_api.get(STATS_URL)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_requires_authentication(self, api):
        response = api.get(STATS_URL)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_works_with_a_staff_api_key(self, staff_api):
        staff = get_user_model().objects.get(is_staff=True)
        _key, raw = APIKey.generate(user=staff, name="ops", scope=APIKey.Scope.WRITE)
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=raw)

        response = client.get(STATS_URL)

        assert response.status_code == status.HTTP_200_OK


class TestStatsNumbers:
    def test_shape_and_counts(self, staff_api, user):
        DocumentFactory(owner=user, status=Document.Status.READY)
        conversation = Conversation.objects.create(owner=user)
        Message.objects.create(conversation=conversation, role=Message.Role.USER, content="q")
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content="a [1]",
            prompt_tokens=120,
            completion_tokens=45,
            rating=Message.Rating.UP,
        )

        response = staff_api.get(STATS_URL)

        assert response.status_code == status.HTTP_200_OK
        body = response.data
        assert body["users"] == 2  # the fixture user + the staff account
        assert body["documents"]["total"] == 1
        assert body["documents"]["ready"] == 1
        assert body["chunks"] == 0  # factory rows bypass the processing pipeline
        assert body["conversations"] == 1
        assert body["messages"]["user"] == 1
        assert body["messages"]["assistant"] == 1
        assert body["feedback"]["up"] == 1
        assert body["feedback"]["down"] == 0
        assert body["feedback"]["positive_ratio"] == 1.0
        assert body["tokens"]["prompt"] == 120
        assert body["tokens"]["completion"] == 45
        assert body["api_keys"]["total"] == 0
        assert len(body["activity_14d"]) == 14
        assert sum(d["assistant_messages"] for d in body["activity_14d"]) == 1

    def test_feedback_ratio_none_without_ratings(self, staff_api):
        response = staff_api.get(STATS_URL)

        assert response.data["feedback"]["positive_ratio"] is None

    def test_activity_series_is_zero_filled(self, staff_api):
        response = staff_api.get(STATS_URL)

        series = response.data["activity_14d"]
        assert series[0]["date"] < series[-1]["date"]
        assert all(d["assistant_messages"] == 0 for d in series)

    def test_api_key_traffic_ranking(self, staff_api, user):
        staff = get_user_model().objects.get(is_staff=True)
        APIKey.generate(user=staff, name="ops", scope=APIKey.Scope.WRITE)  # never used
        busy, _raw = APIKey.generate(user=user, name="ci", scope=APIKey.Scope.WRITE)
        quiet, _raw2 = APIKey.generate(user=user, name="cron")
        APIKey.objects.filter(pk=busy.pk).update(request_count=40, last_used_at=timezone.now())
        APIKey.objects.filter(pk=quiet.pk).update(request_count=5, last_used_at=timezone.now())

        response = staff_api.get(STATS_URL)

        body = response.data["api_keys"]
        assert body["total"] == 3
        assert body["active"] == 3
        assert body["requests_total"] == 45
        top = body["top_keys"]
        assert [k["name"] for k in top] == ["ci", "cron"]  # unused keys stay out
        assert top[0]["request_count"] == 40
        assert top[0]["scope"] == "write"
        assert top[0]["masked"].startswith(APIKey.KEY_PREFIX)
        assert "hashed_key" not in top[0]
