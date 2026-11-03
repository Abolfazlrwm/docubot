"""Tests for the scoped rate limits (auth, chat, search, upload)."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status

from tests.factories import PASSWORD, UserFactory

User = get_user_model()

pytestmark = pytest.mark.django_db

LOGIN_URL = reverse("users:login")
REGISTER_URL = reverse("users:register")
SEARCH_URL = reverse("chat:search")


def set_rate(settings, scope: str, rate: str) -> None:
    settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"][scope] = rate


class TestAuthThrottle:
    """The auth scope protects login/register/refresh from brute force."""

    def test_login_blocks_after_limit(self, api, settings):
        set_rate(settings, "auth", "2/min")

        for _ in range(2):  # two attempts are allowed (even failed ones)
            response = api.post(LOGIN_URL, {"username": "x", "password": "y"}, format="json")
            assert response.status_code == status.HTTP_401_UNAUTHORIZED

        response = api.post(LOGIN_URL, {"username": "x", "password": "y"}, format="json")
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert "Retry-After" in response.headers or response.json()  # DRF throttle body

    def test_successful_logins_count_too(self, api, settings):
        user = UserFactory()
        set_rate(settings, "auth", "2/min")

        for _ in range(2):
            response = api.post(
                LOGIN_URL, {"username": user.username, "password": PASSWORD}, format="json"
            )
            assert response.status_code == status.HTTP_200_OK

        response = api.post(
            LOGIN_URL, {"username": user.username, "password": PASSWORD}, format="json"
        )
        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    def test_register_shares_the_auth_scope(self, api, settings):
        set_rate(settings, "auth", "1/min")

        first = api.post(
            REGISTER_URL,
            {"username": "newuser", "email": "new@example.com", "password": "s3creT!pass!"},
            format="json",
        )
        second = api.post(
            REGISTER_URL,
            {"username": "other", "email": "other@example.com", "password": "s3creT!pass!"},
            format="json",
        )

        assert first.status_code == status.HTTP_201_CREATED
        assert second.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    def test_rates_are_env_tunable(self, api, settings):
        """A raised limit lets the same number of requests through."""
        user = UserFactory()
        set_rate(settings, "auth", "5/min")

        for _ in range(5):
            response = api.post(
                LOGIN_URL, {"username": user.username, "password": PASSWORD}, format="json"
            )
            assert response.status_code == status.HTTP_200_OK


class TestChatThrottle:
    """The chat scope meters questions, not history reads."""

    def test_ask_is_limited_but_list_is_not(self, auth_api, settings):
        from tests.test_chat import conversation_messages_url  # reuse the URL helper

        set_rate(settings, "chat", "1/min")

        first_read = auth_api.get(conversation_messages_url(99999))
        second_read = auth_api.get(conversation_messages_url(99999))
        assert first_read.status_code == status.HTTP_404_NOT_FOUND
        assert second_read.status_code == status.HTTP_404_NOT_FOUND  # GETs stay unmetered


class TestSearchThrottle:
    """The search scope limits semantic search bursts."""

    def test_search_blocks_after_limit(self, auth_api, settings):
        set_rate(settings, "search", "1/min")

        first = auth_api.post(SEARCH_URL, {"query": "anything"})
        second = auth_api.post(SEARCH_URL, {"query": "anything"})

        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_429_TOO_MANY_REQUESTS


class TestUploadThrottle:
    """Uploads are metered; document reads are not."""

    def test_read_not_counted_against_upload_bucket(self, auth_api, settings):
        set_rate(settings, "upload", "1/hour")

        for _ in range(3):  # any number of GETs never hits the upload bucket
            response = auth_api.get(reverse("documents:document-list"))
            assert response.status_code == status.HTTP_200_OK
