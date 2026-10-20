from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

EvidenceKind = Literal[
    "measured",
    "fitted",
    "model",
    "author_inference",
    "extra_inference",
    "not_reported",
]


class EvidencePoint(BaseModel):
    statement: str = Field(min_length=2, max_length=1200)
    evidence_kind: EvidenceKind
    locations: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("locations")
    @classmethod
    def validate_locations(cls, locations: list[str]) -> list[str]:
        cleaned = []
        for location in locations:
            location = location.strip()
            if location and location not in cleaned:
                cleaned.append(location)
        return cleaned


class TagSuggestion(BaseModel):
    canonical: str = Field(min_length=1, max_length=80)
    category: Literal["material", "method", "physics", "condition", "mechanism", "other"]
    rationale: str = Field(default="", max_length=240)
    synonyms: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("canonical")
    @classmethod
    def clean_tag(cls, value: str) -> str:
        return re.sub(r"\s+", " ", value.strip())


class Coverage(BaseModel):
    pages_read: list[int] = Field(min_length=1)
    total_pages: int = Field(ge=1)
    mode: Literal["full_text", "abstract_only"] = "full_text"
    supplementary_material_read: bool = False
    omitted_ranges: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("pages_read")
    @classmethod
    def normalized_pages(cls, pages: list[int]) -> list[int]:
        pages = sorted(set(pages))
        if pages and pages[0] < 1:
            raise ValueError("PDF pages are 1-based")
        return pages

    @model_validator(mode="after")
    def pages_exist(self):
        if any(page > self.total_pages for page in self.pages_read):
            raise ValueError("pages_read contains a page beyond total_pages")
        return self


