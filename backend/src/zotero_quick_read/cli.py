from __future__ import annotations

import argparse
import ctypes
import getpass
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import uvicorn

from . import __version__
from .api import create_app
from .auth import ChatGPTAuth
from .config import AppSettings, SettingsStore, default_data_dir
from .diagnostics import run_diagnostics
from .errors import QuickReadError
from .local_auth import ensure_local_api_token
from .openai_client import API_KEY_SECRET, OpenAIClient
from .prompting import PromptStore, final_instructions
from .runtime import build_runtime
from .schemas import JobSubmit
from .secrets import SecretStore
from .service_control import ServiceController


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
    configure.add_argument("--prompt-profile")

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

    prompt = commands.add_parser("prompt", help="Manage research-stage prompt profiles")
    prompt_commands = prompt.add_subparsers(dest="prompt_command", required=True)
    prompt_commands.add_parser("list", help="List local prompt profiles")
    prompt_show = prompt_commands.add_parser("show", help="Show a prompt profile")
    prompt_show.add_argument("profile", nargs="?")
    prompt_show.add_argument(
        "--compiled",
        action="store_true",
        help="Include the complete instructions with schema and tag vocabulary",
    )
    prompt_create = prompt_commands.add_parser("create", help="Copy a profile for editing")
    prompt_create.add_argument("profile")
    prompt_create.add_argument("--from", dest="from_profile")
    prompt_use = prompt_commands.add_parser("use", help="Select a prompt profile")
    prompt_use.add_argument("profile")
    prompt_edit = prompt_commands.add_parser("edit", help="Open a profile in Notepad")
    prompt_edit.add_argument("profile", nargs="?")
    prompt_delete = prompt_commands.add_parser("delete", help="Delete a profile recoverably")
    prompt_delete.add_argument("profile")

    serve = commands.add_parser("serve", help="Run the loopback service")
    serve.add_argument("--log-level", default="info", choices=("error", "warning", "info", "debug"))
    serve.add_argument("--managed-window", action="store_true", help=argparse.SUPPRESS)

    commands.add_parser("status", help="Show whether the local backend is running")
    start = commands.add_parser("start", help="Start the local backend in the background")
    start.add_argument(
        "--log-level", default="warning", choices=("error", "warning", "info", "debug")
    )
    start.add_argument("--timeout", type=float, default=20.0)
    start.add_argument(
        "--hidden",
        action="store_true",
        help="Start without a visible status window",
    )
    stop = commands.add_parser("stop", help="Stop the authenticated local backend")
    stop.add_argument("--timeout", type=float, default=20.0)
    restart = commands.add_parser("restart", help="Restart the authenticated local backend")
    restart.add_argument(
        "--log-level", default="warning", choices=("error", "warning", "info", "debug")
    )
    restart.add_argument("--timeout", type=float, default=20.0)
    restart.add_argument(
        "--hidden",
        action="store_true",
        help="Restart without a visible status window",
    )

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
        if args.prompt_profile:
            profile = PromptStore(data_dir).resolve(args.prompt_profile)
            patch["output"] = {"prompt_profile": profile.id}
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

    if args.command == "prompt":
        return _dispatch_prompt(args, data_dir, settings_store, settings)

    if args.command in {"status", "start", "stop", "restart", "serve"}:
        local_token = ensure_local_api_token(data_dir, secrets)
        service = ServiceController(settings, data_dir, local_token)
        if args.command == "status":
            _print_json({"ok": True, "service": service.status()})
            return 0
        if args.command == "start":
            _print_json(
                {
                    "ok": True,
                    "service": service.start(
                        log_level=args.log_level,
                        timeout=args.timeout,
                        show_window=not args.hidden,
                    ),
                }
            )
            return 0
        if args.command == "stop":
            _print_json({"ok": True, "service": service.stop(timeout=args.timeout)})
            return 0
        if args.command == "restart":
            _print_json(
                {
                    "ok": True,
                    "service": service.restart(
                        log_level=args.log_level,
                        timeout=args.timeout,
                        show_window=not args.hidden,
                    ),
                }
            )
            return 0
        current = service.status()
        if current["running"]:
            _print_json(
                {
                    "ok": True,
                    "service": {
                        **current,
                        "already_running": True,
                        "message": "后台已经在运行；无需再次执行 serve。",
                        "how_to_stop": "ZoteroQuickRead.exe stop",
                        "how_to_restart": "ZoteroQuickRead.exe restart",
                    },
                }
            )
            return 0
        if current["port_in_use"]:
            raise QuickReadError(
                "backend_port_in_use",
                f"端口 {settings.port} 已被其他程序占用；后台未启动。",
            )

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
        if args.managed_window:
            _show_managed_window_banner(runtime.settings)
        server_holder: dict[str, uvicorn.Server] = {}

        def request_shutdown() -> None:
            server_holder["server"].should_exit = True

        app = create_app(runtime=runtime, shutdown_callback=request_shutdown)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=runtime.settings.port,
            log_level=args.log_level,
            access_log=False,
            proxy_headers=False,
        )
        server = uvicorn.Server(config)
        server_holder["server"] = server
        server.run()
        return 0
    if args.command == "read":
        return _read_preview(args, data_dir)
    if args.command == "token-path":
        runtime = build_runtime(data_dir)
        print(runtime.data_dir / "plugin-token")
        return 0
    raise AssertionError(args.command)


