from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower


class User(AbstractUser):
    """Default user. Email is required and unique case-insensitively."""

    email = models.EmailField("email address", unique=True)

    class Meta(AbstractUser.Meta):
        constraints = [
            # The field-level unique=True is exact-match; this constraint is the
            # real case-insensitive guarantee at the database level.
            models.UniqueConstraint(Lower("email"), name="users_user_email_ci_unique"),
        ]
