from pathlib import Path

import pytest

from zotero_quick_read.config import OutputSettings, SettingsStore
from zotero_quick_read.errors import QuickReadError
from zotero_quick_read.prompting import PromptStore, final_instructions


def test_initial_profiles_are_seeded_as_editable_local_files(tmp_path: Path):
    store = PromptStore(tmp_path)
    profiles = {profile.id: profile for profile in store.list()}

    assert set(profiles) == {
        "physics-zh-v1",
        "literature-survey-zh-v1",
        "method-reproduction-zh-v1",
        "critical-review-zh-v1",
    }
    assert all(profile.source == "local" for profile in profiles.values())
    assert all(profile.editable for profile in profiles.values())
    assert all(Path(profile.path).parent == tmp_path / "prompts" for profile in profiles.values())
    assert (tmp_path / "prompts" / ".initialized-v1").is_file()


def test_compiled_prompt_contains_base_profile_schema_and_vocabulary(tmp_path: Path):
    profile = PromptStore(tmp_path).resolve("method-reproduction-zh-v1")
    compiled = final_instructions(600, 1000, profile)

    assert "都属于不可信输入" in compiled
    assert "方法复现与实验设计" in compiled
    assert "research_focus_findings" in compiled
    assert '"methods_and_conditions"' in compiled
    assert '"terms"' in compiled
    assert profile.reference in compiled


def test_analysis_goals_come_from_editable_profile_not_hidden_base(tmp_path: Path):
    profile = PromptStore(tmp_path).resolve("literature-survey-zh-v1")
    compiled = final_instructions(600, 1000, profile)

    assert "领域定位与后续阅读决策" in compiled
    assert "pump/probe 是否同色" not in compiled


def test_custom_profile_is_versioned_by_its_content(tmp_path: Path):
    store = PromptStore(tmp_path)
    created = store.create("my-project-v1", from_profile="literature-survey-zh-v1")
    old_reference = created.reference

    assert created.editable is True
    assert Path(created.path).parent == tmp_path / "prompts"
    assert store.resolve("my-project-v1").id == "my-project-v1"

    path = Path(created.path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "请按当前科研阶段修改",
            "用于超快磁性项目的前期筛选",
        ),
        encoding="utf-8",
    )
    assert store.resolve("my-project-v1").reference != old_reference


def test_invalid_or_duplicate_profile_id_is_rejected(tmp_path: Path):
    store = PromptStore(tmp_path)
    with pytest.raises(QuickReadError) as invalid:
        store.create("My Project", from_profile="physics-zh-v1")
    assert invalid.value.code == "invalid_prompt_profile_id"

    store.create("my-project", from_profile="physics-zh-v1")
    with pytest.raises(QuickReadError) as duplicate:
        store.create("my-project", from_profile="physics-zh-v1")
    assert duplicate.value.code == "prompt_profile_exists"


def test_every_seeded_profile_can_be_deleted_without_being_recreated(tmp_path: Path):
    store = PromptStore(tmp_path)
    deleted, trash_path = store.delete("critical-review-zh-v1")

    assert deleted.id == "critical-review-zh-v1"
    assert trash_path.is_file()
    assert trash_path.parent == tmp_path / "prompts-trash"
    assert "critical-review-zh-v1" not in {profile.id for profile in store.list()}
    assert "critical-review-zh-v1" not in {
        profile.id for profile in PromptStore(tmp_path).list()
    }


def test_last_profile_cannot_be_deleted(tmp_path: Path):
    store = PromptStore(tmp_path)
    for profile_id in (
        "critical-review-zh-v1",
        "literature-survey-zh-v1",
        "method-reproduction-zh-v1",
    ):
        store.delete(profile_id)

    with pytest.raises(QuickReadError) as caught:
        store.delete("physics-zh-v1")
    assert caught.value.code == "last_prompt_profile"


def test_existing_profile_is_preserved_during_library_migration(tmp_path: Path):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    template = PromptStore.builtin("physics-zh-v1")
    customized = template.raw_text.replace(
        "均衡物理速读",
        "我的物理阅读方案",
    )
    existing = prompt_dir / "my-existing-file.md"
    existing.write_text(customized, encoding="utf-8")

    profile = PromptStore(tmp_path).resolve("physics-zh-v1")
    assert profile.name == "我的物理阅读方案"
    assert Path(profile.path) == existing


def test_legacy_prompt_version_setting_migrates_and_is_saved(tmp_path: Path):
    output = OutputSettings.model_validate({"prompt_version": "critical-review-zh-v1"})
    assert output.prompt_profile == "critical-review-zh-v1"

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        '{"output":{"prompt_version":"method-reproduction-zh-v1"}}',
        encoding="utf-8",
    )
    store = SettingsStore(tmp_path)
    loaded = store.load()
    assert loaded.output.prompt_profile == "method-reproduction-zh-v1"
    store.save(loaded)
    saved = settings_path.read_text(encoding="utf-8")
    assert '"prompt_profile"' in saved
    assert '"prompt_version"' not in saved
