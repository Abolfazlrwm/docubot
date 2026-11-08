"""Personal API key management endpoints."""

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.apikeys.models import APIKey
from apps.apikeys.serializers import APIKeyCreateSerializer, APIKeySerializer


@extend_schema_view(
    list=extend_schema(summary="List my API keys (masked)", tags=["keys"]),
    create=extend_schema(
        summary="Create an API key",
        description=(
            "Returns the raw key exactly once; only its SHA-256 hash is stored. "
            "scope=read may only issue safe methods, scope=write can do everything."
        ),
        request=APIKeyCreateSerializer,
        tags=["keys"],
    ),
)
class APIKeyListCreateView(generics.ListCreateAPIView):
    serializer_class = APIKeySerializer

    def get_queryset(self):
        return APIKey.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.request.method == "POST":
            return APIKeyCreateSerializer
        return APIKeySerializer

    def create(self, request, *args, **kwargs):
        serializer = APIKeyCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key, raw = APIKey.generate(
            user=request.user,
            name=serializer.validated_data["name"],
            scope=serializer.validated_data["scope"],
        )
        output = APIKeySerializer(key)
        return Response({**output.data, "key": raw}, status=status.HTTP_201_CREATED)


@extend_schema(summary="Revoke an API key (idempotent, owner-scoped)", tags=["keys"])
class APIKeyRevokeView(APIView):
    def post(self, request, pk):
        # Owner-scoped: another user's key 404s like a nonexistent one.
        key = generics.get_object_or_404(APIKey, pk=pk, user=request.user)
        if key.revoked_at is None:
            key.revoke()
        return Response(APIKeySerializer(key).data)
