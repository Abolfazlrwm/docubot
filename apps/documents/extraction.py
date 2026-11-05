"""Text extraction from stored documents."""

import contextlib
import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from apps.common.exceptions import PermanentProcessingError

_SPACES = re.compile(r"[ \t\f\v]+")
_SPACES_AROUND_NEWLINES = re.compile(r" *\n *")
_CARRIAGE_RETURNS = re.compile(r"\r\n?")
_EXCESS_NEWLINES = re.compile(r"\n{3,}")


def normalize_whitespace(text: str) -> str:
    """Collapse space runs, drop null bytes and \r, cap blank-line runs.

    Newlines are preserved (paragraph structure feeds the chunker) but runs of
    three or more collapse to a single blank line.
    """
    text = text.replace("\x00", "")
    text = _CARRIAGE_RETURNS.sub("\n", text)
    text = _SPACES.sub(" ", text)
    text = _SPACES_AROUND_NEWLINES.sub("\n", text)
    text = _EXCESS_NEWLINES.sub("\n\n", text)
    return text.strip()


def extract_text(document) -> str:
    """Return normalized whole-document text (pages joined with newlines)."""
    return "\n".join(extract_pages(document))


def extract_pages(document) -> list[str]:
    """Return normalized text per page: one entry per PDF page, one for TXT.

    Page boundaries are what citations and deep links need — chunking runs per
    page so every chunk can name the page it came from. Blank pages are
    dropped; a document with no extractable text raises.
    """
    data = _read_file(document)
    if document.original_filename.lower().endswith(".pdf"):
        pages = _extract_pdf_pages(data)
    else:
        pages = [_decode_utf8(data)]
    pages = [page for page in (normalize_whitespace(page) for page in pages) if page]
    if not pages:
        raise PermanentProcessingError("No extractable text found, scanned PDFs are not supported.")
    return pages


def _extract_pdf_pages(data: bytes) -> list[str]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise PermanentProcessingError("The PDF is encrypted and cannot be read.")
        return [page.extract_text() or "" for page in reader.pages]
    except PermanentProcessingError:
        raise
    except (PdfReadError, ValueError, KeyError, IndexError, EOFError) as exc:
        # All of these mean pypdf could not parse the bytes: a corrupt file is a
        # permanent condition, retrying will not help.
        raise PermanentProcessingError("The PDF is corrupt or unreadable.") from exc


def _read_file(document) -> bytes:
    try:
        return document.file.read()
    except (OSError, ValueError) as exc:
        # OSError: storage failure; ValueError: the field has no file at all.
        raise PermanentProcessingError("The stored file could not be read.") from exc
    finally:
        with contextlib.suppress(OSError, ValueError):
            document.file.close()


def _extract_pdf(data: bytes) -> str:
    """Whole-document PDF text (kept for callers that ignore page boundaries)."""
    return "\n".join(_extract_pdf_pages(data))


def _decode_utf8(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PermanentProcessingError("The file is not valid UTF-8 text.") from exc
