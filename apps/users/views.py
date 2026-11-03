from django.contrib.auth import get_user_model
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from apps.users.serializers import RegisterSerializer, UserSerializer

User = get_user_model()


@extend_schema(tags=["auth"], summary="Register a new user")
class RegisterView(generics.CreateAPIView):
    """Create a new user account. No authentication required."""

    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]


@extend_schema(tags=["auth"], summary="Log in and obtain access/refresh tokens")
class LoginView(TokenObtainPairView):
    permission_classes = [permissions.AllowAny]


@extend_schema(tags=["auth"], summary="Refresh an access token")
class RefreshView(TokenRefreshView):
    permission_classes = [permissions.AllowAny]


@extend_schema(tags=["auth"], summary="Get the authenticated user")
class MeView(generics.RetrieveAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user
