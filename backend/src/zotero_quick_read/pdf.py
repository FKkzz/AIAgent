from __future__ import annotations

import base64
import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz

from .config import PdfSettings
from .errors import PdfError


@dataclass(slots=True)
class PdfPage:
    number: int
    text: str
    image_count: int = 0


@dataclass(slots=True)
class PdfExtraction:
    path: Path
    fingerprint: str
    total_pages: int
    pages: list[PdfPage]
    warnings: list[str] = field(default_factory=list)

    @property
    def text_chars(self) -> int:
        return sum(len(page.text) for page in self.pages)

    @property
    def pages_with_text(self) -> list[int]:
        return [page.number for page in self.pages if page.text.strip()]


@dataclass(slots=True)
class TextChunk:
    index: int
    pages: list[int]
    text: str


@dataclass(slots=True)
class RenderedPage:
    number: int
    mime_type: str
    data_url: str


def ensure_pdf_ready(path_value: str | Path, settings: PdfSettings) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        raise PdfError("invalid_pdf_path", "PDF 路径必须是绝对路径。")
    if path.suffix.casefold() != ".pdf":
        raise PdfError("not_a_pdf", "附件不是 PDF 文件。")
    if not path.exists() or not path.is_file():
        raise PdfError("waiting_fulltext", "PDF 尚未下载完成或路径暂时不可读。", retryable=True)
    try:
        first = path.stat()
    except OSError as exc:
        raise PdfError("waiting_fulltext", f"PDF 暂时不可读：{exc}", retryable=True) from exc
    if first.st_size <= 0:
        raise PdfError("waiting_fulltext", "PDF 文件仍为空。", retryable=True)
    if first.st_size > settings.max_file_mb * 1024 * 1024:
        raise PdfError(
            "pdf_too_large",
            f"PDF 为 {first.st_size / 1024 / 1024:.1f} MB，"
            f"超过配置上限 {settings.max_file_mb} MB。",
        )
    if settings.stable_seconds:
        time.sleep(settings.stable_seconds)
        try:
            second = path.stat()
        except OSError as exc:
            raise PdfError(
                "waiting_fulltext", f"PDF 检查期间变得不可读：{exc}", retryable=True
            ) from exc
        if (first.st_size, first.st_mtime_ns) != (second.st_size, second.st_mtime_ns):
            raise PdfError("waiting_fulltext", "PDF 仍在写入，稍后重试。", retryable=True)
    return path.resolve()


def fingerprint_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while block := handle.read(block_size):
                digest.update(block)
    except OSError as exc:
        raise PdfError("pdf_read_failed", f"无法读取 PDF：{exc}", retryable=True) from exc
    return digest.hexdigest()


def extract_pdf(path: Path, settings: PdfSettings) -> PdfExtraction:
    fingerprint = fingerprint_file(path)
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise PdfError("invalid_or_encrypted_pdf", f"无法打开 PDF：{exc}") from exc
    try:
        if document.needs_pass:
            raise PdfError("encrypted_pdf", "PDF 已加密，需要先在 Zotero 外解密。")
        if document.page_count < 1:
            raise PdfError("empty_pdf", "PDF 没有页面。")
        pages: list[PdfPage] = []
        empty_pages: list[int] = []
        for index in range(document.page_count):
            page = document.load_page(index)
            try:
                text = page.get_text("text", sort=True).replace("\x00", "").strip()
            except Exception as exc:
                raise PdfError(
                    "pdf_text_extraction_failed",
                    f"第 {index + 1} 页文本提取失败：{exc}",
                ) from exc
            if not text:
                empty_pages.append(index + 1)
            pages.append(PdfPage(index + 1, text, len(page.get_images(full=True))))
    finally:
        document.close()

    warnings: list[str] = []
    total_text = sum(len(page.text) for page in pages)
    if total_text < settings.min_text_chars:
        raise PdfError(
            "scanned_or_empty_pdf",
            "提取到的文本过少；该文件可能是扫描 PDF。当前版本不会用摘要冒充全文，请先 OCR。",
        )
    if empty_pages:
        warnings.append(f"以下页面未提取到文字，可能依赖扫描图像：{_page_ranges(empty_pages)}。")
    image_heavy = [page.number for page in pages if page.image_count >= 3 and len(page.text) < 1200]
    if image_heavy and not settings.render_page_images:
        warnings.append(
            f"以下页面图像较多且未发送页面图像：{_page_ranges(image_heavy)}；图像依赖结论需人工复核。"
        )
    if _looks_like_supplement(path.name):
        warnings.append("文件名疑似补充材料；请确认选中的附件是否为论文正文。")
    return PdfExtraction(path, fingerprint, len(pages), pages, warnings)


