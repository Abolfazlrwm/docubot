from django.db.models import Count
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.documents import services
from apps.documents.models import Document
from apps.documents.serializers import DocumentSerializer


@extend_schema_view(
    list=extend_schema(summary="List my documents", tags=["documents"]),
    create=extend_schema(summary="Upload a document", tags=["documents"]),
    retrieve=extend_schema(summary="Get one of my documents", tags=["documents"]),
    destroy=extend_schema(summary="Delete one of my documents", tags=["documents"]),
)
class DocumentViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Documents owned by the authenticated user. No update actions in this phase."""

    # Used only for schema generation and router introspection; every real
    # request goes through get_queryset(), which scopes by owner.
    queryset = Document.objects.all()
    serializer_class = DocumentSerializer
    filterset_fields = ["status"]
    search_fields = ["title"]
    ordering_fields = ["created_at", "title", "size_bytes"]

    def get_queryset(self):
        # Scope by owner here, not with an object-level permission check: a
        # document that does not belong to the caller then 404s exactly like a
        # nonexistent id, so the API does not reveal which ids exist.
        # order_by is explicit because the Count() annotation disables the
        # model Meta ordering for pagination purposes.
        return (
            Document.objects.filter(owner=self.request.user)
            .annotate(chunk_count=Count("chunks"))
            .order_by("-created_at")
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        document = serializer.save()
        # Re-fetch from the annotated queryset so the response includes chunk_count.
        document = self.get_queryset().get(pk=document.pk)
        output = self.get_serializer(document)
        headers = self.get_success_headers(output.data)
        return Response(output.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_destroy(self, instance):
        services.delete_document(instance)

    @extend_schema(summary="Re-queue processing of a failed document", tags=["documents"])
    @action(detail=True, methods=["post"])
    def reprocess(self, request, *args, **kwargs):
        document = self.get_object()
        if document.status != Document.Status.FAILED:
            return Response(
                {"detail": "Only failed documents can be reprocessed."},
                status=status.HTTP_409_CONFLICT,
            )
        services.reprocess_document(document)
        document = self.get_queryset().get(pk=document.pk)
        return Response(self.get_serializer(document).data)
