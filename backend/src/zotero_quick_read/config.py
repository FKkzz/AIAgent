from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

APP_NAME = "ZoteroQuickRead"
SETTINGS_VERSION = 1


def default_data_dir() -> Path:
    override = os.environ.get("ZQR_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA")
        if not root:
            raise RuntimeError("LOCALAPPDATA is unavailable")
        return Path(root) / APP_NAME
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME


class ProxySettings(BaseModel):
    enabled: bool = False
    version: Literal["4", "5"] = "5"
    host: str = "127.0.0.1"
    port: int = Field(default=1080, ge=1, le=65535)
    dns: Literal["proxy", "local"] = "proxy"
    username: str | None = None
    password_secret: str = "proxy_password"  # noqa: S105 - storage key, not a password

    @field_validator("host")
    @classmethod
    def nonempty_host(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("proxy host cannot be empty")
        return value


class PdfSettings(BaseModel):
    max_file_mb: int = Field(default=120, ge=1, le=2048)
    min_text_chars: int = Field(default=500, ge=1)
    chunk_chars: int = Field(default=36_000, ge=4_000, le=200_000)
    max_chunks: int = Field(default=20, ge=1, le=100)
    stable_seconds: float = Field(default=2.0, ge=0.0, le=30.0)
    render_page_images: bool = True
    max_image_pages: int = Field(default=3, ge=0, le=12)


class OutputSettings(BaseModel):
    language: str = "zh-CN"
    target_min_chinese_chars: int = Field(default=600, ge=200, le=5000)
    target_max_chinese_chars: int = Field(default=1000, ge=300, le=8000)
    max_tags: int = Field(default=8, ge=1, le=12)
    prompt_version: str = "physics-zh-v1"

    @field_validator("target_max_chinese_chars")
    @classmethod
    def max_above_min(cls, value: int, info):
        minimum = info.data.get("target_min_chinese_chars", 0)
        if value < minimum:
            raise ValueError("target_max_chinese_chars must be >= target_min_chinese_chars")
        return value


class AppSettings(BaseModel):
    settings_version: int = SETTINGS_VERSION
    host: Literal["127.0.0.1"] = "127.0.0.1"
    # Zotero itself reserves 23119 for Connector communication.
    port: int = Field(default=23120, ge=1024, le=65535)
    auth_mode: Literal["chatgpt", "api_key"] = "chatgpt"
    selected_model: str | None = None
    firefox_path: str = r"C:\Program Files\Mozilla Firefox\firefox.exe"
    proxy: ProxySettings = Field(default_factory=ProxySettings)
    pdf: PdfSettings = Field(default_factory=PdfSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    request_timeout_seconds: int = Field(default=180, ge=10, le=3600)
    max_attempts: int = Field(default=3, ge=1, le=10)
    concurrency: Literal[1] = 1


class SettingsStore:
    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or default_data_dir()
        self.path = self.data_dir / "settings.json"

    def load(self) -> AppSettings:
        if not self.path.exists():
            settings = AppSettings()
            self.save(settings)
            return settings
        try:
            return AppSettings.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid settings file: {self.path}: {exc}") from exc

    def save(self, settings: AppSettings) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            settings.model_dump_json(indent=2, exclude_none=True),
            encoding="utf-8",
        )
        os.replace(temp, self.path)

    def update(self, patch: dict) -> AppSettings:
        current = self.load().model_dump(mode="json")
        _deep_update(current, patch)
        updated = AppSettings.model_validate(current)
        self.save(updated)
        return updated


def _deep_update(target: dict, patch: dict) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
