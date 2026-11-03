from django.conf import settings
from rest_framework import serializers

from apps.chat.models import Conversation, Message
from apps.chat.retrieval import RetrievedChunk


class SearchRequestSerializer(serializers.Serializer):
    query = serializers.CharField(max_length=settings.QUERY_MAX_LENGTH)
    top_k = serializers.IntegerField(
        required=False, min_value=1, max_value=settings.RETRIEVAL_MAX_TOP_K
    )
    document_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True
    )


class SearchResultSerializer(serializers.Serializer):
    document_id = serializers.IntegerField()
    document_title = serializers.CharField()
    chunk_index = serializers.IntegerField()
    content = serializers.CharField()
    score = serializers.FloatField()

    def create(self, validated_data):  # pragma: no cover - read-only view
        raise NotImplementedError

    def update(self, instance, validated_data):  # pragma: no cover - read-only view
        raise NotImplementedError

    @classmethod
    def from_retrieved(cls, retrieved: RetrievedChunk) -> dict:
        return {
            "document_id": retrieved.document_id,
            "document_title": retrieved.document_title,
            "chunk_index": retrieved.chunk_index,
            "content": retrieved.content,
            "score": retrieved.score,
        }


class ConversationSerializer(serializers.ModelSerializer):
    message_count = serializers.IntegerField(read_only=True)  # from a queryset annotation

    class Meta:
        model = Conversation
        fields = ("id", "title", "message_count", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = (
            "id",
            "role",
            "content",
            "sources",
            "model",
            "prompt_tokens",
            "completion_tokens",
            "created_at",
        )


class AskSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=2000)
    document_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True
    )
