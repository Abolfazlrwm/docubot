"""Shared fixtures. An autouse fixture moves MEDIA_ROOT into a temp directory
so tests never write into the real media folder."""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from tests.factories import PASSWORD, UserFactory


@pytest.fixture(autouse=True)
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture(autouse=True)
def fast_processing(settings):
    """Hermetic processing for every test: eager Celery + fake embeddings.

    Celery reads its configuration lazily from Django settings, so overrides
    must go through the settings fixture followed by a forced config reload
    (mutating celery_app.conf directly does not stick in celery 5.6).
    """
    settings.EMBEDDING_PROVIDER = "fake"
    settings.EMBEDDING_DIMENSIONS = 384
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    from config.celery import app as celery_app

    celery_app.config_from_object("django.conf:settings", namespace="CELERY", force=True)
    yield


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def user(db):
    return UserFactory()


@pytest.fixture
def auth_api(api, user):
    """An APIClient authenticated with a real JWT obtained from the login endpoint."""
    response = api.post(
        reverse("users:login"),
        {"username": user.username, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200, response.data
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    return api