class QuickReadResult(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    title: str = Field(min_length=1, max_length=500)
    paper_type: Literal["experimental", "theoretical", "review", "mixed", "unknown"]
    one_sentence_summary: str = Field(min_length=10, max_length=600)
    methods_and_conditions: list[EvidencePoint] = Field(min_length=1, max_length=18)
    main_conclusions: list[EvidencePoint] = Field(min_length=2, max_length=3)
    physical_picture: str = Field(min_length=10, max_length=1800)
    novelty_and_significance: list[EvidencePoint] = Field(min_length=1, max_length=6)
    research_focus_findings: list[EvidencePoint] = Field(
        default_factory=list,
        max_length=12,
        description="Findings requested by the active research-stage prompt profile.",
    )
    tags: list[TagSuggestion] = Field(min_length=1, max_length=8)
    coverage: Coverage
    limitations: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def unique_tags(self):
        seen: set[str] = set()
        unique: list[TagSuggestion] = []
        for tag in self.tags:
            key = tag.canonical.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(tag)
        self.tags = unique
        return self


class JobSubmit(BaseModel):
    library_id: int = Field(ge=0)
    parent_key: str = Field(pattern=r"^[A-Z0-9]{8}$")
    attachment_key: str | None = Field(default=None, pattern=r"^[A-Z0-9]{8}$")
    attachment_path: str = Field(min_length=1, max_length=32767)
    title: str = Field(default="", max_length=1000)
    model: str | None = None
    auth_mode: Literal["chatgpt", "api_key"] | None = None
    force: bool = False
    abstract_mode: bool = False
    abstract: str | None = Field(default=None, max_length=20000)


class JobRecord(BaseModel):
    id: str
    status: Literal[
        "waiting_fulltext",
        "queued",
        "processing",
        "completed",
        "failed",
        "waiting_quota",
        "reauthorization_required",
        "cancelled",
    ]
    library_id: int
    parent_key: str
    attachment_key: str | None = None
    attachment_path: str
    title: str
    fingerprint: str | None = None
    instruction_version: str
    model: str | None = None
    auth_mode: str
    attempts: int
    max_attempts: int
    force_generation: bool
    cancel_requested: bool
    error_code: str | None = None
    message: str | None = None
    result: dict | None = None
    created_at: str
    updated_at: str


def render_note_html(
    result: QuickReadResult,
    *,
    model: str,
    usage: dict | None = None,
    elapsed_seconds: float | None = None,
    focus_heading: str = "当前科研阶段重点",
) -> str:
    """Render validated model data; model-provided HTML is never trusted."""

    def esc(value: str) -> str:
        return html.escape(value, quote=True)

    def refs(point: EvidencePoint) -> str:
        if not point.locations:
            return ""
        return f" <span class=\"zqr-source\">[{esc('; '.join(point.locations))}]</span>"

    def points(items: list[EvidencePoint]) -> str:
        return "".join(
            f"<li>{esc(item.statement)}{refs(item)} "
            f"<small>（{esc(item.evidence_kind)}）</small></li>"
            for item in items
        )

    coverage = result.coverage
    pages = _compress_pages(coverage.pages_read)
    warnings = list(coverage.warnings)
    if not coverage.supplementary_material_read:
        warnings.append("未读取补充材料。")
    if coverage.mode == "abstract_only":
        warnings.insert(0, "摘要模式：此笔记不是全文总结。")
    warnings.extend(result.limitations)
    warning_html = "".join(f"<li>{esc(item)}</li>" for item in dict.fromkeys(warnings))
    tags = "、".join(esc(tag.canonical) for tag in result.tags)
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    usage_summary = _format_usage_summary(usage)
    duration = _format_duration(elapsed_seconds)
    focus_html = ""
    if result.research_focus_findings:
        focus_html = (
            f"<h2>4. {esc(focus_heading)}</h2>"
            f"<ul>{points(result.research_focus_findings)}</ul>"
        )
    return (
        '<div data-zqr-note="1" data-zqr-schema="1.0">'
        "<h1>AI 速读</h1>"
        f"<p><strong>一句话概括：</strong>{esc(result.one_sentence_summary)}</p>"
        "<h2>1. 实验方法与关键条件</h2>"
        f"<ul>{points(result.methods_and_conditions)}</ul>"
        "<h2>2. 主要结论与简洁物理图像</h2>"
        f"<ol>{points(result.main_conclusions)}</ol>"
        f"<p><strong>物理图像：</strong>{esc(result.physical_picture)}</p>"
        "<h2>3. 核心卖点与重要性</h2>"
        f"<ul>{points(result.novelty_and_significance)}</ul>"
        f"{focus_html}"
        f"<p><strong>规范标签：</strong>{tags}</p>"
        f"<p><strong>阅读覆盖：</strong>PDF 页 {esc(pages)} / 共 {coverage.total_pages} 页。</p>"
        f"<ul>{warning_html}</ul>"
        "<hr>"
        f"<p><small>由 Zotero Quick Read 生成；模型 {esc(model)}；"
        f"{esc(usage_summary)}；处理耗时 {esc(duration)}；"
        f"生成时间 {esc(generated)}</small></p>"
        "</div>"
    )


def _format_usage_summary(usage: dict | None) -> str:
    usage = usage if isinstance(usage, dict) else {}
    totals = usage.get("totals")
    totals = totals if isinstance(totals, dict) else {}

    def number(key: str) -> int | None:
        value = totals.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    requests = usage.get("requests")
    request_text = f"请求 {requests:,} 次" if isinstance(requests, int) else "请求次数未记录"
    input_tokens = number("input_tokens")
    output_tokens = number("output_tokens")
    total_tokens = number("total_tokens")
    if None in {input_tokens, output_tokens, total_tokens}:
        return f"{request_text}；Token 未完整记录"
    return (
        f"{request_text}；Token：输入 {input_tokens:,} / "
        f"输出 {output_tokens:,} / 总计 {total_tokens:,}"
    )


def _format_duration(elapsed_seconds: float | None) -> str:
    if not isinstance(elapsed_seconds, (int, float)) or elapsed_seconds < 0:
        return "未记录"
    seconds = int(round(elapsed_seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours} 小时 {minutes} 分 {seconds} 秒"
    if minutes:
        return f"{minutes} 分 {seconds} 秒"
    return f"{seconds} 秒"


def _compress_pages(pages: list[int]) -> str:
    if not pages:
        return "未记录"
    ranges: list[str] = []
    start = previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        ranges.append(str(start) if start == previous else f"{start}–{previous}")
        start = previous = page
    ranges.append(str(start) if start == previous else f"{start}–{previous}")
    return ", ".join(ranges)
