import pytest
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status

from apps.documents.models import Document
from apps.documents.validators import _human_size
from tests.factories import DocumentFactory, UserFactory

pytestmark = pytest.mark.django_db

PDF_BYTES = b"%PDF-1.4\n% minimal test pdf\n"
LIST_URL = reverse("documents:document-list")


def detail_url(document_id):
    return reverse("documents:document-detail", args=[document_id])


def upload(
    api, *, name="sample.pdf", content=PDF_BYTES, content_type="application/pdf", title=None
):
    data = {"file": SimpleUploadedFile(name, content, content_type=content_type)}
    if title is not None:
        data["title"] = title
    return api.post(LIST_URL, data, format="multipart")


def test_upload_pdf_ok(auth_api, user):
    response = upload(auth_api)

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["status"] == Document.Status.PENDING
    assert response.data["original_filename"] == "sample.pdf"
    assert response.data["title"] == "sample.pdf"  # title defaults to the filename
    assert response.data["size_bytes"] == len(PDF_BYTES)
    assert response.data["content_type"] == "application/pdf"
    # The storage path must never be exposed.
    assert "file" not in response.data

    document = Document.objects.get(pk=response.data["id"])
    assert document.owner_id == user.id
    assert default_storage.exists(document.file.name)


def test_upload_txt_ok(auth_api):
    response = upload(auth_api, name="notes.txt", content=b"plain text", content_type="text/plain")

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["original_filename"] == "notes.txt"


def test_upload_with_explicit_title(auth_api):
    response = upload(auth_api, title="My Report")

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["title"] == "My Report"


def test_upload_rejects_wrong_extension(auth_api):
    response = upload(auth_api, name="virus.exe", content=b"MZ fake binary")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "extension" in str(response.data)


def test_upload_rejects_fake_pdf(auth_api):
    # Correct extension, but the content lacks the %PDF- magic bytes.
    response = upload(auth_api, name="fake.pdf", content=b"definitely not a pdf")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "PDF" in str(response.data)


def test_upload_rejects_non_utf8_txt(auth_api):
    response = upload(
        auth_api, name="data.txt", content=b"\xff\xfe\x00\x01", content_type="text/plain"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "UTF-8" in str(response.data)


def test_upload_rejects_oversized_file(auth_api, settings):
    settings.MAX_UPLOAD_SIZE = 64
    response = upload(auth_api, name="big.txt", content=b"x" * 65, content_type="text/plain")

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "65 bytes" in str(response.data)


def test_upload_rejects_oversized_file_reports_megabytes(auth_api, settings):
    settings.MAX_UPLOAD_SIZE = 2 * 1024 * 1024
    response = upload(
        auth_api, name="big.txt", content=b"x" * (3 * 1024 * 1024), content_type="text/plain"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "3 MB" in str(response.data)
    assert "2 MB" in str(response.data)


@pytest.mark.parametrize(
    ("num_bytes", "expected"),
    [(512, "512 bytes"), (2048, "2 KB"), (5 * 1024 * 1024, "5 MB")],
)
def test_human_size_formatting(num_bytes, expected):
    assert _human_size(num_bytes) == expected


def test_upload_requires_authentication(api):
    response = upload(api)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_list_requires_authentication(api):
    response = api.get(LIST_URL)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_list_returns_only_own_documents(auth_api, user):
    other = UserFactory()
    mine = DocumentFactory.create_batch(3, owner=user)
    DocumentFactory(owner=other)

    response = auth_api.get(LIST_URL)

    assert response.status_code == status.HTTP_200_OK
    assert response.data["count"] == 3
    returned_ids = {row["id"] for row in response.data["results"]}
    assert returned_ids == {doc.id for doc in mine}


def test_retrieve_own_document(auth_api, user):
    document = DocumentFactory(owner=user)

    response = auth_api.get(detail_url(document.id))

    assert response.status_code == status.HTTP_200_OK
    assert response.data["id"] == document.id
    assert "file" not in response.data
    assert str(document) == f"{document.title} ({document.status})"


def test_retrieve_other_users_document_returns_404(auth_api):
    document = DocumentFactory()  # owned by a different user

    response = auth_api.get(detail_url(document.id))

    assert response.status_code == status.HTTP_404_NOT_FOUND


def test_delete_other_users_document_returns_404(auth_api):
    document = DocumentFactory()

    response = auth_api.delete(detail_url(document.id))

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert Document.objects.filter(pk=document.pk).exists()


def test_delete_removes_row_and_file(auth_api, user, django_capture_on_commit_callbacks):
    document = DocumentFactory(owner=user)
    file_name = document.file.name
    assert default_storage.exists(file_name)

    with django_capture_on_commit_callbacks(execute=True):
        response = auth_api.delete(detail_url(document.id))

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not Document.objects.filter(pk=document.pk).exists()
    assert not default_storage.exists(file_name)


def test_list_is_paginated(auth_api, user):
    DocumentFactory.create_batch(25, owner=user)

    page1 = auth_api.get(LIST_URL)
    assert page1.data["count"] == 25
    assert len(page1.data["results"]) == 20
    assert page1.data["next"] is not None

    page2 = auth_api.get(LIST_URL, {"page": 2})
    assert len(page2.data["results"]) == 5
    assert page2.data["next"] is None


def test_list_filter_by_status(auth_api, user):
    DocumentFactory(owner=user)
    DocumentFactory(owner=user, status=Document.Status.READY)

    response = auth_api.get(LIST_URL, {"status": "ready"})

    assert response.data["count"] == 1
    assert response.data["results"][0]["status"] == Document.Status.READY


def test_list_search_by_title(auth_api, user):
    DocumentFactory(owner=user, title="Quarterly report")
    DocumentFactory(owner=user, title="Meeting notes")

    response = auth_api.get(LIST_URL, {"search": "quarterly"})

    assert response.data["count"] == 1
    assert response.data["results"][0]["title"] == "Quarterly report"


def test_list_ordering_by_size_bytes(auth_api, user):
    DocumentFactory(
        owner=user, file=SimpleUploadedFile("a.txt", b"x" * 30, content_type="text/plain")
    )
    DocumentFactory(
        owner=user, file=SimpleUploadedFile("b.txt", b"x" * 10, content_type="text/plain")
    )
    DocumentFactory(
        owner=user, file=SimpleUploadedFile("c.txt", b"x" * 20, content_type="text/plain")
    )

    ascending = auth_api.get(LIST_URL, {"ordering": "size_bytes"})
    descending = auth_api.get(LIST_URL, {"ordering": "-size_bytes"})

    assert [row["size_bytes"] for row in ascending.data["results"]] == [10, 20, 30]
    assert [row["size_bytes"] for row in descending.data["results"]] == [30, 20, 10]


def test_list_has_no_n1_queries(auth_api, user, django_assert_max_num_queries):
    """The list endpoint must stay at a fixed number of queries regardless of rows."""
    DocumentFactory.create_batch(10, owner=user)

    # 1 query to load the user from the JWT, 1 for the pagination count,
    # 1 for the page of results.
    with django_assert_max_num_queries(3):
        response = auth_api.get(LIST_URL)

    assert response.status_code == status.HTTP_200_OK
    assert len(response.data["results"]) == 10
