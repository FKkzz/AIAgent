import json
from pathlib import Path

import pymupdf as fitz

from zotero_quick_read.config import AppSettings, PdfSettings
from zotero_quick_read.db import QueueDB
from zotero_quick_read.pipeline import InferenceResult, JobWorker
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
            return InferenceResult('{"facts":["[PDF p.1] fact"]}', {"input_tokens": 10})
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
        return InferenceResult(json.dumps(result, ensure_ascii=False), {"output_tokens": 20}, "r1")


def test_worker_only_completes_after_validated_result(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    create_pdf(pdf)
    db = QueueDB(tmp_path / "queue.db")
    settings = AppSettings(
        selected_model="available-model",
        pdf=PdfSettings(stable_seconds=0, min_text_chars=50, chunk_chars=5000),
    )
    job, _ = db.enqueue(
        JobSubmit(
            library_id=1,
            parent_key="ABCDEFGH",
            attachment_key="HGFEDCBA",
            attachment_path=str(pdf),
            title="Fake",
        ),
        instruction_version=settings.output.prompt_version,
        model=settings.selected_model,
        auth_mode="chatgpt",
        max_attempts=3,
        fingerprint=None,
    )
    worker = JobWorker(db, settings, lambda: FakeClient())
    assert worker.process_one() is True
    completed = db.get(job.id)
    assert completed.status == "completed"
    assert completed.result["tags"] == ["超快动力学"]
    assert "AI 速读" in completed.result["note_html"]
    assert completed.result["processing"]["total_pages"] == 2
