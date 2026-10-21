import json
from pathlib import Path

import pymupdf as fitz

from zotero_quick_read.config import AppSettings, PdfSettings
from zotero_quick_read.db import QueueDB
from zotero_quick_read.pipeline import InferenceResult, JobWorker
from zotero_quick_read.prompting import PromptStore, parse_prompt_profile
from zotero_quick_read.schemas import JobSubmit


def create_pdf(path: Path):
    doc = fitz.open()
    for index in range(2):
        page = doc.new_page()
        page.insert_textbox(
            fitz.Rect(72, 72, 540, 760),
            (f"page {index + 1} physics experiment " * 80),
            fontsize=8,
        )
    doc.save(path)
    doc.close()


class FakeClient:
    def respond(self, *, model, instructions, input_content, auth_mode):
        if "必须符合以下 JSON Schema" not in instructions:
            return InferenceResult(
                '{"facts":["[PDF p.1] fact"]}',
                {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            )
        result = {
            "schema_version": "1.0",
            "title": "Fake",
            "paper_type": "experimental",
            "one_sentence_summary": "该实验用光学方法研究一个模型材料并观察到两种动力学过程。",
            "methods_and_conditions": [
                {
                    "statement": "使用泵浦探测方法。",
                    "evidence_kind": "measured",
                    "locations": ["PDF p.1"],
                }
            ],
            "main_conclusions": [
                {
                    "statement": "观察到快速响应。",
                    "evidence_kind": "measured",
                    "locations": ["PDF p.1"],
                },
                {
                    "statement": "模型解释了慢响应。",
                    "evidence_kind": "model",
                    "locations": ["PDF p.2"],
                },
            ],
            "physical_picture": "光激发改变初始态，体系弛豫后由探测信号读出。",
            "novelty_and_significance": [
                {
                    "statement": "作者认为方法提供了新的动力学证据。",
                    "evidence_kind": "author_inference",
                    "locations": ["PDF p.2"],
                }
            ],
            "research_focus_findings": [
                {
                    "statement": "关键结论同时依赖测量结果与模型解释。",
                    "evidence_kind": "model",
                    "locations": ["PDF p.2"],
                }
            ],
            "tags": [
                {
                    "canonical": "超快动力学",
                    "category": "physics",
                    "rationale": "核心问题",
                    "synonyms": [],
                }
            ],
            "coverage": {
                "pages_read": [1, 2],
                "total_pages": 2,
                "mode": "full_text",
                "supplementary_material_read": False,
                "omitted_ranges": [],
                "warnings": [],
            },
            "limitations": [],
        }
        return InferenceResult(
            json.dumps(result, ensure_ascii=False),
            {"input_tokens": 30, "output_tokens": 20, "total_tokens": 50},
            "r1",
        )


def test_worker_only_completes_after_validated_result(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    create_pdf(pdf)
    db = QueueDB(tmp_path / "queue.db")
    settings = AppSettings(
        selected_model="available-model",
        pdf=PdfSettings(stable_seconds=0, min_text_chars=50, chunk_chars=5000),
    )
    prompt_profile = PromptStore.builtin(settings.output.prompt_profile)
    job, _ = db.enqueue(
        JobSubmit(
            library_id=1,
            parent_key="ABCDEFGH",
            attachment_key="HGFEDCBA",
            attachment_path=str(pdf),
            title="Fake",
        ),
        instruction_version=prompt_profile.reference,
        model=settings.selected_model,
        auth_mode="chatgpt",
        max_attempts=3,
        fingerprint=None,
    )
    worker = JobWorker(
        db,
        settings,
        lambda: FakeClient(),
        prompt_profile=prompt_profile,
    )
    assert worker.process_one() is True
    completed = db.get(job.id)
    assert completed.status == "completed"
    assert completed.result["tags"] == ["超快动力学"]
    assert "AI 速读" in completed.result["note_html"]
    assert completed.result["processing"]["total_pages"] == 2
    assert completed.result["processing"]["prompt_profile"]["id"] == "physics-zh-v1"
    assert completed.result["processing"]["elapsed_seconds"] >= 0
    note_html = completed.result["note_html"]
    usage = completed.result["processing"]["usage"]
    assert f"请求 {usage['requests']} 次" in note_html
    assert f"输入 {usage['totals']['input_tokens']:,}" in note_html
    assert f"输出 {usage['totals']['output_tokens']:,}" in note_html
    assert f"总计 {usage['totals']['total_tokens']:,}" in note_html
    assert "处理耗时" in note_html
    assert "生成时间" in note_html
    assert "文件指纹" not in note_html
    assert "physics-zh-v1" not in note_html
    assert "per_request" not in note_html
    assert "综合阅读重点" in note_html


def test_worker_uses_the_prompt_snapshot_attached_to_the_job(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    create_pdf(pdf)
    db = QueueDB(tmp_path / "queue.db")
    settings = AppSettings(
        selected_model="available-model",
        pdf=PdfSettings(stable_seconds=0, min_text_chars=50, chunk_chars=5000),
    )
    default_profile = PromptStore.builtin("physics-zh-v1")
    selected_template = PromptStore.builtin("method-reproduction-zh-v1")
    selected_profile = parse_prompt_profile(
        selected_template.raw_text.replace(
            "\n---\n",
            "\ntarget_min_chinese_chars: 1800\n"
            "target_max_chinese_chars: 3000\n---\n",
            1,
        ),
        source="test",
        path="method-deep.md",
    )
    job, _ = db.enqueue(
        JobSubmit(
            library_id=1,
            parent_key="ABCDEFGH",
            attachment_key="HGFEDCBA",
            attachment_path=str(pdf),
            title="Fake",
        ),
        instruction_version=selected_profile.reference,
        model=settings.selected_model,
        auth_mode="chatgpt",
        max_attempts=3,
        fingerprint=None,
        prompt_profile=selected_profile.as_dict(include_content=True),
    )
    worker = JobWorker(
        db,
        settings,
        lambda: FakeClient(),
        prompt_profile=default_profile,
    )

    assert worker.process_one() is True
    completed = db.get(job.id)
    assert completed.result["processing"]["prompt_profile"]["id"] == (
        "method-reproduction-zh-v1"
    )
    assert completed.result["processing"]["target_chinese_chars"] == {
        "min": 1800,
        "max": 3000,
        "source": "prompt_profile",
    }
    assert "复现与实验设计重点" in completed.result["note_html"]
    assert "综合阅读重点" not in completed.result["note_html"]
