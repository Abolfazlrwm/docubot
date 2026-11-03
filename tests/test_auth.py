import pytest
from django.urls import reverse
from rest_framework import status

from apps.users.models import User
from tests.factories import PASSWORD

pytestmark = pytest.mark.django_db


def test_register_success(api):
    response = api.post(
        reverse("users:register"),
        {
            "username": "alice",
            "email": "alice@example.com",
            "password": "Un1que&safe-pass",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["username"] == "alice"
    assert "password" not in response.data
    user = User.objects.get(username="alice")
    assert user.email == "alice@example.com"
    assert user.check_password("Un1que&safe-pass")


def test_register_email_is_lowercased(api):
    response = api.post(
        reverse("users:register"),
        {"username": "bob", "email": "BOB@Example.COM", "password": "Un1que&safe-pass"},
        format="json",
    )

    assert response.status_code == status.HTTP_201_CREATED
    assert User.objects.get(username="bob").email == "bob@example.com"


def test_register_duplicate_email_case_insensitive(api, user):
    response = api.post(
        reverse("users:register"),
        {"username": "other", "email": user.email.upper(), "password": "Un1que&safe-pass"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "email" in response.data


def test_register_duplicate_username(api, user):
    response = api.post(
        reverse("users:register"),
        {"username": user.username, "email": "new@example.com", "password": "Un1que&safe-pass"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "username" in response.data


@pytest.mark.parametrize(
    "password",
    [
        "short",  # MinimumLengthValidator
        "12345678",  # NumericPasswordValidator
        "password",  # CommonPasswordValidator
    ],
)
def test_register_weak_password_rejected(api, password):
    response = api.post(
        reverse("users:register"),
        {"username": "carol", "email": "carol@example.com", "password": password},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "password" in response.data
    assert not User.objects.filter(username="carol").exists()


def test_register_password_similar_to_username_rejected(api):
    response = api.post(
        reverse("users:register"),
        {"username": "alexandra", "email": "alexandra@example.com", "password": "alexandra2024"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "password" in response.data


def test_register_missing_email(api):
    response = api.post(
        reverse("users:register"),
        {"username": "erin", "password": "Un1que&safe-pass"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "email" in response.data


def test_login_success(api, user):
    response = api.post(
        reverse("users:login"),
        {"username": user.username, "password": PASSWORD},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert "access" in response.data
    assert "refresh" in response.data


def test_login_wrong_password(api, user):
    response = api.post(
        reverse("users:login"),
        {"username": user.username, "password": "wrong-pass-123"},
        format="json",
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_login_unknown_user(api):
    response = api.post(
        reverse("users:login"),
        {"username": "ghost", "password": "wrong-pass-123"},
        format="json",
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_refresh_returns_new_tokens_and_rotates(api, user):
    login = api.post(
        reverse("users:login"),
        {"username": user.username, "password": PASSWORD},
        format="json",
    )
    old_refresh = login.data["refresh"]

    refresh = api.post(reverse("users:token-refresh"), {"refresh": old_refresh}, format="json")

    assert refresh.status_code == status.HTTP_200_OK
    assert "access" in refresh.data
    # Rotation issues a new refresh token and blacklists the old one.
    assert refresh.data["refresh"] != old_refresh

    reuse = api.post(reverse("users:token-refresh"), {"refresh": old_refresh}, format="json")

    assert reuse.status_code == status.HTTP_401_UNAUTHORIZED


def test_me_requires_authentication(api):
    response = api.get(reverse("users:me"))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_me_returns_current_user(auth_api, user):
    response = auth_api.get(reverse("users:me"))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["username"] == user.username
    assert response.data["email"] == user.email
