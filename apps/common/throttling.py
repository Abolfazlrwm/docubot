"""Rate limiting for abuse-prone endpoints, built on DRF scoped throttling.

Scopes and their default rates live in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
and can be tuned per environment (e.g. THROTTLE_AUTH=100/min). Throttles are opt-in
per view, so cheap read endpoints are never counted against a bucket.
"""

from rest_framework.throttling import ScopedRateThrottle


class WriteMethodsRateThrottle(ScopedRateThrottle):
    """Scoped throttle that only counts unsafe methods.

    GET/HEAD/OPTIONS stay unmetered so browsing history or documents never
    burns the same bucket as asking questions or uploading files.
    """

    def allow_request(self, request, view):
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return True
        return super().allow_request(request, view)