def _show_managed_window_banner(settings: AppSettings) -> None:
    title = f"Zotero Quick Read {__version__} - RUNNING"
    if os.name == "nt":
        if getattr(sys, "frozen", False):
            try:
                console_output = open(  # Kept open for the managed process lifetime.
                    "CONOUT$",
                    "w",
                    encoding="utf-8",
                    errors="replace",
                    buffering=1,
                )
            except OSError:
                pass
            else:
                sys.stdout = console_output
                sys.stderr = console_output
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    border = "=" * 62
    print(
        "\n".join(
            (
                border,
                title,
                "",
                "The local backend is running.",
                f"Address: http://127.0.0.1:{settings.port}",
                "You may minimize this window.",
                "Stop command: ZoteroQuickRead.exe stop",
                "Closing this window also stops the backend.",
                border,
            )
        ),
        flush=True,
    )


def _dispatch_prompt(
    args: argparse.Namespace,
    data_dir: Path,
    settings_store: SettingsStore,
    settings: AppSettings,
) -> int:
    store = PromptStore(data_dir)
    selected = settings.output.prompt_profile
    if args.prompt_command == "list":
        _print_json(
            {
                "ok": True,
                "selected": selected,
                "prompt_directory": str(store.custom_dir),
                "custom_directory": str(store.custom_dir),
                "trash_directory": str(store.trash_dir),
                "profiles": [
                    {**profile.as_dict(), "selected": profile.id == selected}
                    for profile in store.list()
                ],
            }
        )
        return 0
    if args.prompt_command == "show":
        profile = store.resolve(args.profile or selected)
        result = {
            "ok": True,
            "selected": profile.id == selected,
            "profile": profile.as_dict(include_content=True),
        }
        if args.compiled:
            result["compiled_instructions"] = final_instructions(
                settings.output.target_min_chinese_chars,
                settings.output.target_max_chinese_chars,
                profile,
            )
        _print_json(result)
        return 0
    if args.prompt_command == "create":
        profile = store.create(
            args.profile,
            from_profile=args.from_profile or selected,
        )
        _print_json(
            {
                "ok": True,
                "profile": profile.as_dict(include_content=False),
                "next": [
                    f"ZoteroQuickRead.exe prompt edit {profile.id}",
                    f"ZoteroQuickRead.exe prompt use {profile.id}",
                    "ZoteroQuickRead.exe restart",
                ],
            }
        )
        return 0
    if args.prompt_command == "use":
        profile = store.resolve(args.profile)
        settings_store.update({"output": {"prompt_profile": profile.id}})
        _print_json(
            {
                "ok": True,
                "selected": profile.as_dict(),
                "restart_required": True,
                "restart_command": "ZoteroQuickRead.exe restart",
            }
        )
        return 0
    if args.prompt_command == "edit":
        profile = store.resolve(args.profile or selected)
        if os.name != "nt":
            raise QuickReadError(
                "prompt_editor_unavailable",
                f"请使用文本编辑器打开：{profile.path}",
            )
        notepad = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "notepad.exe"
        subprocess.Popen(  # noqa: S603
            [str(notepad), profile.path],
            close_fds=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        _print_json({"ok": True, "opened": profile.path, "profile": profile.id})
        return 0
    if args.prompt_command == "delete":
        profile, trash_path = store.delete(args.profile)
        remaining = store.list()
        selected_after = selected
        restart_required = profile.id == selected
        if restart_required:
            preferred = next(
                (item for item in remaining if item.id == "physics-fast-reading-zh"),
                remaining[0],
            )
            selected_after = preferred.id
            settings_store.update({"output": {"prompt_profile": selected_after}})
        _print_json(
            {
                "ok": True,
                "deleted": profile.id,
                "recoverable_from": str(trash_path),
                "selected": selected_after,
                "restart_required": restart_required,
                "restart_command": (
                    "ZoteroQuickRead.exe restart" if restart_required else None
                ),
            }
        )
        return 0
    raise AssertionError(args.prompt_command)


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
        instruction_version=runtime.prompt_profile.reference,
        model=args.model or runtime.settings.selected_model,
        auth_mode=args.auth_mode or runtime.settings.auth_mode,
        max_attempts=runtime.settings.max_attempts,
        fingerprint=None,
        prompt_profile=runtime.prompt_profile.as_dict(include_content=True),
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
