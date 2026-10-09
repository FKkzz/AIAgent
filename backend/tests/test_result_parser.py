import json

import pytest

from zotero_quick_read.errors import ModelResponseError
from zotero_quick_read.result_parser import (
    apply_ground_truth_coverage,
    normalize_tags,
    parse_result,
)


def valid_result() -> dict:
    return {
        "schema_version": "1.0",
        "title": "Test paper",
        "paper_type": "experimental",
        "one_sentence_summary": "该工作采用时间分辨测量研究样品中的超快自旋动力学。",
        "methods_and_conditions": [
            {
                "statement": "在低温下使用泵浦探测测量。",
                "evidence_kind": "measured",
                "locations": ["PDF p.2"],
            }
        ],
        "main_conclusions": [
            {
                "statement": "信号显示两个时间尺度。",
                "evidence_kind": "measured",
                "locations": ["Fig. 2, PDF p.3"],
            },
            {
                "statement": "作者用速率方程解释较慢分量。",
                "evidence_kind": "model",
                "locations": ["Sec. IV, PDF p.4"],
            },
        ],
        "physical_picture": "初始态受到光激发后发生弛豫，最终由旋转信号读出。",
        "novelty_and_significance": [
            {
                "statement": "作者认为该测量扩展了可访问的时间尺度。",
                "evidence_kind": "author_inference",
                "locations": ["Discussion, PDF p.5"],
            }
        ],
        "tags": [
            {
                "canonical": "time-resolved Kerr rotation",
                "category": "method",
                "rationale": "核心方法",
                "synonyms": [],
            },
            {
                "canonical": "TRKR",
                "category": "method",
                "rationale": "重复同义词",
                "synonyms": [],
            },
        ],
        "coverage": {
            "pages_read": [1],
            "total_pages": 1,
            "mode": "full_text",
            "supplementary_material_read": False,
            "omitted_ranges": [],
            "warnings": [],
        },
        "limitations": [],
    }


def test_json_fence_and_vocabulary_normalization():
    result = parse_result("```json\n" + json.dumps(valid_result(), ensure_ascii=False) + "\n```")
    result = normalize_tags(result, 8)
    assert [tag.canonical for tag in result.tags] == ["TRKR"]


def test_actual_coverage_overrides_model_claim():
    result = parse_result(json.dumps(valid_result(), ensure_ascii=False))
    result = apply_ground_truth_coverage(
        result,
        pages_read=[1, 2, 3, 4, 5],
        total_pages=5,
        warnings=["page warning"],
        abstract_mode=False,
    )
    assert result.coverage.pages_read == [1, 2, 3, 4, 5]
    assert "page warning" in result.coverage.warnings


def test_impossible_page_reference_fails():
    data = valid_result()
    data["main_conclusions"][0]["locations"] = ["PDF p.99"]
    result = parse_result(json.dumps(data, ensure_ascii=False))
    with pytest.raises(ModelResponseError) as caught:
        apply_ground_truth_coverage(
            result,
            pages_read=[1, 2],
            total_pages=2,
            warnings=[],
            abstract_mode=False,
        )
    assert caught.value.code == "invalid_source_location"
