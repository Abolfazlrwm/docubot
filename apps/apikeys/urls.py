from django.urls import path

from apps.apikeys.views import APIKeyListCreateView, APIKeyRevokeView

app_name = "apikeys"

urlpatterns = [
    path("keys/", APIKeyListCreateView.as_view(), name="api-keys"),
    path("keys/<int:pk>/revoke/", APIKeyRevokeView.as_view(), name="api-key-revoke"),
]
