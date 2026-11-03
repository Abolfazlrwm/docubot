import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from apps.common.ai.embeddings import FakeEmbeddingProvider
from apps.common.exceptions import TransientProcessingError
from apps.documents.models import Chunk, Document
from apps.documents.services import process_document
from apps.documents.tasks import process_document_task
from tests.factories import DocumentFactory
from tests.pdf import build_pdf

pytestmark = pytest.mark.django_db

LIST_URL = reverse("documents:document-list")

LONG_TEXT = (
    "DocuChat is a knowledge base backend. This paragraph exists so that the "
    "chunker has enough text to produce several chunks. Each sentence carries "
    "some words. The pipeline extracts, chunks and embeds everything. "
) * 12


def detail_url(document_id):
    return reverse("documents:document-detail", args=[document_id])


def reprocess_url(document_id):
    return reverse("documents:document-reprocess", args=[document_id])


def make_document(owner, text=LONG_TEXT, name="doc.txt"):
    return DocumentFactory(
        owner=owner,
        file=SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/plain"),
    )


class TestProcessDocumentService:
    def test_success_path(self, user):
        document = make_document(user)

        process_document(document.pk)

        document.refresh_from_db()
        assert document.status == Document.Status.READY
        assert document.error_message == ""
        chunks = list(document.chunks.order_by("chunk_index"))
        assert len(chunks) > 1
        assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
        assert all(len(chunk.embedding) == 384 for chunk in chunks)
        assert all(chunk.content.strip() for chunk in chunks)

    def test_running_twice_never_duplicates_chunks(self, user):
        document = make_document(user)
        process_document(document.pk)
        first_count = document.chunks.count()

        process_document(document.pk)

        assert document.chunks.count() == first_count
        assert Chunk.objects.filter(document=document).count() == first_count

    def test_reprocessing_replaces_chunks(self, user, settings):
        document = make_document(user)
        process_document(document.pk)
        first_count = document.chunks.count()
        # Force the document back into a claimable state, then re-run with a
        # much smaller chunk size: counts must change but stay unique.
        document.status = Document.Status.FAILED
        document.save(update_fields=["status"])
        settings.CHUNK_SIZE = 100
        settings.CHUNK_OVERLAP = 20

        process_document(document.pk)

        second_count = document.chunks.count()
        assert second_count > first_count
        indexes = list(
            Chunk.objects.filter(document=document).values_list("chunk_index", flat=True)
        )
        assert len(indexes) == len(set(indexes))

    def test_skips_already_ready_document(self, user):
        document = make_document(user, text="tiny text")
        document.status = Document.Status.READY
        document.save(update_fields=["status"])

        process_document(document.pk)

        assert document.chunks.count() == 0  # no work was done
        document.refresh_from_db()
        assert document.status == Document.Status.READY

    def test_skips_document_claimed_by_another_worker(self, user):
        document = make_document(user, text="tiny text")
        document.status = Document.Status.PROCESSING
        document.processing_started_at = timezone.now()
        document.save(update_fields=["status", "processing_started_at"])

        process_document(document.pk)

        assert document.chunks.count() == 0
        document.refresh_from_db()
        assert document.status == Document.Status.PROCESSING

    def test_reclaims_stale_processing_document(self, user, settings):
        document = make_document(user, text="recovered text " * 30)
        document.status = Document.Status.PROCESSING
        # Claimed longer ago than the stale threshold: its worker must have died.
        document.processing_started_at = timezone.now() - timezone.timedelta(
            seconds=settings.PROCESSING_STALE_AFTER_SECONDS + 10
        )
        document.save(update_fields=["status", "processing_started_at"])

        process_document(document.pk)

        document.refresh_from_db()
        assert document.status == Document.Status.READY
        assert document.chunks.count() > 0

    def test_processing_without_started_at_is_treated_as_stale(self, user):
        document = make_document(user, text="legacy claim text " * 30)
        document.status = Document.Status.PROCESSING
        document.processing_started_at = None
        document.save(update_fields=["status"])

        process_document(document.pk)

        document.refresh_from_db()
        assert document.status == Document.Status.READY

    def test_stale_threshold_defaults_to_time_limit_plus_slack(self):
        # Computed at settings import: CELERY_TASK_TIME_LIMIT (300, no env override) + 60.
        from django.conf import settings as django_settings

        assert django_settings.PROCESSING_STALE_AFTER_SECONDS == 360

    def test_missing_document_is_a_noop(self):
        process_document(999_999)  # must not raise

    def test_permanent_error_marks_failed_with_message(self, user):
        document = DocumentFactory(
            owner=user,
            file=SimpleUploadedFile("blank.pdf", build_pdf(None), content_type="application/pdf"),
        )

        process_document(document.pk)

        document.refresh_from_db()
        assert document.status == Document.Status.FAILED
        assert "scanned PDFs" in document.error_message
        assert len(document.error_message) <= 1000


