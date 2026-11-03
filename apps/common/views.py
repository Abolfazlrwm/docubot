"""Shared API views: service health reporting for orchestrators and load balancers."""

import django
from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from config.celery import app as celery_app

_CACHE_PROBE_KEY = "healthcheck:probe"
_CACHE_PROBE_TTL = 5
_CELERY_PING_TIMEOUT = 1.0


class HealthSerializer(serializers.Serializer):
    """Response shape of the health endpoint."""

    status = serializers.CharField()
    components = serializers.DictField(child=serializers.CharField())
    django = serializers.CharField()


def _check_database() -> str:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return "error"
    return "ok"


def _check_cache() -> str:
    try:
        cache.set(_CACHE_PROBE_KEY, "1", _CACHE_PROBE_TTL)
        if cache.get(_CACHE_PROBE_KEY) != "1":
            return "error"
    except Exception:
        return "error"
    return "ok"


def _check_celery() -> str:
    """Non-critical: a missing worker degrades, but the API still serves."""
    if getattr(celery_app.conf, "task_always_eager", False):
        return "eager"
    try:
        pings = celery_app.control.inspect(timeout=_CELERY_PING_TIMEOUT).ping() or {}
    except Exception:
        return "degraded"
    return "ok" if pings else "degraded"


@api_view(["GET"])
@permission_classes([])
@extend_schema(
    tags=["health"],
    summary="Service health",
    description=(
        "Liveness/readiness for load balancers. Public on purpose: probes carry no JWT. "
        "A failing database or cache returns 503; a missing Celery worker is reported "
        "as degraded but keeps the API up."
    ),
    responses={
        200: HealthSerializer,
        503: {"type": "object", "properties": {"status": {"type": "string"}}},
    },
)
def health_view(request):
    """Report the health of the API and its backing services."""
    components = {
        "database": _check_database(),
        "cache": _check_cache(),
        "celery": _check_celery(),
    }
    critical_ok = all(components[name] == "ok" for name in ("database", "cache"))
    body = {
        "status": "ok" if critical_ok else "error",
        "components": components,
        "django": django.get_version(),
    }
    return Response(
        body,
        status=status.HTTP_200_OK if critical_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
    )
