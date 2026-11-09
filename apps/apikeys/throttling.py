"""Per-key rate limiting for API-key traffic.

JWT and session traffic are never counted: the throttle engages only when
``request.auth`` is an ``APIKey``. Each key owns a per-minute request budget -
either its own ``rate_limit`` override or the instance-wide default
(``settings.APIKEY_RATE_LIMIT``, env-tunable). Rejected requests answer 429
with a ``Retry-After`` header.
"""

import math
import time

from django.conf import settings
from django.core.cache import cache
from rest_framework.throttling import BaseThrottle

from apps.apikeys.models import APIKey

CACHE_KEY_TEMPLATE = "throttles:apikey:%s"
WINDOW_SECONDS = 60


class APIKeyRateThrottle(BaseThrottle):
    """A per-minute request budget for every call made with a personal key."""

    def allow_request(self, request, view):
        auth = getattr(request, "auth", None)
        if not isinstance(auth, APIKey):
            return True  # other authentication methods are metered elsewhere

        self.key = CACHE_KEY_TEMPLATE % auth.pk
        self.history = cache.get(self.key, [])
        self.now = time.time()
        while self.history and self.history[-1] <= self.now - WINDOW_SECONDS:
            self.history.pop()

        self.num_requests = auth.rate_limit or getattr(settings, "APIKEY_RATE_LIMIT", 120)
        if len(self.history) >= self.num_requests:
            return False  # budget exhausted; wait() feeds the Retry-After header

        self.history.insert(0, self.now)
        cache.set(self.key, self.history, WINDOW_SECONDS)
        return True

    def wait(self):
        """Seconds until the oldest counted request leaves the window."""
        if not getattr(self, "history", None):
            return None
        return max(0, math.ceil(WINDOW_SECONDS - (self.now - self.history[-1])))
