"""Validation rules for document uploads. Pure functions, no I/O side effects."""

import os
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import File

ALLOWED_EXTENSIONS = {".pdf", ".txt"}
_PDF_MAGIC = b"%PDF-"


def _human_size(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.0f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes} bytes"


def validate_extension(uploaded_file: File) -> None:
    extension = Path(uploaded_file.name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f"Unsupported file extension '{extension or 'none'}'. "
            f"Allowed extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}."
        )


def validate_size(uploaded_file: File, max_size: int) -> None:
    if uploaded_file.size > max_size:
        raise ValidationError(
            f"File is too large ({_human_size(uploaded_file.size)}). "
            f"Maximum allowed size is {_human_size(max_size)}."
        )


def validate_pdf_content(uploaded_file: File) -> None:
    uploaded_file.seek(0)
    header = uploaded_file.read(len(_PDF_MAGIC))
    uploaded_file.seek(0)
    if header != _PDF_MAGIC:
        raise ValidationError("File is not a valid PDF (missing %PDF- header).")


def validate_txt_content(uploaded_file: File) -> None:
    uploaded_file.seek(0)
    try:
        uploaded_file.read().decode("utf-8")
    except UnicodeDecodeError:
        raise ValidationError("File is not valid UTF-8 text.") from None
    finally:
        uploaded_file.seek(0)


def validate_document_file(uploaded_file: File, max_size: int | None = None) -> None:
    """Run every upload rule; raises ValidationError with user-facing messages.

    Checks the extension first (cheap), then the size, then the real content —
    extensions and content types are client-controlled and easy to fake.
    """
    max_size = max_size if max_size is not None else settings.MAX_UPLOAD_SIZE
    validate_extension(uploaded_file)
    validate_size(uploaded_file, max_size)
    extension = Path(uploaded_file.name).suffix.lower()
    if extension == ".pdf":
        validate_pdf_content(uploaded_file)
    else:
        validate_txt_content(uploaded_file)


def strip_unsafe_basename(filename: str) -> str:
    return os.path.basename(filename.replace("\\", "/"))
