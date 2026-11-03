"""Business logic for user accounts."""

from django.contrib.auth.password_validation import validate_password

from apps.users.models import User


def register_user(*, username: str, email: str, password: str) -> User:
    """Create an active user with a validated password and a lowercase email."""
    email = email.strip().lower()
    candidate = User(username=username, email=email)
    validate_password(password, user=candidate)
    return User.objects.create_user(username=username, email=email, password=password)
