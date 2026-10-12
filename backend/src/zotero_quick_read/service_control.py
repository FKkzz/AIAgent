from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

from .config import AppSettings
from .errors import QuickReadError


class ServiceController:
    """Start, inspect, and stop the loopback backend without exposing credentials."""

    def __init__(self, settings: AppSettings, data_dir: Path, local_token: str):
        self.settings = settings
        self.data_dir = data_dir
        self.local_token = local_token
        self.base_url = f"http://127.0.0.1:{settings.port}"
        self.session = requests.Session()
        self.session.trust_env = False

    def status(self) -> dict[str, Any]:
        base = {
            "running": False,
            "address": self.base_url,
            "port": self.settings.port,
            "port_in_use": False,
            "message": "后台未运行。",
        }
        if not self._port_is_open():
            return base

        base["port_in_use"] = True
        try:
            response = self.session.get(f"{self.base_url}/health", timeout=1.5)
        except requests.exceptions.RequestException:
            base.update(
                {
                    "code": "port_in_use_by_other_process",
                    "message": "端口已被占用，但占用者不是可识别的 Zotero Quick Read 后台。",
                }
            )
            return base

        status_code = response.status_code
        try:
            health = response.json()
        except ValueError:
            health = None
        finally:
            response.close()
        if (
            status_code != 200
            or not isinstance(health, dict)
            or health.get("status") != "ok"
            or not health.get("loopback")
        ):
            base.update(
                {
                    "code": "port_in_use_by_other_process",
                    "message": "端口已被占用，但健康检查结果不属于 Zotero Quick Read。",
                }
            )
            return base

        base.update(
            {
                "running": True,
                "version": health.get("version"),
                "pid": health.get("pid"),
                "started_at": health.get("started_at"),
                "authenticated": False,
                "message": "后台正在运行。",
            }
        )
        try:
            response = self.session.get(
                f"{self.base_url}/api/v1/info",
                headers=self._headers(),
                timeout=1.5,
            )
        except requests.exceptions.RequestException:
            return base
        status_code = response.status_code
        try:
            info = response.json()
        except ValueError:
            info = None
        finally:
            response.close()
        if status_code == 200 and isinstance(info, dict):
            base.update(
                {
                    "authenticated": True,
                    "selected_model": info.get("selected_model"),
                    "auth_mode": info.get("auth_mode"),
                }
            )
        return base

    def start(self, *, log_level: str = "warning", timeout: float = 20.0) -> dict[str, Any]:
        current = self.status()
        if current["running"]:
            return {
                **current,
                "already_running": True,
                "message": "后台已经在运行，无需再次启动。",
            }
        if current["port_in_use"]:
            raise QuickReadError(
                "backend_port_in_use",
                f"端口 {self.settings.port} 已被其他程序占用；请更换端口或关闭占用者。",
            )

        self.data_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.data_dir / "backend.log"
        command = self._serve_command(log_level)
        popen_options: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": None,
            "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if getattr(sys, "frozen", False):
            child_environment = os.environ.copy()
            child_environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            popen_options["env"] = child_environment
        if os.name == "nt":
            popen_options["creationflags"] = (
                subprocess.CREATE_NO_WINDOW
                | subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            popen_options["start_new_session"] = True

        with log_path.open("a", encoding="utf-8") as log_file:
            popen_options["stdout"] = log_file
            process = subprocess.Popen(command, **popen_options)  # noqa: S603

        deadline = time.monotonic() + max(1.0, timeout)
        while time.monotonic() < deadline:
            current = self.status()
            if current["running"]:
                return {
                    **current,
                    "started": True,
                    "log_file": str(log_path),
                    "message": "后台已启动。以后可用 status 查看、stop 关闭。",
                }
            if process.poll() is not None:
                break
            time.sleep(0.2)
        raise QuickReadError(
            "backend_start_failed",
            f"后台未能启动；请检查日志：{log_path}",
        )

    def stop(self, *, timeout: float = 20.0) -> dict[str, Any]:
        current = self.status()
        if not current["running"]:
            if current["port_in_use"]:
                raise QuickReadError(
                    "backend_port_in_use",
                    f"端口 {self.settings.port} 被其他程序占用，不能作为本后台关闭。",
                )
            return {
                **current,
                "already_stopped": True,
                "message": "后台当前没有运行。",
            }
        if not current.get("authenticated"):
            raise QuickReadError(
                "backend_token_mismatch",
                "后台正在运行，但本地 Token 不匹配；拒绝关闭未知实例。",
            )

        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/control/shutdown",
                headers=self._headers(),
                timeout=3,
            )
        except requests.exceptions.RequestException as exc:
            raise QuickReadError(
                "backend_stop_failed",
                "无法向后台发送关闭请求。",
                retryable=True,
            ) from exc
        status_code = response.status_code
        response.close()
        if status_code == 404:
            raise QuickReadError(
                "backend_upgrade_required",
                "运行中的后台版本过旧，不支持 stop；请在任务管理器中结束一次后升级。",
            )
        if not 200 <= status_code < 300:
            raise QuickReadError(
                "backend_stop_failed",
                f"后台拒绝关闭请求（HTTP {status_code}）。",
            )

        deadline = time.monotonic() + max(1.0, timeout)
        while time.monotonic() < deadline:
            stopped = self.status()
            if not stopped["running"] and not stopped["port_in_use"]:
                return {
                    **stopped,
                    "stopped": True,
                    "message": "后台已关闭。",
                }
            time.sleep(0.2)
        raise QuickReadError(
            "backend_stop_timeout",
            "后台收到关闭请求，但未在规定时间内退出。",
            retryable=True,
        )

    def restart(self, *, log_level: str = "warning", timeout: float = 20.0) -> dict[str, Any]:
        self.stop(timeout=timeout)
        result = self.start(log_level=log_level, timeout=timeout)
        result["restarted"] = True
        result["message"] = "后台已重新启动。"
        return result

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.local_token}"}

    def _port_is_open(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.settings.port), timeout=0.35):
                return True
        except OSError:
            return False

    def _serve_command(self, log_level: str) -> list[str]:
        common = [
            "--data-dir",
            str(self.data_dir),
            "serve",
            "--log-level",
            log_level,
        ]
        if getattr(sys, "frozen", False):
            return [sys.executable, *common]
        return [sys.executable, "-m", "zotero_quick_read", *common]
