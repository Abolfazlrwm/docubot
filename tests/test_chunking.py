from itertools import pairwise

from apps.documents.chunking import chunk_text


def test_respects_chunk_size():
    text = " ".join(f"word{i}" for i in range(200))

    chunks = chunk_text(text, chunk_size=100, overlap=20)

    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)


def test_overlap_between_consecutive_chunks():
    sentence = "This numbered sentence number XX carries plenty of padding characters indeed."
    text = " ".join(sentence.replace("XX", str(i)) for i in range(30))

    chunks = chunk_text(text, chunk_size=120, overlap=40)

    assert len(chunks) > 1
    for previous, current in pairwise(chunks):
        assert current[:15] in previous


def test_no_empty_chunks():
    chunks = chunk_text("some text\n\n\n\n   \nmore text", chunk_size=50, overlap=10)

    assert chunks
    assert all(chunk.strip() for chunk in chunks)


def test_very_short_text_is_one_chunk():
    assert chunk_text("hello world", chunk_size=100, overlap=20) == ["hello world"]


def test_empty_text_returns_no_chunks():
    assert chunk_text("   \n\t ", chunk_size=100, overlap=20) == []


def test_text_without_spaces_is_hard_cut():
    chunks = chunk_text("a" * 250, chunk_size=100, overlap=20)

    assert [len(chunk) for chunk in chunks] == [100, 100, 50]


def test_persian_text_chunks_cleanly():
    text = (
        "این یک جملهٔ آزمایشی است که برای بررسی قطعه‌بندی نوشته شده است. "
        "آیا قطعه‌بندی روی نشانه‌های سجاوندی فارسی هم کار می‌کند؟ بله! "
        "پایان متن ۔"
    ) * 20

    chunks = chunk_text(text, chunk_size=150, overlap=30)

    assert len(chunks) > 1
    assert all(len(chunk) <= 150 for chunk in chunks)
    assert all(chunk.strip() for chunk in chunks)
    assert "آیا" in "".join(chunks)


def test_invalid_overlap_rejected():
    import pytest

    with pytest.raises(ValueError):
        chunk_text("some text", chunk_size=100, overlap=100)


def test_defaults_come_from_settings(settings):
    settings.CHUNK_SIZE = 50
    settings.CHUNK_OVERLAP = 10

    chunks = chunk_text(" ".join(f"w{i}" for i in range(50)))

    assert all(len(chunk) <= 50 for chunk in chunks)
