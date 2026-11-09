"""Rate-limit transparency for API-key traffic.

Every response to a request authenticated with a personal key carries the
standard ``X-RateLimit-*`` headers, so integrations can pace themselves
without reading the docs:

- ``X-RateLimit-Limit``: the key's effective per-minute budget
- ``X-RateLimit-Remaining``: requests still available in the current window
- ``X-RateLimit-Reset``: unix epoch at which the window frees up

A 429 additionally carries DRF's ``Retry-After``. JWT traffic is untouched:
no key, no headers. The numbers come from the same cache bucket the throttle
uses, so headers and enforcement never disagree.
"""

import time

from django.conf import settings
from django.core.cache import cache

from apps.apikeys.models import APIKey
from apps.apikeys.throttling import CACHE_KEY_TEMPLATE, WINDOW_SECONDS

LIMIT_HEADER = "X-RateLimit-Limit"
REMAINING_HEADER = "X-RateLimit-Remaining"
RESET_HEADER = "X-RateLimit-Reset"


def _effective_limit(key: APIKey) -> int:
    return key.rate_limit or getattr(settings, "APIKEY_RATE_LIMIT", 120)


def budget_state(key: APIKey) -> tuple[int, int, int]:
    """Return ``(limit, remaining, reset_epoch)`` for a key right now."""
    now = time.time()
    history = cache.get(CACHE_KEY_TEMPLATE % key.pk, [])
    active = [stamp for stamp in history if stamp > now - WINDOW_SECONDS]
    limit = _effective_limit(key)
    remaining = max(0, limit - len(active))
    # oldest stamp leaves the window; an empty bucket resets after a full window
    reset = int(active[-1] + WINDOW_SECONDS) if active else int(now + WINDOW_SECONDS)
    return limit, remaining, reset


class APIKeyRateLimitHeadersMiddleware:
    """Attach X-RateLimit-* headers to DRF responses for key-authenticated calls."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        context = getattr(response, "renderer_context", None)
        view = context.get("view") if context else None
        drf_request = getattr(view, "request", None)
        auth = getattr(drf_request, "auth", None)
        if not isinstance(auth, APIKey):
            return response  # JWT/session traffic is metered by the scoped throttles

        limit, remaining, reset = budget_state(auth)
        response[LIMIT_HEADER] = str(limit)
        response[REMAINING_HEADER] = str(remaining)
        response[RESET_HEADER] = str(reset)
        return response
