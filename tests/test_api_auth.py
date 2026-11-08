"""X-API-Key across the API: scopes, invalid/revoked keys, last-used tracking."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status

from apps.apikeys.models import APIKey
from apps.documents.models import Document

pytestmark = pytest.mark.django_db


def with_key(api, raw):
    api.credentials(HTTP_X_API_KEY=raw)
    return api


def upload(client):
    upload_file = SimpleUploadedFile("notes.txt", b"hello docubot", content_type="text/plain")
    return client.post(
        reverse("documents:document-list"),
        {"title": "Notes", "file": upload_file},
        format="multipart",
    )


class TestApiKeyAccess:
    def test_read_key_can_list_documents(self, api, user):
        _key, raw = APIKey.generate(user=user, name="ro")

        response = with_key(api, raw).get(reverse("documents:document-list"))

        assert response.status_code == status.HTTP_200_OK

    def test_read_key_cannot_upload(self, api, user):
        _key, raw = APIKey.generate(user=user, name="ro")
        client = with_key(api, raw)

        response = upload(client)

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "does not allow write" in response.data["detail"]
        assert not Document.objects.filter(owner=user).exists()

    def test_write_key_can_upload(self, api, user):
        _key, raw = APIKey.generate(user=user, name="ci", scope=APIKey.Scope.WRITE)
        client = with_key(api, raw)

        response = upload(client)

        assert response.status_code != status.HTTP_403_FORBIDDEN
        assert status.is_success(response.status_code)
        assert Document.objects.filter(owner=user, title="Notes").exists()

    def test_write_key_can_search(self, api, user):
        _key, raw = APIKey.generate(user=user, name="rw", scope=APIKey.Scope.WRITE)

        response = with_key(api, raw).post(reverse("chat:search"), {"query": "x"})

        assert response.status_code == status.HTTP_200_OK
        assert response.data == []

    def test_invalid_key_is_rejected(self, api, user):
        client = with_key(api, "dbk_not_a_real_key")

        response = client.get(reverse("documents:document-list"))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_revoked_key_is_rejected_immediately(self, api, user):
        key, raw = APIKey.generate(user=user, name="tmp")
        client = with_key(api, raw)
        assert client.get(reverse("documents:document-list")).status_code == 200

        key.revoke()

        assert client.get(reverse("documents:document-list")).status_code == (
            status.HTTP_401_UNAUTHORIZED
        )

    def test_last_used_at_is_recorded(self, api, user):
        _key, raw = APIKey.generate(user=user, name="tracked")

        with_key(api, raw).get(reverse("documents:document-list"))

        assert APIKey.objects.get().last_used_at is not None

    def test_jwt_still_works_alongside(self, auth_api):
        response = auth_api.get(reverse("documents:document-list"))

        assert response.status_code == status.HTTP_200_OK
