from pathlib import Path

from zotero_quick_read.cli import _show_managed_window_banner, build_parser
from zotero_quick_read.config import AppSettings
from zotero_quick_read.service_control import ServiceController


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload

    def close(self):
        return None


class FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, str, dict | None]] = []

    def get(self, url: str, *, headers=None, timeout=None):
        self.calls.append(("GET", url, headers))
        if url.endswith("/health"):
            return FakeResponse(
                200,
                {
                    "status": "ok",
                    "loopback": True,
                    "version": "2.4.0",
                    "pid": 1234,
                    "started_at": "2026-10-12T00:00:00+00:00",
                },
            )
        return FakeResponse(
            200,
            {
                "selected_model": "gpt-5.6-terra",
                "auth_mode": "chatgpt",
                "prompt_profile": {"id": "physics-zh-v1", "version": "1.0.0"},
            },
        )


def controller(tmp_path: Path) -> ServiceController:
    return ServiceController(AppSettings(selected_model="gpt-5.6-terra"), tmp_path, "secret")


def test_status_reports_stopped_without_starting_anything(tmp_path: Path, monkeypatch):
    service = controller(tmp_path)
    monkeypatch.setattr(service, "_port_is_open", lambda: False)
    status = service.status()
    assert status["running"] is False
    assert status["port_in_use"] is False
    assert status["message"] == "后台未运行。"


def test_status_reports_running_model_and_pid(tmp_path: Path, monkeypatch):
    service = controller(tmp_path)
    fake_session = FakeSession()
    service.session = fake_session
    monkeypatch.setattr(service, "_port_is_open", lambda: True)
    status = service.status()
    assert status["running"] is True
    assert status["authenticated"] is True
    assert status["version"] == "2.4.0"
    assert status["pid"] == 1234
    assert status["selected_model"] == "gpt-5.6-terra"
    assert status["prompt_profile"]["id"] == "physics-zh-v1"
    assert fake_session.calls[-1][2] == {"Authorization": "Bearer secret"}


def test_start_is_idempotent_when_backend_is_running(tmp_path: Path, monkeypatch):
    service = controller(tmp_path)
    monkeypatch.setattr(
        service,
        "status",
        lambda: {"running": True, "port_in_use": True, "pid": 1234},
    )
    result = service.start()
    assert result["already_running"] is True
    assert result["pid"] == 1234


def test_stop_is_idempotent_when_backend_is_stopped(tmp_path: Path, monkeypatch):
    service = controller(tmp_path)
    monkeypatch.setattr(
        service,
        "status",
        lambda: {"running": False, "port_in_use": False},
    )
    result = service.stop()
    assert result["already_stopped"] is True


def test_cli_exposes_explicit_service_commands():
    parser = build_parser()
    for command in ("status", "start", "stop", "restart"):
        assert parser.parse_args([command]).command == command
    assert parser.parse_args(["start"]).hidden is False
    assert parser.parse_args(["start", "--hidden"]).hidden is True
    assert parser.parse_args(["restart", "--hidden"]).hidden is True


def test_cli_exposes_prompt_crud_commands():
    parser = build_parser()
    assert parser.parse_args(["prompt", "create", "my-prompt"]).prompt_command == "create"
    assert parser.parse_args(["prompt", "edit", "physics-zh-v1"]).prompt_command == "edit"
    assert parser.parse_args(["prompt", "delete", "physics-zh-v1"]).prompt_command == "delete"


def test_managed_serve_command_and_banner_are_explicit(tmp_path: Path, capsys):
    service = controller(tmp_path)
    command = service._serve_command("warning", managed_window=True)
    assert command[-1] == "--managed-window"

    _show_managed_window_banner(service.settings)
    output = capsys.readouterr().out
    assert "Zotero Quick Read" in output
    assert "RUNNING" in output
    assert "http://127.0.0.1:23120" in output
    assert "ZoteroQuickRead.exe stop" in output
