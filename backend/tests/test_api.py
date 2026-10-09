from pathlib import Path

from fastapi.testclient import TestClient

from zotero_quick_read.api import create_app
from zotero_quick_read.runtime import build_runtime


def client_for(tmp_path: Path):
    runtime = build_runtime(tmp_path)
    app = create_app(runtime=runtime, start_worker=False, allow_test_client=True)
    return runtime, TestClient(app)


def test_health_is_local_but_does_not_require_token(tmp_path: Path):
    _runtime, client = client_for(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["loopback"] is True


def test_protected_endpoint_rejects_missing_or_bad_token(tmp_path: Path):
    runtime, client = client_for(tmp_path)
    assert client.get("/api/v1/info").status_code == 401
    assert (
        client.get("/api/v1/info", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    ok = client.get(
        "/api/v1/info", headers={"Authorization": f"Bearer {runtime.local_token}"}
    )
    assert ok.status_code == 200


def test_browser_origin_is_rejected_even_with_token(tmp_path: Path):
    runtime, client = client_for(tmp_path)
    response = client.get(
        "/api/v1/info",
        headers={
            "Authorization": f"Bearer {runtime.local_token}",
            "Origin": "https://attacker.example",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "browser_origin_rejected"


def test_submit_and_deduplicate_job(tmp_path: Path):
    runtime, client = client_for(tmp_path)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF placeholder")
    payload = {
        "library_id": 1,
        "parent_key": "ABCDEFGH",
        "attachment_key": "HGFEDCBA",
        "attachment_path": str(pdf),
        "title": "Paper",
    }
    headers = {"Authorization": f"Bearer {runtime.local_token}"}
    first = client.post("/api/v1/jobs", json=payload, headers=headers)
    second = client.post("/api/v1/jobs", json=payload, headers=headers)
    assert first.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["job"]["id"] == second.json()["job"]["id"]


def test_token_file_is_not_written_to_settings(tmp_path: Path):
    runtime, _client = client_for(tmp_path)
    token_file = tmp_path / "plugin-token"
    assert token_file.read_text(encoding="ascii").strip() == runtime.local_token
    assert runtime.local_token not in (tmp_path / "settings.json").read_text(encoding="utf-8")
