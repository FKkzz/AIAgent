from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from pathlib import Path

import uvicorn

from .api import create_app
from .auth import ChatGPTAuth
from .config import AppSettings, SettingsStore, default_data_dir
from .diagnostics import run_diagnostics
from .errors import QuickReadError
from .openai_client import API_KEY_SECRET, OpenAIClient
from .runtime import build_runtime
from .schemas import JobSubmit
from .secrets import SecretStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="zotero-quick-read")
    parser.add_argument("--data-dir", type=Path, help="Override local runtime data directory")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="Initialize settings, queue, and plugin token")

    configure = commands.add_parser("configure", help="Update non-secret local settings")
    configure.add_argument("--proxy", choices=("on", "off"))
    configure.add_argument("--socks-version", choices=("4", "5"))
    configure.add_argument("--proxy-host")
    configure.add_argument("--proxy-port", type=int)
    configure.add_argument("--proxy-dns", choices=("proxy", "local"))
    configure.add_argument("--proxy-username")
    configure.add_argument("--set-proxy-password", action="store_true")
    configure.add_argument("--clear-proxy-password", action="store_true")
    configure.add_argument("--firefox-path")
    configure.add_argument("--auth-mode", choices=("chatgpt", "api_key"))
    configure.add_argument("--model")
    configure.add_argument("--port", type=int)
    configure.add_argument("--page-images", choices=("on", "off"))
    configure.add_argument("--max-image-pages", type=int)

    login = commands.add_parser("auth-login", help="Sign in with ChatGPT in configured Firefox")
    login.add_argument("--force-new-registration", action="store_true")
    login.add_argument("--timeout", type=float, default=300.0)
    commands.add_parser("auth-status", help="Show redacted ChatGPT authorization status")
    commands.add_parser("auth-refresh", help="Refresh the saved ChatGPT session")
    commands.add_parser("auth-logout", help="Revoke and remove the ChatGPT session")

    commands.add_parser("set-api-key", help="Store an API key using protected local storage")
    commands.add_parser("clear-api-key", help="Remove the protected API key")

    models = commands.add_parser("models", help="List models for the selected auth mode")
    models.add_argument("--auth-mode", choices=("chatgpt", "api_key"))
    smoke = commands.add_parser("smoke", help="Run one minimal streaming request")
    smoke.add_argument("--auth-mode", choices=("chatgpt", "api_key"))
    smoke.add_argument("--model")

    diagnose = commands.add_parser("diagnose", help="Run layered connection diagnostics")
    diagnose.add_argument("--models", action="store_true")
    diagnose.add_argument("--inference", action="store_true")

    serve = commands.add_parser("serve", help="Run the loopback service")
    serve.add_argument("--log-level", default="info", choices=("error", "warning", "info", "debug"))

    read = commands.add_parser("read", help="Process one local PDF and create a preview")
    read.add_argument("pdf", type=Path)
    read.add_argument("--title", default="")
    read.add_argument("--model")
    read.add_argument("--auth-mode", choices=("chatgpt", "api_key"))
    read.add_argument("--timeout", type=float, default=3600.0)

    commands.add_parser("token-path", help="Print the plugin token file path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = (args.data_dir or default_data_dir()).expanduser().resolve()
    try:
        return _dispatch(args, data_dir)
    except QuickReadError as exc:
        _print_json({"ok": False, "error": {"code": exc.code, "message": exc.message}})
        return 2
    except (OSError, ValueError, RuntimeError) as exc:
        _print_json(
            {"ok": False, "error": {"code": "local_error", "message": str(exc)[:500]}}
        )
        return 2


def _dispatch(args: argparse.Namespace, data_dir: Path) -> int:
    settings_store = SettingsStore(data_dir)
    settings = settings_store.load()
    secrets = SecretStore(data_dir)

    if args.command == "init":
        runtime = build_runtime(data_dir)
        _print_json(
            {
                "ok": True,
                "data_dir": str(runtime.data_dir),
                "settings": str(runtime.settings_store.path),
                "plugin_token": str(runtime.data_dir / "plugin-token"),
            }
        )
        return 0
    if args.command == "configure":
        patch: dict = {}
        proxy_patch: dict = {}
        if args.proxy:
            proxy_patch["enabled"] = args.proxy == "on"
        for option, key in (
            (args.socks_version, "version"),
            (args.proxy_host, "host"),
            (args.proxy_port, "port"),
            (args.proxy_dns, "dns"),
            (args.proxy_username, "username"),
        ):
            if option is not None:
                proxy_patch[key] = option
        if proxy_patch:
            patch["proxy"] = proxy_patch
        pdf_patch: dict = {}
        if args.page_images:
            pdf_patch["render_page_images"] = args.page_images == "on"
        if args.max_image_pages is not None:
            pdf_patch["max_image_pages"] = args.max_image_pages
        if pdf_patch:
            patch["pdf"] = pdf_patch
        for option, key in (
            (args.firefox_path, "firefox_path"),
            (args.auth_mode, "auth_mode"),
            (args.model, "selected_model"),
            (args.port, "port"),
        ):
            if option is not None:
                patch[key] = option
        settings = settings_store.update(patch) if patch else settings
        if args.set_proxy_password:
            password = getpass.getpass("SOCKS proxy password: ")
            secrets.set(settings.proxy.password_secret, password)
        if args.clear_proxy_password:
            secrets.delete(settings.proxy.password_secret)
        _print_json({"ok": True, "settings": _safe_settings(settings, secrets)})
        return 0

    auth = ChatGPTAuth(settings, secrets)
    client = OpenAIClient(settings, secrets, auth=auth)
    if args.command == "auth-login":
        print("Firefox 将打开 OpenAI 官方授权页；完成登录与授权后返回此窗口。", flush=True)
        credentials = auth.login(
            timeout_seconds=args.timeout,
            force_new_registration=args.force_new_registration,
        )
        _print_json({"ok": True, "authorization": _redacted_credentials(credentials)})
        return 0
    if args.command == "auth-status":
        status = auth.status() if hasattr(auth, "status") else _redacted_credentials(
            secrets.get("chatgpt.credentials", {})
        )
        _print_json({"ok": True, "authorization": status})
        return 0
    if args.command == "auth-refresh":
        _print_json({"ok": True, "authorization": _redacted_credentials(auth.refresh())})
        return 0
    if args.command == "auth-logout":
        if not hasattr(auth, "logout"):
            secrets.delete("chatgpt.credentials")
        else:
            auth.logout(revoke=True)
        _print_json({"ok": True, "signed_in": False})
        return 0
    if args.command == "set-api-key":
        value = getpass.getpass("OpenAI API key (will be DPAPI-protected): ").strip()
        if not value:
            raise ValueError("API key cannot be empty")
        secrets.set(API_KEY_SECRET, value)
        _print_json({"ok": True, "api_key_configured": True})
        return 0
    if args.command == "clear-api-key":
        secrets.delete(API_KEY_SECRET)
        _print_json({"ok": True, "api_key_configured": False})
        return 0
    if args.command == "models":
        _print_json({"ok": True, "models": client.list_models(args.auth_mode)})
        return 0
    if args.command == "smoke":
        result = client.minimal_request(model=args.model, auth_mode=args.auth_mode)
        _print_json(
            {
                "ok": True,
                "completed": True,
                "text": result.text,
                "model": result.model,
                "usage": result.usage,
                "response_id": result.response_id,
            }
        )
        return 0
    if args.command == "diagnose":
        steps = run_diagnostics(
            settings,
            secrets,
            include_models=args.models or args.inference,
            include_inference=args.inference,
        )
        ok = all(step.status != "fail" for step in steps)
        _print_json({"ok": ok, "steps": [step.to_dict() for step in steps]})
        return 0 if ok else 2
    if args.command == "serve":
        # Build before starting so ACL/config errors are reported synchronously.
        runtime = build_runtime(data_dir)
        app = create_app(runtime=runtime)
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=runtime.settings.port,
            log_level=args.log_level,
            access_log=False,
            proxy_headers=False,
        )
        return 0
    if args.command == "read":
        return _read_preview(args, data_dir)
    if args.command == "token-path":
        runtime = build_runtime(data_dir)
        print(runtime.data_dir / "plugin-token")
        return 0
    raise AssertionError(args.command)