def build_chunks(extraction: PdfExtraction, settings: PdfSettings) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    current_parts: list[str] = []
    current_pages: list[int] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current_parts, current_pages, current_chars
        if current_parts:
            chunks.append(TextChunk(len(chunks) + 1, current_pages, "\n\n".join(current_parts)))
        current_parts = []
        current_pages = []
        current_chars = 0

    for page in extraction.pages:
        page_text = f"[PDF p.{page.number}]\n{page.text}"
        if len(page_text) <= settings.chunk_chars:
            if current_parts and current_chars + len(page_text) + 2 > settings.chunk_chars:
                flush()
            current_parts.append(page_text)
            current_pages.append(page.number)
            current_chars += len(page_text) + 2
            continue

        flush()
        # A single text-heavy page may exceed the budget. Split it, retaining its page marker.
        body = page.text
        stride = max(1000, settings.chunk_chars - 80)
        for offset in range(0, len(body), stride):
            fragment = body[offset : offset + stride]
            chunks.append(
                TextChunk(
                    len(chunks) + 1,
                    [page.number],
                    f"[PDF p.{page.number}, fragment {offset // stride + 1}]\n{fragment}",
                )
            )
    flush()
    if len(chunks) > settings.max_chunks:
        raise PdfError(
            "document_too_large",
            f"全文需要 {len(chunks)} 个分段，超过配置上限 {settings.max_chunks}；未静默截断。",
        )
    return chunks


def render_relevant_pages(
    extraction: PdfExtraction, settings: PdfSettings
) -> list[RenderedPage]:
    """Render a bounded, heuristic set of figure-heavy pages for multimodal models."""

    if not settings.render_page_images or settings.max_image_pages == 0:
        return []
    candidates: list[tuple[int, int]] = []
    caption_pattern = re.compile(r"(?:^|\n)\s*(?:fig(?:ure)?\.?|图)\s*\d+", re.IGNORECASE)
    for page in extraction.pages:
        if page.image_count < 1:
            continue
        caption_hits = len(caption_pattern.findall(page.text))
        score = caption_hits * 10 + min(page.image_count, 9)
        candidates.append((score, page.number))
    selected = [
        number
        for _score, number in sorted(candidates, key=lambda item: (-item[0], item[1]))[
            : settings.max_image_pages
        ]
    ]
    if not selected:
        return []
    try:
        document = fitz.open(extraction.path)
    except Exception as exc:
        raise PdfError("pdf_image_render_failed", f"无法重新打开 PDF 以渲染页面：{exc}") from exc
    rendered: list[RenderedPage] = []
    try:
        for number in sorted(selected):
            page = document.load_page(number - 1)
            try:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False, annots=False)
                encoded = base64.b64encode(
                    pixmap.tobytes("jpeg", jpg_quality=72)
                ).decode("ascii")
            except Exception as exc:
                raise PdfError(
                    "pdf_image_render_failed", f"PDF 第 {number} 页图像渲染失败：{exc}"
                ) from exc
            rendered.append(
                RenderedPage(number, "image/jpeg", f"data:image/jpeg;base64,{encoded}")
            )
    finally:
        document.close()
    return rendered


def _looks_like_supplement(name: str) -> bool:
    lowered = name.casefold()
    return any(marker in lowered for marker in ("supp", "supporting", "si.pdf", "appendix"))


def _page_ranges(pages: list[int]) -> str:
    if not pages:
        return ""
    pages = sorted(set(pages))
    parts: list[str] = []
    start = previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        parts.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = page
    parts.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(parts)
