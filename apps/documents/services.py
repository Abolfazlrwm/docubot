"""Business logic for documents."""

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from apps.common.ai.embeddings import embed_texts, get_embedding_provider
from apps.common.exceptions import PermanentProcessingError, TransientProcessingError
from apps.documents.chunking import chunk_text
from apps.documents.extraction import extract_text
from apps.documents.models import Chunk, Document
from apps.documents.validators import strip_unsafe_basename, validate_document_file

ERROR_MESSAGE_MAX_LENGTH = 1000


def create_document(*, owner, uploaded_file: File, title: str | None = None) -> Document:
    """Validate an upload, persist it, and queue background processing.

    The task is only enqueued after the database transaction commits, so the
    worker never sees an uncommitted row.
    """
    validate_document_file(uploaded_file, max_size=settings.MAX_UPLOAD_SIZE)
    original_filename = strip_unsafe_basename(uploaded_file.name)
    document = Document.objects.create(
        owner=owner,
        title=title or original_filename,
        file=uploaded_file,
        original_filename=original_filename,
        content_type=getattr(uploaded_file, "content_type", "") or "",
        size_bytes=uploaded_file.size,
    )
    transaction.on_commit(lambda: _enqueue_processing(document.pk))
    return document


def delete_document(document: Document) -> None:
    """Delete the row, then remove the stored file once the transaction commits.

    If the delete rolled back after the file was removed, the database would
    point at a missing file, so the storage cleanup is deferred to on_commit.
    """
    file_name = document.file.name
    with transaction.atomic():
        document.delete()
    transaction.on_commit(lambda: default_storage.delete(file_name))


def _enqueue_processing(document_id: int) -> None:
    from apps.documents.tasks import process_document_task  # local import: avoids a cycle

    process_document_task.delay(document_id)


def process_document(document_id: int) -> None:
    """Extract, chunk and embed one document: pending -> ready (or failed).

    Safe to run repeatedly: a document already ready or claimed by another
    worker is skipped, and chunk creation always replaces existing chunks.
    """
    document = _claim_document(document_id)
    if document is None:
        return
    try:
        _run_processing_pipeline(document)
    except PermanentProcessingError as exc:
        mark_document_failed(document_id, str(exc) or "Processing failed.")
    except TransientProcessingError:
        # Release the claim so the queued retry can start over.
        _release_document(document_id)
        raise


def _claim_document(document_id: int) -> Document | None:
    """Lock the row and move pending/failed -> processing; None means skip.

    A "processing" document may be re-claimed when its claim is stale — i.e.
    its worker died before finishing (acks_late redelivers the task), which is
    detectable via processing_started_at.
    """
    with transaction.atomic():
        document = Document.objects.select_for_update().filter(pk=document_id).first()
        if document is None:
            return None
        if document.status == Document.Status.READY:
            return None
        if document.status == Document.Status.PROCESSING and not _claim_is_stale(document):
            return None
        document.status = Document.Status.PROCESSING
        document.processing_started_at = timezone.now()
        document.save(update_fields=["status", "processing_started_at", "updated_at"])
    return document


def _claim_is_stale(document: Document) -> bool:
    started_at = document.processing_started_at
    if started_at is None:  # claim record lost (legacy rows, manual edits)
        return True
    stale_after = settings.PROCESSING_STALE_AFTER_SECONDS
    return (timezone.now() - started_at).total_seconds() > stale_after


def _release_document(document_id: int) -> None:
    """Return a processing document to pending (a retry will re-claim it)."""
    with transaction.atomic():
        document = Document.objects.select_for_update().filter(pk=document_id).first()
        if document and document.status == Document.Status.PROCESSING:
            document.status = Document.Status.PENDING
            document.processing_started_at = None
            document.save(update_fields=["status", "processing_started_at", "updated_at"])


def mark_document_failed(document_id: int, message: str) -> None:
    """Set status=failed with a truncated message; never overwrites a ready doc."""
    with transaction.atomic():
        document = Document.objects.select_for_update().filter(pk=document_id).first()
        if document is None or document.status == Document.Status.READY:
            return
        document.status = Document.Status.FAILED
        document.error_message = message[:ERROR_MESSAGE_MAX_LENGTH]
        document.save(update_fields=["status", "error_message", "updated_at"])


def reprocess_document(document: Document) -> None:
    """Reset a failed document to pending and queue processing again."""
    with transaction.atomic():
        document.status = Document.Status.PENDING
        document.error_message = ""
        document.save(update_fields=["status", "error_message", "updated_at"])
        transaction.on_commit(lambda: _enqueue_processing(document.pk))


def _run_processing_pipeline(document: Document) -> None:
    text = extract_text(document)
    chunks = chunk_text(text)
    if not chunks:
        raise PermanentProcessingError("No extractable text found, scanned PDFs are not supported.")
    vectors = embed_texts(get_embedding_provider(), chunks)
    # Replace-and-create in one transaction: re-running can never duplicate
    # chunks, and the unique (document, chunk_index) constraint is the backstop.
    with transaction.atomic():
        document.chunks.all().delete()
        Chunk.objects.bulk_create(
            Chunk(document=document, chunk_index=index, content=content, embedding=vector)
            for index, (content, vector) in enumerate(zip(chunks, vectors, strict=True))
        )
        document.status = Document.Status.READY
        document.error_message = ""
        document.save(update_fields=["status", "error_message", "updated_at"])