def _read_preview(args: argparse.Namespace, data_dir: Path) -> int:
    runtime = build_runtime(data_dir)
    pdf = args.pdf.expanduser().resolve()
    job, _ = runtime.db.enqueue(
        JobSubmit(
            library_id=0,
            parent_key="PREVIEW1",
            attachment_key="PREVIEW2",
            attachment_path=str(pdf),
            title=args.title or pdf.stem,
            model=args.model,
            auth_mode=args.auth_mode,
            force=True,
        ),
        instruction_version=runtime.settings.output.prompt_version,
        model=args.model or runtime.settings.selected_model,
        auth_mode=args.auth_mode or runtime.settings.auth_mode,
        max_attempts=runtime.settings.max_attempts,
        fingerprint=None,
    )
    runtime.worker.start()
    deadline = time.monotonic() + args.timeout
    try:
        while time.monotonic() < deadline:
            current = runtime.db.get(job.id)
            if current and current.status in {
                "completed",
                "failed",
                "waiting_quota",
                "reauthorization_required",
                "cancelled",
            }:
                break
            time.sleep(0.5)
        else:
            raise QuickReadError("preview_timeout", "等待论文处理超时。")
    finally:
        runtime.worker.stop()
    current = runtime.db.get(job.id)
    if current is None or current.status != "completed" or not current.result:
        _print_json(
            {
                "ok": False,
                "job_id": job.id,
                "status": current.status if current else "missing",
                "code": current.error_code if current else "job_missing",
                "message": current.message if current else "任务记录不存在。",
            }
        )
        return 2
    preview_dir = data_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    json_path = preview_dir / f"{job.id}.json"
    html_path = preview_dir / f"{job.id}.html"
    json_path.write_text(json.dumps(current.result, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(current.result["note_html"], encoding="utf-8")
    _print_json(
        {
            "ok": True,
            "job_id": job.id,
            "json_preview": str(json_path),
            "html_preview": str(html_path),
            "usage": current.result.get("processing", {}).get("usage"),
        }
    )
    return 0


def _safe_settings(settings: AppSettings, secrets: SecretStore) -> dict:
    value = settings.model_dump(mode="json")
    value["proxy"]["password_configured"] = bool(
        secrets.get(settings.proxy.password_secret)
    )
    value["proxy"].pop("password_secret", None)
    return value


def _redacted_credentials(credentials: dict) -> dict:
    if not isinstance(credentials, dict) or not credentials:
        return {"signed_in": False}
    return {
        "signed_in": bool(credentials.get("access_token")),
        "email": credentials.get("email"),
        "client_id": credentials.get("client_id"),
        "scopes": credentials.get("scopes", []),
        "expires_at": credentials.get("expires_at"),
        "saved_at": credentials.get("saved_at"),
    }


def _print_json(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
