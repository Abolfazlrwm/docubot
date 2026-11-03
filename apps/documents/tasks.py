"""Celery tasks. Thin wrappers around service functions only."""

from apps.common.exceptions import TransientProcessingError
from apps.documents.services import mark_document_failed, process_document
from config.celery import app as celery_app


class ProcessDocumentTask(celery_app.Task):
    """Marks the document failed once the task gives up (retries exhausted)."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        document_id = args[0] if args else kwargs.get("document_id")
        if document_id is None:
            return
        mark_document_failed(document_id, f"Processing failed after exhausting retries: {exc}")


@celery_app.task(
    base=ProcessDocumentTask,
    autoretry_for=(TransientProcessingError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def process_document_task(document_id: int) -> None:
    """Extract, chunk and embed one document in the background."""
    process_document(document_id)
