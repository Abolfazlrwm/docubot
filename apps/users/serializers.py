from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from apps.users.models import User
from apps.users.services import register_user


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "email", "date_joined")
        read_only_fields = ("id", "username", "email", "date_joined")


class RegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        validators=[
            UniqueValidator(
                queryset=User.objects.all(),
                lookup="iexact",
                message="A user with this email already exists.",
            )
        ]
    )
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    class Meta:
        model = User
        fields = ("id", "username", "email", "password", "date_joined")
        read_only_fields = ("id", "date_joined")

    def create(self, validated_data):
        try:
            return register_user(**validated_data)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": exc.messages}) from exc
