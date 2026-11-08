"""Serializers for API key management."""

from rest_framework import serializers

from apps.apikeys.models import APIKey


class APIKeySerializer(serializers.ModelSerializer):
    """Masked key listing — the secret never appears in a response."""

    masked = serializers.CharField(read_only=True)

    class Meta:
        model = APIKey
        fields = [
            "id",
            "name",
            "scope",
            "masked",
            "is_active",
            "created_at",
            "last_used_at",
            "revoked_at",
        ]


class APIKeyCreateSerializer(serializers.Serializer):
    """Payload for creating a key. The raw key comes back in the response."""

    name = serializers.CharField(max_length=100)
    scope = serializers.ChoiceField(choices=APIKey.Scope.choices, default=APIKey.Scope.READ)
