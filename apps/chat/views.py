from django.conf import settings
from django.db.models import Count
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, mixins, status, viewsets
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.chat import retrieval, services
from apps.chat.models import Conversation, Message
from apps.chat.serializers import (
    AskSerializer,
    ConversationSerializer,
    MessageSerializer,
    SearchRequestSerializer,
    SearchResultSerializer,
)
from apps.common.ai.llm import LLMPermanentError, LLMTransientError
from apps.common.throttling import WriteMethodsRateThrottle


class SearchView(APIView):
    """Semantic search over the authenticated user's ready documents."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "search"

    @extend_schema(
        tags=["search"],
        summary="Semantic search in my documents",
        request=SearchRequestSerializer,
        responses=SearchResultSerializer(many=True),
    )
    def post(self, request):
        request_serializer = SearchRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        data = request_serializer.validated_data

        results = retrieval.retrieve(
            request.user,
            data["query"],
            top_k=data.get("top_k"),
            document_ids=data.get("document_ids"),
            mode=data.get("mode") or settings.SEARCH_MODE,
        )
        output = SearchResultSerializer(
            [SearchResultSerializer.from_retrieved(result) for result in results], many=True
        )
        return Response(output.data, status=status.HTTP_200_OK)


@extend_schema_view(
    list=extend_schema(summary="List my conversations", tags=["chat"]),
    create=extend_schema(summary="Create a conversation", tags=["chat"]),
    retrieve=extend_schema(summary="Get one of my conversations", tags=["chat"]),
    destroy=extend_schema(summary="Delete one of my conversations", tags=["chat"]),
)
class ConversationViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = ConversationSerializer

    def get_queryset(self):
        # The Count annotation disables Meta.ordering, so order explicitly:
        # stable pagination and least-recently-active conversations sink.
        return (
            Conversation.objects.filter(owner=self.request.user)
            .annotate(message_count=Count("messages"))
            .order_by("-updated_at", "-id")
        )

    def perform_create(self, serializer):
        # The owner always comes from the authenticated request, never the payload.
        serializer.save(owner=self.request.user)


@extend_schema_view(
    list=extend_schema(summary="List a conversation's messages", tags=["chat"]),
    create=extend_schema(
        summary="Ask a question in a conversation",
        description="Creates the user message, runs RAG and returns the assistant message.",
        request=AskSerializer,
        responses=MessageSerializer,
        tags=["chat"],
    ),
)
class MessageListCreateView(generics.ListCreateAPIView):
    """GET: chronological history. POST: ask a question, get the assistant reply."""

    throttle_classes = [WriteMethodsRateThrottle]
    throttle_scope = "chat"

    def get_serializer_class(self):
        return AskSerializer if self.request.method == "POST" else MessageSerializer

    def get_conversation(self) -> Conversation:
        # Owner-scoped: another user's conversation id 404s like a nonexistent one.
        return get_object_or_404(
            Conversation, pk=self.kwargs["conversation_pk"], owner=self.request.user
        )

    def get_queryset(self):
        return Message.objects.filter(conversation=self.get_conversation()).order_by(
            "created_at", "id"
        )

    def create(self, request, *args, **kwargs):
        conversation = self.get_conversation()
        input_serializer = AskSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        try:
            assistant_message = services.ask_question(
                request.user,
                conversation,
                input_serializer.validated_data["content"],
                document_ids=input_serializer.validated_data.get("document_ids"),
                mode=input_serializer.validated_data.get("mode"),
            )
        except LLMTransientError as exc:
            return Response(
                {"detail": f"The AI service is temporarily unavailable; please retry. ({exc})"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except LLMPermanentError as exc:
            return Response(
                {"detail": f"The AI service rejected the request. ({exc})"},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        output = MessageSerializer(assistant_message)
        headers = self.get_success_headers(output.data)
        return Response(output.data, status=status.HTTP_201_CREATED, headers=headers)


@extend_schema(
    summary="Ask a question and stream the answer (Server-Sent Events)",
    description=(
        "Same RAG turn as the JSON endpoint, but the answer arrives as an "
        "event stream: `sources`, repeated `delta` events, then `done`. "
        "A failing LLM produces an `error` event; the turn is then not persisted."
    ),
    request=AskSerializer,
    responses={(200, "text/event-stream"): MessageSerializer},
    tags=["chat"],
)
class MessageStreamView(APIView):
    """POST: ask a question, stream the answer as text/event-stream."""

    throttle_classes = [WriteMethodsRateThrottle]
    throttle_scope = "chat"

    def post(self, request, conversation_pk: int):
        conversation = get_object_or_404(Conversation, pk=conversation_pk, owner=request.user)
        input_serializer = AskSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        generator = services.stream_answer(
            request.user,
            conversation,
            data["content"],
            document_ids=data.get("document_ids"),
            mode=data.get("mode"),
        )
        return StreamingHttpResponse(
            generator,
            content_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # nginx: do not buffer the stream
            },
        )
