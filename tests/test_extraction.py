import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.common.exceptions import PermanentProcessingError
from apps.documents.extraction import extract_text, normalize_whitespace
from tests.factories import DocumentFactory
from tests.pdf import build_pdf

pytestmark = pytest.mark.django_db


def make_document(content: bytes, name: str) -> object:
    return DocumentFactory(
        file=SimpleUploadedFile(name, content, content_type="application/octet-stream")
    )


def test_normalize_whitespace_collapses_and_strips_nulls():
    assert normalize_whitespace("  a \t b  \x00 c \r\n d \n\n\n\n e ") == "a b c\nd\n\ne"


def test_extract_txt():
    document = make_document(b"  hello   world \n\n again ", "a.txt")

    assert extract_text(document) == "hello world\n\nagain"


def test_extract_txt_invalid_utf8_is_permanent():
    document = make_document(b"\xff\xfe\x00", "a.txt")

    with pytest.raises(PermanentProcessingError, match="UTF-8"):
        extract_text(document)


def test_extract_pdf():
    document = make_document(build_pdf("Hello PDF extraction"), "a.pdf")

    assert extract_text(document) == "Hello PDF extraction"


def test_extract_pdf_without_text_layer_is_permanent():
    document = make_document(build_pdf(None), "scanned.pdf")

    with pytest.raises(PermanentProcessingError, match="scanned PDFs"):
        extract_text(document)


def test_extract_corrupt_pdf_is_permanent():
    document = make_document(b"%PDF-1.4 this is not a real pdf body at all", "bad.pdf")

    with pytest.raises(PermanentProcessingError, match="corrupt or unreadable"):
        extract_text(document)


def test_extract_whitespace_only_txt_is_permanent():
    document = make_document(b"   \n\t  ", "empty.txt")

    with pytest.raises(PermanentProcessingError, match="scanned PDFs"):
        extract_text(document)


def test_extract_missing_file_is_permanent():
    document = make_document(b"hello", "a.txt")
    document.file.delete(save=False)

    with pytest.raises(PermanentProcessingError, match="could not be read"):
        extract_text(document)
