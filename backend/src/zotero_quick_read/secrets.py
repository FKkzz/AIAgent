from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import tempfile
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Any, Protocol

from cryptography.fernet import Fernet, InvalidToken

from .errors import QuickReadError

_SECRET_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_DPAPI_ENTROPY = b"ZoteroQuickRead.SecretStore.v1"


class _Protector(Protocol):
    def protect(self, plaintext: bytes) -> bytes: ...

    def unprotect(self, ciphertext: bytes) -> bytes: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    # Keep the backing buffer alive for the duration of the native call.
    buffer = ctypes.create_string_buffer(data, len(data))
    return (
        _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))),
        buffer,
    )


class _DpapiProtector:
    """Encrypt data for the current Windows user with DPAPI."""

    _CRYPTPROTECT_UI_FORBIDDEN = 0x01

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows DPAPI is only available on Windows")
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    def protect(self, plaintext: bytes) -> bytes:
        input_blob, input_buffer = _blob(plaintext)
        entropy_blob, entropy_buffer = _blob(_DPAPI_ENTROPY)
        output_blob = _DataBlob()
        # References are intentionally retained until CryptProtectData returns.
        _ = (input_buffer, entropy_buffer)
        if not self._crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            "Zotero Quick Read credentials",
            ctypes.byref(entropy_blob),
            None,
            None,
            self._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            self._kernel32.LocalFree(output_blob.pbData)

    def unprotect(self, ciphertext: bytes) -> bytes:
        input_blob, input_buffer = _blob(ciphertext)
        entropy_blob, entropy_buffer = _blob(_DPAPI_ENTROPY)
        output_blob = _DataBlob()
        description = wintypes.LPWSTR()
        _ = (input_buffer, entropy_buffer)
        if not self._crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            ctypes.byref(description),
            ctypes.byref(entropy_blob),
            None,
            None,
            self._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output_blob),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            if description:
                self._kernel32.LocalFree(description)
            self._kernel32.LocalFree(output_blob.pbData)


class _FernetProtector:
    """Portable encrypted fallback used by tests and non-Windows development."""

    def __init__(self, key_path: Path) -> None:
        self._key_path = key_path
        if key_path.exists():
            key = key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            _atomic_write(key_path, key, mode=0o600)
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as exc:
            raise QuickReadError(
                "secret_key_invalid",
                "The portable secret-store key is invalid.",
            ) from exc

    def protect(self, plaintext: bytes) -> bytes:
        return self._fernet.encrypt(plaintext)

    def unprotect(self, ciphertext: bytes) -> bytes:
        try:
            return self._fernet.decrypt(ciphertext)
        except InvalidToken as exc:
            raise QuickReadError(
                "secret_store_corrupt",
                "The protected credential store cannot be decrypted.",
            ) from exc


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp_path, mode)
        except OSError:
            # DPAPI protects the Windows payload even on filesystems without POSIX modes.
            pass
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


class SecretStore:
    """Small atomic credential store protected by Windows DPAPI.

    On non-Windows systems a Fernet key with owner-only permissions is used so
    unit tests and development never fall back to plaintext. Production Windows
    installations always use current-user DPAPI and do not create a key file.
    """

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "secrets.bin"
        self._lock = threading.RLock()
        self._protector: _Protector
        if os.name == "nt":
            self._protector = _DpapiProtector()
        else:
            self._protector = _FernetProtector(self.data_dir / "secrets.key")

    def get(self, name: str, default: Any = None) -> Any:
        self._validate_name(name)
        with self._lock:
            return self._read_all().get(name, default)

    def set(self, name: str, value: Any) -> None:
        self._validate_name(name)
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("secret value must be JSON serializable") from exc
        with self._lock:
            values = self._read_all()
            values[name] = value
            self._write_all(values)

    def delete(self, name: str) -> bool:
        self._validate_name(name)
        with self._lock:
            values = self._read_all()
            if name not in values:
                return False
            del values[name]
            self._write_all(values)
            return True

    def _read_all(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            envelope = json.loads(self.path.read_text(encoding="ascii"))
            if envelope.get("version") != 1 or not isinstance(envelope.get("payload"), str):
                raise ValueError("unsupported credential-store envelope")
            ciphertext = base64.b64decode(envelope["payload"], validate=True)
            plaintext = self._protector.unprotect(ciphertext)
            values = json.loads(plaintext.decode("utf-8"))
            if not isinstance(values, dict):
                raise ValueError("credential-store payload is not an object")
            return values
        except QuickReadError:
            raise
        except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise QuickReadError(
                "secret_store_corrupt",
                "The protected credential store is unreadable or corrupt.",
            ) from exc

    def _write_all(self, values: dict[str, Any]) -> None:
        plaintext = json.dumps(
            values,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        try:
            ciphertext = self._protector.protect(plaintext)
        except QuickReadError:
            raise
        except OSError as exc:
            raise QuickReadError(
                "secret_store_unavailable",
                "Windows could not protect the credential store for this user.",
            ) from exc
        envelope = json.dumps(
            {
                "version": 1,
                "protection": "dpapi-current-user" if os.name == "nt" else "fernet-test",
                "payload": base64.b64encode(ciphertext).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode("ascii")
        _atomic_write(self.path, envelope)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str) or not _SECRET_NAME.fullmatch(name):
            raise ValueError("invalid secret name")
