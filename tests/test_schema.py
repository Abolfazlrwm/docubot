import pytest
from django.urls import reverse
from rest_framework import status

pytestmark = pytest.mark.django_db


def test_openapi_schema_is_served_with_jwt_security_scheme(api):
    response = api.get(reverse("schema"), {"format": "json"})

    assert response.status_code == status.HTTP_200_OK
    schema = response.content.decode()
    assert '"openapi"' in schema
    # SimpleJWT must appear as a bearer scheme so "Authorize" works in Swagger UI.
    assert "jwtAuth" in schema
    assert '"bearer"' in schema


def test_swagger_ui_is_served(api):
    response = api.get(reverse("docs"))

    assert response.status_code == status.HTTP_200_OK
