from django.contrib import admin

from apps.documents.models import Chunk, Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "status", "size_bytes", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("title", "original_filename", "owner__username")
    autocomplete_fields = ("owner",)
    readonly_fields = (
        "original_filename",
        "content_type",
        "size_bytes",
        "error_message",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)
    date_hierarchy = "created_at"


@admin.register(Chunk)
class ChunkAdmin(admin.ModelAdmin):
    """Read-only: chunks are produced by the processing pipeline, never by hand."""

    list_display = ("document", "chunk_index", "content_excerpt", "created_at")
    list_filter = ("created_at",)
    search_fields = ("content", "document__title")
    raw_id_fields = ("document",)
    readonly_fields = ("document", "chunk_index", "content", "created_at")
    exclude = ("embedding",)  # never render the raw vector

    @admin.display(description="Content")
    def content_excerpt(self, chunk: Chunk) -> str:
        return chunk.content[:80]

    def has_add_permission(self, request):
        return False
