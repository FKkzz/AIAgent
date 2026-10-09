from __future__ import annotations

import json
import re

from pydantic import ValidationError

from .errors import ModelResponseError
from .prompting import read_vocabulary
from .schemas import Coverage, QuickReadResult, TagSuggestion


def parse_result(text: str) -> QuickReadResult:
    candidate = text.strip().lstrip("\ufeff")
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        raw = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ModelResponseError(
                "invalid_model_json", "模型没有返回可解析的 JSON。"
            ) from None
        try:
            raw = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ModelResponseError(
                "invalid_model_json",
                f"模型 JSON 解析失败（line {exc.lineno}, column {exc.colno}）。",
            ) from exc
    try:
        return QuickReadResult.model_validate(raw)
    except ValidationError as exc:
        fields = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in exc.errors()[:8]
        )
        raise ModelResponseError(
            "invalid_model_schema", f"模型结果未通过结构校验：{fields}"
        ) from exc


def apply_ground_truth_coverage(
    result: QuickReadResult,
    *,
    pages_read: list[int],
    total_pages: int,
    warnings: list[str],
    abstract_mode: bool,
) -> QuickReadResult:
    result.coverage = Coverage(
        pages_read=pages_read or [1],
        total_pages=max(total_pages, 1),
        mode="abstract_only" if abstract_mode else "full_text",
        supplementary_material_read=False,
        omitted_ranges=result.coverage.omitted_ranges,
        warnings=list(dict.fromkeys([*warnings, *result.coverage.warnings])),
    )
    invalid_refs: list[str] = []
    all_points = [
        *result.methods_and_conditions,
        *result.main_conclusions,
        *result.novelty_and_significance,
    ]
    for point in all_points:
        for location in point.locations:
            for match in re.finditer(r"(?:PDF\s*)?p\.?\s*(\d+)", location, re.IGNORECASE):
                if int(match.group(1)) > total_pages:
                    invalid_refs.append(location)
    if invalid_refs:
        raise ModelResponseError(
            "invalid_source_location",
            f"模型引用了不存在的 PDF 页码：{', '.join(dict.fromkeys(invalid_refs))}",
        )
    return result


def normalize_tags(result: QuickReadResult, max_tags: int) -> QuickReadResult:
    vocabulary = read_vocabulary()
    lookup: dict[str, tuple[str, str, list[str]]] = {}
    for term in vocabulary.get("terms", []):
        canonical = term["canonical"]
        category = term.get("category", "other")
        synonyms = term.get("synonyms", [])
        for value in [canonical, *synonyms]:
            lookup[_tag_key(value)] = (canonical, category, synonyms)

    normalized: list[TagSuggestion] = []
    seen: set[str] = set()
    for tag in result.tags:
        mapped = lookup.get(_tag_key(tag.canonical))
        if mapped:
            canonical, category, synonyms = mapped
            tag = TagSuggestion(
                canonical=canonical,
                category=category,
                rationale=tag.rationale,
                synonyms=list(dict.fromkeys([*tag.synonyms, *synonyms]))[:8],
            )
        key = _tag_key(tag.canonical)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(tag)
        if len(normalized) >= max_tags:
            break
    result.tags = normalized
    return result


def _tag_key(value: str) -> str:
    return re.sub(r"[\s_\-/]+", "", value).casefold()
