"""Tests for personal API keys: creation, masking, listing and revocation."""

import hashlib

import pytest
from django.urls import reverse
from rest_framework import status

from apps.apikeys.models import APIKey
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def create_key(client, name="ci", scope="read"):
    return client.post(reverse("apikeys:api-keys"), {"name": name, "scope": scope}, format="json")


class TestKeyCreation:
    def test_create_returns_raw_key_exactly_once(self, auth_api):
        response = create_key(auth_api, scope="write")

        assert response.status_code == status.HTTP_201_CREATED
        raw = response.data["key"]
        assert raw.startswith(APIKey.KEY_PREFIX)
        key = APIKey.objects.get()
        assert key.hashed_key == hashlib.sha256(raw.encode()).hexdigest()
        assert response.data["masked"] == key.masked
        assert response.data["scope"] == "write"

    def test_create_defaults_to_read_scope(self, auth_api):
        response = create_key(auth_api)

        assert response.status_code == status.HTTP_201_CREATED
        assert APIKey.objects.get().scope == APIKey.Scope.READ

    def test_create_requires_authentication(self, api):
        response = create_key(api)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_name_is_required(self, auth_api):
        response = auth_api.post(reverse("apikeys:api-keys"), {}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_with_a_rate_limit_override(self, auth_api):
        response = auth_api.post(
            reverse("apikeys:api-keys"),
            {"name": "tight", "rate_limit": 7},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["rate_limit"] == 7
        assert APIKey.objects.get().rate_limit == 7

    def test_create_rejects_an_impossible_rate_limit(self, auth_api):
        response = auth_api.post(
            reverse("apikeys:api-keys"),
            {"name": "greedy", "rate_limit": 0},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not APIKey.objects.exists()


class TestKeyListing:
    def test_never_exposes_the_secret(self, auth_api):
        create_key(auth_api, name="one")
        create_key(auth_api, name="two")

        response = auth_api.get(reverse("apikeys:api-keys"))

        assert response.status_code == status.HTTP_200_OK
        items = response.data["results"]
        assert len(items) == 2
        for item in items:
            assert "key" not in item
            assert "hashed_key" not in item
            assert item["masked"].startswith(APIKey.KEY_PREFIX)
            assert "rate_limit" in item  # budget visible to the owner
            assert "request_count" in item  # usage visible to the owner

    def test_owner_scoped(self, auth_api):
        create_key(auth_api, name="mine")
        APIKey.generate(user=UserFactory(), name="theirs")

        response = auth_api.get(reverse("apikeys:api-keys"))

        assert response.data["count"] == 1


class TestRevocation:
    def test_revoke_disables_the_key(self, auth_api):
        create_key(auth_api)
        key = APIKey.objects.get()

        response = auth_api.post(reverse("apikeys:api-key-revoke", kwargs={"pk": key.pk}))

        assert response.status_code == status.HTTP_200_OK
        key.refresh_from_db()
        assert key.revoked_at is not None
        assert key.is_active is False

    def test_revoke_is_idempotent(self, auth_api):
        create_key(auth_api)
        key = APIKey.objects.get()
        url = reverse("apikeys:api-key-revoke", kwargs={"pk": key.pk})

        first = auth_api.post(url)
        second = auth_api.post(url)

        assert first.status_code == second.status_code == status.HTTP_200_OK

    def test_cannot_revoke_someone_elses_key(self, auth_api):
        other_key, _raw = APIKey.generate(user=UserFactory(), name="theirs")

        response = auth_api.post(reverse("apikeys:api-key-revoke", kwargs={"pk": other_key.pk}))

        assert response.status_code == status.HTTP_404_NOT_FOUND
        other_key.refresh_from_db()
        assert other_key.is_active is True
