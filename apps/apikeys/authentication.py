"""X-API-Key authentication for DRF.

Runs alongside JWT (see DEFAULT_AUTHENTICATION_CLASSES): when a request
carries an X-API-Key header, the API key identifies the caller; without one it
returns None and JWT authentication proceeds as usual. This makes every
existing endpoint instantly usable by scripts, CI jobs and integrations
without touching a single view.
"""

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.apikeys.models import APIKey


class APIKeyAuthentication(BaseAuthentication):
    """Authenticate requests carrying ``X-API-Key: <raw key>``."""

    def authenticate(self, request):
        raw = request.headers.get("X-API-Key", "")
        if not raw:
            return None  # no API key supplied; let the other authenticators run
        key = APIKey.authenticate(raw)
        if key is None:
            raise AuthenticationFailed("Invalid or revoked API key.")
        key.touch()
        return (key.user, key)
