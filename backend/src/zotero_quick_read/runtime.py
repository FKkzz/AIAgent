from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import AppSettings, SettingsStore, default_data_dir
from .db import QueueDB
from .local_auth import ensure_local_api_token
from .openai_client import OpenAIClient
from .pipeline import JobWorker
from .prompting import PromptProfile, PromptStore
from .secrets import SecretStore


@dataclass(slots=True)
class Runtime:
    data_dir: Path
    settings_store: SettingsStore
    settings: AppSettings
    secrets: SecretStore
    db: QueueDB
    client: OpenAIClient
    worker: JobWorker
    prompt_store: PromptStore
    prompt_profile: PromptProfile
    local_token: str


def build_runtime(data_dir: Path | None = None, *, client=None) -> Runtime:
    data_dir = (data_dir or default_data_dir()).resolve()
    settings_store = SettingsStore(data_dir)
    settings = settings_store.load()
    secret_store = SecretStore(data_dir)
    local_token = ensure_local_api_token(data_dir, secret_store)
    db = QueueDB(data_dir / "queue.db")
    openai_client = client or OpenAIClient(settings, secret_store)
    prompt_store = PromptStore(data_dir)
    prompt_profile = prompt_store.resolve(settings.output.prompt_profile)
    worker = JobWorker(
        db,
        settings,
        lambda: openai_client,
        prompt_profile=prompt_profile,
    )
    return Runtime(
        data_dir=data_dir,
        settings_store=settings_store,
        settings=settings,
        secrets=secret_store,
        db=db,
        client=openai_client,
        worker=worker,
        prompt_store=prompt_store,
        prompt_profile=prompt_profile,
        local_token=local_token,
    )
