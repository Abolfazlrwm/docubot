from django.conf import settings
from rest_framework import serializers

from apps.chat.models import Conversation, Message
from apps.chat.retrieval import MODES, RetrievedChunk


class SearchRequestSerializer(serializers.Serializer):
    query = serializers.CharField()
    top_k = serializers.IntegerField(required=False, min_value=1)
    mode = serializers.ChoiceField(choices=MODES, required=False, allow_null=True)
    document_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True
    )

    def validate_query(self, value: str) -> str:
        # Read at validation time so tests and ops can tune the setting without
        # a process restart (a class-level max_length would freeze it at import).
        max_length = settings.QUERY_MAX_LENGTH
        if len(value) > max_length:
            raise serializers.ValidationError(
                f"Query is too long ({len(value)} characters). Maximum is {max_length}."
            )
        return value

    def validate_top_k(self, value: int | None) -> int | None:
        if value is not None and value > settings.RETRIEVAL_MAX_TOP_K:
            raise serializers.ValidationError(
                f"top_k may not exceed {settings.RETRIEVAL_MAX_TOP_K}."
            )
        return value


class SearchResultSerializer(serializers.Serializer):
    document_id = serializers.IntegerField()
    document_title = serializers.CharField()
    chunk_index = serializers.IntegerField()
    page = serializers.IntegerField(allow_null=True)
    content = serializers.CharField()
    score = serializers.FloatField()
    match = serializers.ChoiceField(choices=("vector", "keyword", "both"))

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
            "page": retrieved.page,
            "content": retrieved.content,
            "score": retrieved.score,
            "match": retrieved.match,
        }


class ConversationSerializer(serializers.ModelSerializer):
    message_count = serializers.IntegerField(read_only=True)  # from a queryset annotation

    class Meta:
        model = Conversation
        fields = ("id", "title", "message_count", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")


class MessageSerializer(serializers.ModelSerializer):
    # The sources the answer actually cited, resolved from the [n] markers in
    # the content — not just everything that was in the context window.
    citations = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = (
            "id",
            "role",
            "content",
            "sources",
            "citations",
            "model",
            "prompt_tokens",
            "completion_tokens",
            "created_at",
        )

    def get_citations(self, message: Message) -> list[dict]:
        from apps.chat.services import extract_citations  # local import: services imports models

        if message.role != Message.Role.ASSISTANT:
            return []
        return extract_citations(message.content, message.sources)


class AskSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=2000)
    mode = serializers.ChoiceField(choices=MODES, required=False, allow_null=True)
    document_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True
    )
