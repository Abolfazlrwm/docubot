from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.documents import services
from apps.documents.models import Document


class DocumentSerializer(serializers.ModelSerializer):
    # write_only: the storage path of the file must never appear in responses.
    file = serializers.FileField(write_only=True)
    title = serializers.CharField(max_length=255, required=False)
    # Populated by an annotation on the view queryset; avoids N+1 queries.
    chunk_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Document
        fields = (
            "id",
            "title",
            "file",
            "original_filename",
            "content_type",
            "size_bytes",
            "status",
            "error_message",
            "chunk_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "original_filename",
            "content_type",
            "size_bytes",
            "status",
            "error_message",
            "created_at",
            "updated_at",
        )

    def create(self, validated_data):
        request = self.context["request"]
        try:
            return services.create_document(
                owner=request.user,
                uploaded_file=validated_data["file"],
                title=validated_data.get("title"),
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"file": exc.messages}) from exc
