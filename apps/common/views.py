"""Shared API views: service health reporting and admin usage analytics."""

from datetime import timedelta

import django
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from apps.apikeys.models import APIKey
from apps.chat.models import Conversation, Message
from apps.documents.models import Chunk, Document
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


# ---------------------------------------------------------------------------
# Admin analytics
# ---------------------------------------------------------------------------


class TopKeySerializer(serializers.Serializer):
    """One entry of the most-used API keys ranking."""

    name = serializers.CharField()
    masked = serializers.CharField()
    scope = serializers.CharField()
    request_count = serializers.IntegerField()
    last_used_at = serializers.DateTimeField()


class StatsSerializer(serializers.Serializer):
    """Response shape of the admin analytics endpoint."""

    users = serializers.IntegerField()
    documents = serializers.DictField(child=serializers.IntegerField())
    chunks = serializers.IntegerField()
    conversations = serializers.IntegerField()
    messages = serializers.DictField(child=serializers.IntegerField())
    feedback = serializers.DictField()
    tokens = serializers.DictField(child=serializers.IntegerField())
    api_keys = serializers.DictField()
    activity_14d = serializers.ListField()


def _document_status_counts() -> dict:
    counts = dict.fromkeys(Document.Status.values, 0)
    for row in Document.objects.values("status").annotate(n=Count("id")):
        counts[row["status"]] = row["n"]
    counts["total"] = sum(counts.values())
    return counts


def _activity_series(days: int = 14) -> list:
    """Per-day assistant message counts for the last `days` days, zero-filled."""
    start = timezone.localdate() - timedelta(days=days - 1)
    rows = (
        Message.objects.filter(role=Message.Role.ASSISTANT, created_at__date__gte=start)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(assistant_messages=Count("id"))
    )
    counts = {row["day"]: row["assistant_messages"] for row in rows}
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "assistant_messages": counts.get(start + timedelta(days=i), 0),
        }
        for i in range(days)
    ]


@api_view(["GET"])
@permission_classes([IsAdminUser])
@extend_schema(
    tags=["stats"],
    summary="Usage analytics (admin only)",
    description=(
        "Aggregate usage signals: users, documents by status, chunks, messages "
        "by role, token totals, the feedback ratio, API-key adoption and "
        "traffic (lifetime request counter plus the five most-used keys) and a "
        "14-day activity series. Staff only."
    ),
    responses=StatsSerializer,
)
def stats_view(request):
    """Aggregate product analytics for the admin dashboard."""
    rated = Message.objects.exclude(rating="")
    up = rated.filter(rating=Message.Rating.UP).count()
    down = rated.count() - up
    tokens = Message.objects.aggregate(
        prompt=Sum("prompt_tokens"), completion=Sum("completion_tokens")
    )

    return Response(
        {
            "users": get_user_model().objects.count(),
            "documents": _document_status_counts(),
            "chunks": Chunk.objects.count(),
            "conversations": Conversation.objects.count(),
            "messages": {
                "total": Message.objects.count(),
                "user": Message.objects.filter(role=Message.Role.USER).count(),
                "assistant": Message.objects.filter(role=Message.Role.ASSISTANT).count(),
            },
            "feedback": {
                "up": up,
                "down": down,
                "positive_ratio": round(up / (up + down), 4) if (up + down) else None,
            },
            "tokens": {
                "prompt": tokens["prompt"] or 0,
                "completion": tokens["completion"] or 0,
            },
            "api_keys": {
                "total": APIKey.objects.count(),
                "active": APIKey.objects.filter(is_active=True, revoked_at__isnull=True).count(),
                "requests_total": APIKey.objects.aggregate(n=Sum("request_count"))["n"] or 0,
                "top_keys": [
                    {
                        "name": key.name,
                        "masked": key.masked,
                        "scope": key.scope,
                        "request_count": key.request_count,
                        "last_used_at": key.last_used_at,
                    }
                    for key in APIKey.objects.filter(request_count__gt=0).order_by(
                        "-request_count", "name"
                    )[:5]
                ],
            },
            "activity_14d": _activity_series(),
        }
    )
