import uuid

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import models
from django.db.models import GeneratedField
from pgvector.django import HnswIndex, VectorField


def document_upload_to(instance: "Document", filename: str) -> str:
    """Place files under a per-owner directory with a uuid prefix, so paths are
    unique and never leak the original layout of the client's filesystem."""
    return f"documents/{instance.owner_id}/{uuid.uuid4().hex}_{filename}"


class Document(models.Model):
    """A user-uploaded file awaiting processing into the knowledge base.

    Phase 1 only stores the upload; background processing (extraction,
    embeddings) arrives in a later phase and flips ``status``.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to=document_upload_to)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, blank=True, default="")
    size_bytes = models.PositiveBigIntegerField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    # Set when a worker claims the document. A "processing" status whose
    # started_at is older than PROCESSING_STALE_AFTER_SECONDS belongs to a
    # dead worker (acks_late will redeliver) and may be re-claimed.
    processing_started_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"


class Chunk(models.Model):
    """One piece of a document's extracted text, with its embedding vector."""

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    chunk_index = models.PositiveIntegerField()
    # 1-based source page (PDF page number; TXT files get 1). Null marks chunks
    # created before page tracking existed — old rows keep working untouched.
    page = models.PositiveIntegerField(null=True, blank=True)
    content = models.TextField()
    embedding = VectorField(dimensions=settings.EMBEDDING_DIMENSIONS)
    # Stored generated column: the database keeps this in sync with `content`
    # on every write, so the keyword signal can never drift from the text.
    search = GeneratedField(
        expression=SearchVector("content", config=settings.FTS_CONFIG),
        output_field=SearchVectorField(),
        db_persist=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["chunk_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "chunk_index"],
                name="unique_chunk_index_per_document",
            ),
        ]
        indexes = [
            models.Index(fields=["document"]),
            # ANN index; cosine distance everywhere. No index = sequential scan.
            HnswIndex(
                name="chunk_embedding_hnsw",
                fields=["embedding"],
                opclasses=["vector_cosine_ops"],
                m=16,
                ef_construction=64,
            ),
            # Inverted index backing the keyword (FTS) retrieval signal.
            GinIndex(fields=["search"], name="chunk_search_gin"),
        ]

    def __str__(self) -> str:
        return f"document={self.document_id} chunk #{self.chunk_index}"
