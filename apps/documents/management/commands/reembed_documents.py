"""Re-embed all ready documents with the currently configured embedding provider.

Run after changing EMBEDDING_PROVIDER, EMBEDDING_MODEL, or any embedding input
convention (e.g. query/passage prefixes): old vectors are inconsistent with
new ones until every document is reprocessed.
"""

from django.core.management.base import BaseCommand

from apps.documents.models import Document
from apps.documents.services import _enqueue_processing


class Command(BaseCommand):
    help = (
        "Reset every ready document to pending and re-queue processing, so all "
        "chunks are re-created with the currently configured embedding provider."
    )

    def handle(self, *args, **options):
        document_ids = list(
            Document.objects.filter(status=Document.Status.READY).values_list("id", flat=True)
        )
        for document in Document.objects.filter(id__in=document_ids):
            document.status = Document.Status.PENDING
            document.error_message = ""
            document.processing_started_at = None
            document.save(update_fields=["status", "error_message", "processing_started_at", "updated_at"])
        for document_id in document_ids:
            _enqueue_processing(document_id)
        self.stdout.write(self.style.SUCCESS(f"Re-queued {len(document_ids)} document(s) for re-embedding."))