class AlwaysTransientProvider(FakeEmbeddingProvider):
    def embed(self, texts):
        raise TransientProcessingError("network down")


class TestTaskRetries:
    def test_transient_error_releases_claim_and_reraises(self, user, monkeypatch):
        document = make_document(user)
        monkeypatch.setattr(
            "apps.documents.services.get_embedding_provider",
            lambda: AlwaysTransientProvider(dimensions=384),
        )

        with pytest.raises(TransientProcessingError):
            process_document(document.pk)

        # The claim must be released so the queued Celery retry can start over.
        document.refresh_from_db()
        assert document.status == Document.Status.PENDING

    def test_task_is_configured_for_exponential_backoff_retries(self):
        assert process_document_task.autoretry_for == (TransientProcessingError,)
        assert process_document_task.max_retries == 3
        assert process_document_task.retry_backoff
        assert process_document_task.retry_jitter

    def test_retries_exhausted_marks_failed(self, user):
        document = make_document(user, text="tiny text")
        document.status = Document.Status.PROCESSING
        document.save(update_fields=["status"])

        # Celery calls on_failure once the last retry raises; invoke it directly.
        process_document_task.on_failure(
            TransientProcessingError("network down"), "task-id", [document.pk], {}, None
        )

        document.refresh_from_db()
        assert document.status == Document.Status.FAILED
        assert "network down" in document.error_message


class TestProcessingAPI:
    def test_upload_processes_to_ready_eagerly(
        self, auth_api, user, django_capture_on_commit_callbacks
    ):
        text = "این یک متن فارسی برای آزمون است. " * 40 + "Some English tail text."
        payload = {
            "file": SimpleUploadedFile("mixed.txt", text.encode("utf-8"), content_type="text/plain")
        }
        with django_capture_on_commit_callbacks(execute=True):
            response = auth_api.post(LIST_URL, payload, format="multipart")

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["chunk_count"] == 0  # nothing processed yet at create time

        detail = auth_api.get(detail_url(response.data["id"]))
        assert detail.data["status"] == Document.Status.READY
        assert detail.data["chunk_count"] > 0

    def test_reprocess_rejects_non_failed_document(self, auth_api, user):
        document = DocumentFactory(owner=user, status=Document.Status.READY)

        response = auth_api.post(reprocess_url(document.pk))

        assert response.status_code == status.HTTP_409_CONFLICT

    def test_reprocess_failed_document(self, auth_api, user, django_capture_on_commit_callbacks):
        document = make_document(user, text="recoverable text " * 30)
        document.status = Document.Status.FAILED
        document.error_message = "old failure"
        document.save(update_fields=["status", "error_message"])

        with django_capture_on_commit_callbacks(execute=True):
            response = auth_api.post(reprocess_url(document.pk))

        assert response.status_code == status.HTTP_200_OK
        document.refresh_from_db()
        assert document.status == Document.Status.READY
        assert document.error_message == ""
        assert document.chunks.count() > 0

    def test_reprocess_other_users_document_404(self, auth_api):
        document = DocumentFactory(status=Document.Status.FAILED)

        response = auth_api.post(reprocess_url(document.pk))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_reprocess_requires_authentication(self, api, user):
        document = DocumentFactory(owner=user, status=Document.Status.FAILED)

        response = api.post(reprocess_url(document.pk))

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_upload_of_unprocessable_pdf_ends_failed(
        self, auth_api, django_capture_on_commit_callbacks
    ):
        payload = {
            "file": SimpleUploadedFile("blank.pdf", build_pdf(None), content_type="application/pdf")
        }
        with django_capture_on_commit_callbacks(execute=True):
            response = auth_api.post(LIST_URL, payload, format="multipart")

        assert response.status_code == status.HTTP_201_CREATED
        detail = auth_api.get(detail_url(response.data["id"]))
        assert detail.data["status"] == Document.Status.FAILED
        assert "scanned PDFs" in detail.data["error_message"]
