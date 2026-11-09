"""Tests for the scoped rate limits (auth, chat, search, upload) and the
per-API-key request budget."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status

from apps.apikeys.models import APIKey
from tests.factories import PASSWORD, UserFactory

User = get_user_model()

pytestmark = pytest.mark.django_db

LOGIN_URL = reverse("users:login")
REGISTER_URL = reverse("users:register")
SEARCH_URL = reverse("chat:search")
DOCS_URL = reverse("documents:document-list")


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


class TestAPIKeyBudget:
    """Every personal key owns a per-minute request budget; JWT is untouched."""

    @pytest.fixture
    def key_api(self, api, user):
        """An APIClient with a fresh read key whose budget can be tuned."""

        def make(rate_limit=None):
            _key, raw = APIKey.generate(user=user, name="bot", rate_limit=rate_limit)
            api.credentials(HTTP_X_API_KEY=raw)
            return api

        return make

    def test_budget_is_enforced_with_429_and_retry_after(self, key_api):
        client = key_api(rate_limit=2)

        assert client.get(DOCS_URL).status_code == status.HTTP_200_OK
        assert client.get(DOCS_URL).status_code == status.HTTP_200_OK
        throttled = client.get(DOCS_URL)

        assert throttled.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert 0 <= int(throttled.headers["Retry-After"]) <= 60

    def test_key_without_override_uses_the_instance_default(self, key_api, settings):
        settings.APIKEY_RATE_LIMIT = 3
        client = key_api(rate_limit=None)

        for _ in range(3):
            assert client.get(DOCS_URL).status_code == status.HTTP_200_OK
        assert client.get(DOCS_URL).status_code == status.HTTP_429_TOO_MANY_REQUESTS

    def test_jwt_traffic_is_never_metered(self, auth_api, settings):
        settings.APIKEY_RATE_LIMIT = 1

        for _ in range(5):  # the same volume that would throttle a key
            assert auth_api.get(DOCS_URL).status_code == status.HTTP_200_OK
        assert "X-RateLimit-Limit" not in auth_api.get(DOCS_URL).headers

    def test_success_headers_report_the_remaining_budget(self, key_api):
        client = key_api(rate_limit=5)

        response = client.get(DOCS_URL)

        assert response.headers["X-RateLimit-Limit"] == "5"
        assert response.headers["X-RateLimit-Remaining"] == "4"
        assert int(response.headers["X-RateLimit-Reset"]) > 0

    def test_429_headers_show_an_empty_budget(self, key_api):
        client = key_api(rate_limit=1)

        assert client.get(DOCS_URL).status_code == status.HTTP_200_OK
        throttled = client.get(DOCS_URL)

        assert throttled.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert throttled.headers["X-RateLimit-Remaining"] == "0"

    def test_usage_counter_accumulates(self, key_api, user):
        client = key_api(rate_limit=10)

        for _ in range(3):
            client.get(DOCS_URL)

        key = APIKey.objects.get(user=user)
        assert key.request_count == 3
        assert key.last_used_at is not None
