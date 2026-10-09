from pathlib import Path

import pymupdf as fitz
import pytest

from zotero_quick_read.config import PdfSettings
from zotero_quick_read.errors import PdfError
from zotero_quick_read.pdf import (
    build_chunks,
    ensure_pdf_ready,
    extract_pdf,
    render_relevant_pages,
)


def make_pdf(path: Path, texts: list[str]) -> None:
    doc = fitz.open()
    for text in texts:
        page = doc.new_page()
        if text:
            page.insert_textbox(fitz.Rect(72, 72, 540, 760), text, fontsize=8)
    doc.save(path)
    doc.close()


def test_extracts_all_pages_with_page_numbers(tmp_path: Path):
    path = tmp_path / "paper.pdf"
    make_pdf(path, ["first page " * 80, "second page " * 80, "third page " * 80])
    settings = PdfSettings(stable_seconds=0, min_text_chars=100, chunk_chars=4000)
    ready = ensure_pdf_ready(path, settings)
    result = extract_pdf(ready, settings)
    chunks = build_chunks(result, settings)
    joined = "\n".join(chunk.text for chunk in chunks)
    assert result.total_pages == 3
    assert result.pages_with_text == [1, 2, 3]
    assert "[PDF p.1]" in joined
    assert "[PDF p.3]" in joined


def test_scanned_pdf_is_explicit_failure(tmp_path: Path):
    path = tmp_path / "scan.pdf"
    make_pdf(path, [""])
    settings = PdfSettings(stable_seconds=0, min_text_chars=10)
    with pytest.raises(PdfError) as caught:
        extract_pdf(path, settings)
    assert caught.value.code == "scanned_or_empty_pdf"


def test_no_silent_truncation_when_chunk_limit_exceeded(tmp_path: Path):
    path = tmp_path / "large.pdf"
    make_pdf(path, [("long text " * 120) for _ in range(15)])
    settings = PdfSettings(
        stable_seconds=0,
        min_text_chars=10,
        chunk_chars=4000,
        max_chunks=2,
    )
    result = extract_pdf(path, settings)
    with pytest.raises(PdfError) as caught:
        build_chunks(result, settings)
    assert caught.value.code == "document_too_large"


def test_renders_bounded_figure_page_as_inline_image(tmp_path: Path):
    path = tmp_path / "figure.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(
        fitz.Rect(72, 72, 540, 250),
        "Figure 1. Experimental signal. " * 30,
        fontsize=8,
    )
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
    pixmap.clear_with(0x336699)
    page.insert_image(fitz.Rect(100, 300, 300, 500), pixmap=pixmap)
    doc.save(path)
    doc.close()
    settings = PdfSettings(
        stable_seconds=0,
        min_text_chars=10,
        render_page_images=True,
        max_image_pages=1,
    )
    extraction = extract_pdf(path, settings)
    rendered = render_relevant_pages(extraction, settings)
    assert [item.number for item in rendered] == [1]
    assert rendered[0].data_url.startswith("data:image/jpeg;base64,")
