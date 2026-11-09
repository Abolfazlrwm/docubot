"""Personal API keys for programmatic (non-browser) access.

Design notes:

- Only the SHA-256 hash of a key is stored; the raw secret is shown exactly
  once at creation, like GitHub personal access tokens.
- A short prefix is kept for display (``dbk_ab12cd...``) so a key can be
  recognised in a list without exposing anything verifiable.
- Scopes: ``read`` may only issue safe methods; ``write`` can do everything
  (write implies read).
- Usage: every authenticated request bumps ``request_count`` and
  ``last_used_at``; a key may carry its own per-minute ``rate_limit``.
"""

import hashlib
import secrets

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F
from django.utils import timezone


class APIKey(models.Model):
    """A personal access key owned by exactly one user."""

    class Scope(models.TextChoices):
        READ = "read", "Read-only"
        WRITE = "write", "Read + write"

    KEY_PREFIX = "dbk"
    PREFIX_DISPLAY_LENGTH = 14

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    name = models.CharField(max_length=100)
    prefix = models.CharField(max_length=16, db_index=True)
    hashed_key = models.CharField(max_length=64, unique=True)
    scope = models.CharField(max_length=5, choices=Scope.choices, default=Scope.READ)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    rate_limit = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(10000)],
        help_text="Optional per-minute request cap; overrides the instance default.",
    )
    request_count = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.masked})"

    @classmethod
    def generate(cls, user, name: str, scope: str = Scope.READ, rate_limit: int | None = None):
        """Create a key. Returns ``(instance, raw_key)``; raw is shown once."""
        raw = f"{cls.KEY_PREFIX}_{secrets.token_urlsafe(32)}"
        key = cls.objects.create(
            user=user,
            name=name,
            scope=scope,
            rate_limit=rate_limit,
            prefix=raw[: cls.PREFIX_DISPLAY_LENGTH],
            hashed_key=cls.hash(raw),
        )
        return key, raw

    @staticmethod
    def hash(raw_key: str) -> str:
        return hashlib.sha256(raw_key.encode()).hexdigest()

    @classmethod
    def authenticate(cls, raw_key: str):
        """Return the active, non-revoked key for ``raw_key``, else None."""
        if not raw_key:
            return None
        return (
            cls.objects.select_related("user")
            .filter(hashed_key=cls.hash(raw_key), is_active=True, revoked_at__isnull=True)
            .first()
        )

    def revoke(self) -> None:
        self.revoked_at = timezone.now()
        self.is_active = False
        self.save(update_fields=["revoked_at", "is_active"])

    def touch(self) -> None:
        """Record usage: bump last_used_at and the lifetime request counter."""
        now = timezone.now()
        type(self).objects.filter(pk=self.pk).update(
            last_used_at=now,
            request_count=F("request_count") + 1,
        )
        self.last_used_at = now

    @property
    def masked(self) -> str:
        return f"{self.prefix}..."
