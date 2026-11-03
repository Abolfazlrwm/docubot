"""Pure text chunking: recursive splitting with overlap. No I/O, no Django models."""

from django.conf import settings

PARAGRAPH_SEPARATOR = "\n\n"
LINE_SEPARATOR = "\n"
# Sentence terminators, including Persian/Arabic punctuation (؟ ؛ ۔).
SENTENCE_SEPARATORS = (".", "!", "?", "؟", "۔", "؛")
WORD_SEPARATOR = " "
SEPARATORS = (PARAGRAPH_SEPARATOR, LINE_SEPARATOR, *SENTENCE_SEPARATORS, WORD_SEPARATOR, "")


def chunk_text(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[str]:
    """Split text into chunks of at most chunk_size characters with overlap.

    Splitting prefers natural boundaries in order: paragraphs, lines,
    sentences (incl. Persian/Arabic punctuation), words, then a hard cut.
    """
    chunk_size = chunk_size if chunk_size is not None else settings.CHUNK_SIZE
    overlap = overlap if overlap is not None else settings.CHUNK_OVERLAP
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must satisfy 0 <= overlap < chunk_size")

    text = (text or "").strip()
    if not text:
        return []

    pieces = [piece for piece in _split(text, SEPARATORS, chunk_size) if piece.strip()]
    return _merge_with_overlap(pieces, chunk_size, overlap)


def _split(text: str, separators: tuple[str, ...], chunk_size: int) -> list[str]:
    """Recursively split text into pieces of at most chunk_size characters.

    Each piece keeps its trailing separator so that merged chunks stay readable.
    """
    if not text:
        return []
    separator, remaining = separators[0], separators[1:]
    if not separator:  # hard cut, nothing structural left to try
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]

    parts = text.split(separator)
    pieces: list[str] = []
    for index, part in enumerate(parts):
        piece = part if index == len(parts) - 1 else part + separator
        if not piece.strip():
            continue
        if len(piece) <= chunk_size:
            pieces.append(piece)
        else:
            pieces.extend(_split(piece, remaining, chunk_size))
    return pieces


def _merge_with_overlap(pieces: list[str], chunk_size: int, overlap: int) -> list[str]:
    """Pack pieces into chunks, starting each chunk with the tail of the previous."""
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for piece in pieces:
        while current and length + len(piece) > chunk_size:
            chunk = "".join(current).strip()
            if chunk:
                chunks.append(chunk)
            tail = _tail_for_overlap(current, overlap)
            if len(tail) >= len(current):  # guarantee progress, avoid loops
                tail = []
            current = list(tail)
            length = sum(len(part) for part in current)
        current.append(piece)
        length += len(piece)
    final = "".join(current).strip()
    if final:
        chunks.append(final)
    return chunks


def _tail_for_overlap(pieces: list[str], overlap: int) -> list[str]:
    """Suffix of pieces totalling roughly `overlap` characters."""
    tail: list[str] = []
    total = 0
    for piece in reversed(pieces):
        if total >= overlap:
            break
        tail.insert(0, piece)
        total += len(piece)
    return tail
