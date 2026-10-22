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
    health = response.json()
    assert health["loopback"] is True
    assert isinstance(health["pid"], int)
    assert health["started_at"].endswith("+00:00")


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
    info = ok.json()
    assert info["prompt_profile"]["id"] == "physics-fast-reading-zh"
    assert info["prompt_profile"]["source"] == "local"
    assert info["prompt_version"].startswith("physics-fast-reading-zh@2.0.0+")


def test_prompt_catalog_exposes_local_profiles_and_default(tmp_path: Path):
    runtime, client = client_for(tmp_path)
    headers = {"Authorization": f"Bearer {runtime.local_token}"}

    response = client.get("/api/v1/prompts", headers=headers)

    assert response.status_code == 200
    catalog = response.json()
    assert catalog["default"]["id"] == "physics-fast-reading-zh"
    assert catalog["default"]["effective_target_min_chinese_chars"] == 900
    assert catalog["default"]["effective_target_max_chinese_chars"] == 1400
    assert catalog["default"]["output_length_source"] == "prompt_profile"
    assert {profile["id"] for profile in catalog["profiles"]} == {
        "physics-fast-reading-zh",
        "physics-methods-deep-zh",
    }
    assert all(profile["source"] == "local" for profile in catalog["profiles"])
    assert all("content" not in profile for profile in catalog["profiles"])


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


def test_submit_can_override_prompt_profile_per_job(tmp_path: Path):
    runtime, client = client_for(tmp_path)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF placeholder")
    headers = {"Authorization": f"Bearer {runtime.local_token}"}
    payload = {
        "library_id": 1,
        "parent_key": "ABCDEFGH",
        "attachment_key": "HGFEDCBA",
        "attachment_path": str(pdf),
        "title": "Paper",
        "prompt_profile": "physics-methods-deep-zh",
    }

    response = client.post("/api/v1/jobs", json=payload, headers=headers)

    assert response.status_code == 200
    job = response.json()["job"]
    assert job["prompt_profile"]["id"] == "physics-methods-deep-zh"
    assert job["instruction_version"].startswith("physics-methods-deep-zh@2.0.0+")
    assert "content" not in job["prompt_profile"]
    snapshot = runtime.db.payload(job["id"])["prompt_profile"]
    assert snapshot["id"] == "physics-methods-deep-zh"
    assert "# 任务" in snapshot["content"]


def test_different_prompt_profiles_do_not_deduplicate(tmp_path: Path):
    runtime, client = client_for(tmp_path)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF placeholder")
    headers = {"Authorization": f"Bearer {runtime.local_token}"}
    payload = {
        "library_id": 1,
        "parent_key": "ABCDEFGH",
        "attachment_key": "HGFEDCBA",
        "attachment_path": str(pdf),
        "title": "Paper",
    }

    default = client.post("/api/v1/jobs", json=payload, headers=headers)
    method = client.post(
        "/api/v1/jobs",
        json={**payload, "prompt_profile": "physics-methods-deep-zh"},
        headers=headers,
    )

    assert default.json()["created"] is True
    assert method.json()["created"] is True
    assert default.json()["job"]["id"] != method.json()["job"]["id"]


def test_unknown_prompt_profile_is_rejected(tmp_path: Path):
    runtime, client = client_for(tmp_path)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF placeholder")
    response = client.post(
        "/api/v1/jobs",
        json={
            "library_id": 1,
            "parent_key": "ABCDEFGH",
            "attachment_path": str(pdf),
            "prompt_profile": "missing-profile",
        },
        headers={"Authorization": f"Bearer {runtime.local_token}"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "prompt_profile_not_found"


def test_token_file_is_not_written_to_settings(tmp_path: Path):
    runtime, _client = client_for(tmp_path)
    token_file = tmp_path / "plugin-token"
    assert token_file.read_text(encoding="ascii").strip() == runtime.local_token
    assert runtime.local_token not in (tmp_path / "settings.json").read_text(encoding="utf-8")


def test_authenticated_shutdown_requests_server_exit(tmp_path: Path):
    runtime = build_runtime(tmp_path)
    calls: list[bool] = []
    app = create_app(
        runtime=runtime,
        start_worker=False,
        allow_test_client=True,
        shutdown_callback=lambda: calls.append(True),
    )
    client = TestClient(app)
    assert client.post("/api/v1/control/shutdown").status_code == 401
    response = client.post(
        "/api/v1/control/shutdown",
        headers={"Authorization": f"Bearer {runtime.local_token}"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls == [True]
