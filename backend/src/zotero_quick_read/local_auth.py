from __future__ import annotations

import hmac
import os
import secrets
import stat
import subprocess
from pathlib import Path

from .errors import QuickReadError
from .secrets import SecretStore

LOCAL_TOKEN_SECRET = "local_api_token"  # noqa: S105
TOKEN_FILENAME = "plugin-token"  # noqa: S105 - filename, not a secret value


def ensure_local_api_token(data_dir: Path, secret_store: SecretStore) -> str:
    token = secret_store.get(LOCAL_TOKEN_SECRET)
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(48)
        secret_store.set(LOCAL_TOKEN_SECRET, token)
    token_path = data_dir / TOKEN_FILENAME
    current = None
    try:
        current = token_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        pass
    if current != token:
        data_dir.mkdir(parents=True, exist_ok=True)
        temporary = token_path.with_suffix(".tmp")
        temporary.write_text(token + "\n", encoding="ascii")
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, token_path)
        _restrict_token_file(token_path)
    return token


def token_file_path(data_dir: Path) -> Path:
    return data_dir / TOKEN_FILENAME


def token_matches(expected: str, supplied: str | None) -> bool:
    return bool(supplied) and hmac.compare_digest(expected, supplied)


def _restrict_token_file(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return
    account = os.environ.get("USERNAME")
    if not account:
        path.unlink(missing_ok=True)
        raise QuickReadError(
            "local_token_acl_failed",
            "无法识别当前 Windows 用户，未创建插件认证令牌文件。",
        )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    icacls = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe"
    completed = subprocess.run(  # noqa: S603 - fixed executable, shell disabled
        [
            str(icacls),
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{account}:(R,W)",
        ],
        check=False,
        capture_output=True,
        text=True,
        creationflags=creation_flags,
    )
    if completed.returncode != 0:
        path.unlink(missing_ok=True)
        raise QuickReadError(
            "local_token_acl_failed",
            "Windows 无法限制插件认证令牌文件的访问权限。",
        )
