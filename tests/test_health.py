"""Tests for the public health endpoint."""

import pytest
from django.urls import reverse
from rest_framework import status

pytestmark = pytest.mark.django_db

HEALTH_URL = reverse("health")


def test_health_is_public(api):
    """Probes carry no JWT; the endpoint must not require authentication."""
    response = api.get(HEALTH_URL)
    assert response.status_code == status.HTTP_200_OK


def test_health_reports_ok_on_live_database_and_cache(api):
    response = api.get(HEALTH_URL)

    body = response.json()
    assert body["status"] == "ok"
    assert body["components"]["database"] == "ok"
    assert body["components"]["cache"] == "ok"
    assert "celery" in body["components"]  # ok / degraded / eager, never fatal
    assert "django" in body


def test_health_returns_503_when_database_reports_error(api, monkeypatch):
    from apps.common import views as health_views

    monkeypatch.setattr(health_views, "_check_database", lambda: "error")

    response = api.get(HEALTH_URL)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["status"] == "error"
    assert response.json()["components"]["database"] == "error"


def test_health_swallows_probe_exceptions_as_error(api, monkeypatch):
    """An unexpected exception inside a probe must produce a 503, not a 500."""
    from apps.common import views as health_views

    def broken_probe(*args, **kwargs):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(health_views.connection, "cursor", broken_probe)

    response = api.get(HEALTH_URL)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["components"]["database"] == "error"


def test_health_returns_503_when_cache_is_down(api, monkeypatch):
    from apps.common import views as health_views

    monkeypatch.setattr(health_views, "_check_cache", lambda: "error")

    response = api.get(HEALTH_URL)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json()["components"]["cache"] == "error"


def test_health_reports_degraded_celery_as_ok(api, monkeypatch):
    """A missing worker must degrade the report, not fail the probe."""
    from apps.common import views as health_views

    monkeypatch.setattr(health_views, "_check_celery", lambda: "degraded")

    response = api.get(HEALTH_URL)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["components"]["celery"] == "degraded"
