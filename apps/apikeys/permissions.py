from rest_framework.permissions import BasePermission

from apps.apikeys.models import APIKey


class HasAPIKeyScope(BasePermission):
    """Enforce the scope of API-key-authenticated requests.

    JWT and every other authentication method are unaffected. A ``read`` key
    may only issue safe methods; a ``write`` key may do everything.
    """

    message = "This API key's scope ('read') does not allow write operations."

    def has_permission(self, request, view):
        auth = getattr(request, "auth", None)
        if not isinstance(auth, APIKey):
            return True
        if auth.scope == APIKey.Scope.WRITE:
            return True
        return request.method in ("GET", "HEAD", "OPTIONS")
