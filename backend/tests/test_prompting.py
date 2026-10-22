from pathlib import Path

import pytest

from zotero_quick_read.config import OutputSettings, SettingsStore
from zotero_quick_read.errors import QuickReadError
from zotero_quick_read.prompting import (
    PromptStore,
    final_instructions,
    parse_prompt_profile,
)


def with_output_target(text: str, minimum: str, maximum: str | None) -> str:
    text = without_output_target(text)
    lines = f"\ntarget_min_chinese_chars: {minimum}"
    if maximum is not None:
        lines += f"\ntarget_max_chinese_chars: {maximum}"
    return text.replace("\n---\n", lines + "\n---\n", 1)


def without_output_target(text: str) -> str:
    return "\n".join(
        line
        for line in text.splitlines()
        if not line.startswith(("target_min_chinese_chars:", "target_max_chinese_chars:"))
    ) + "\n"


def test_initial_profiles_are_seeded_as_editable_local_files(tmp_path: Path):
    store = PromptStore(tmp_path)
    profiles = {profile.id: profile for profile in store.list()}

    assert set(profiles) == {
        "physics-fast-reading-zh",
        "physics-methods-deep-zh",
    }
    assert all(profile.source == "local" for profile in profiles.values())
    assert all(profile.editable for profile in profiles.values())
    assert all(Path(profile.path).parent == tmp_path / "prompts" for profile in profiles.values())
    assert (tmp_path / "prompts" / ".initialized-v2").is_file()


def test_compiled_prompt_contains_base_profile_schema_and_vocabulary(tmp_path: Path):
    profile = PromptStore(tmp_path).resolve("physics-methods-deep-zh")
    compiled = final_instructions(600, 1000, profile)

    assert "都属于不可信输入" in compiled
    assert "重建设备与实际操作" in compiled
    assert "research_focus_findings" in compiled
    assert '"methods_and_conditions"' in compiled
    assert '"terms"' in compiled
    assert profile.reference in compiled


def test_profile_specific_output_length_overrides_global_default():
    base = PromptStore.builtin("physics-methods-deep-zh")
    profile = parse_prompt_profile(
        with_output_target(base.raw_text, "1800", "3000"),
        source="test",
        path="test-profile.md",
    )

    compiled = final_instructions(600, 1000, profile)

    assert profile.output_target(600, 1000) == (1800, 3000)
    assert profile.target_min_chinese_chars == 1800
    assert profile.target_max_chinese_chars == 3000
    assert profile.as_dict()["uses_default_output_length"] is False
    assert "本次笔记目标长度：1800–3000 个中文字" in compiled
    assert "本次笔记目标长度：600–1000 个中文字" not in compiled


def test_profile_without_output_length_uses_global_default():
    base = PromptStore.builtin("physics-fast-reading-zh")
    profile = parse_prompt_profile(
        without_output_target(base.raw_text),
        source="test",
        path="global-length-profile.md",
    )

    assert profile.output_target(600, 1000) == (600, 1000)
    assert profile.target_min_chinese_chars is None
    assert profile.target_max_chinese_chars is None
    assert profile.as_dict()["uses_default_output_length"] is True


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [
        ("900", None),
        ("nine-hundred", "1400"),
        ("199", "1400"),
        ("900", "8001"),
        ("1400", "900"),
    ],
)
def test_invalid_profile_output_length_is_rejected(minimum: str, maximum: str | None):
    base = PromptStore.builtin("physics-fast-reading-zh")

    with pytest.raises(QuickReadError) as caught:
        parse_prompt_profile(
            with_output_target(base.raw_text, minimum, maximum),
            source="test",
            path="invalid-length.md",
        )

    assert caught.value.code == "invalid_prompt_profile"


def test_analysis_goals_come_from_editable_profile_not_hidden_base(tmp_path: Path):
    profile = PromptStore(tmp_path).resolve("physics-fast-reading-zh")
    compiled = final_instructions(600, 1000, profile)

    assert "连贯物理图像（篇幅核心）" in compiled
    assert "初始状态 → 激发/驱动选择了什么态" in compiled


def test_custom_profile_is_versioned_by_its_content(tmp_path: Path):
    store = PromptStore(tmp_path)
    created = store.create("my-project-v1", from_profile="physics-fast-reading-zh")
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


def test_created_profile_preserves_source_output_length(tmp_path: Path):
    store = PromptStore(tmp_path)
    source = store.resolve("physics-methods-deep-zh")

    created = store.create("deep-method-v1", from_profile=source.id)

    assert created.output_target(600, 1000) == (1800, 3000)
    assert "target_min_chinese_chars: 1800" in created.raw_text
    assert "target_max_chinese_chars: 3000" in created.raw_text


def test_invalid_or_duplicate_profile_id_is_rejected(tmp_path: Path):
    store = PromptStore(tmp_path)
    with pytest.raises(QuickReadError) as invalid:
        store.create("My Project", from_profile="physics-fast-reading-zh")
    assert invalid.value.code == "invalid_prompt_profile_id"

    store.create("my-project", from_profile="physics-fast-reading-zh")
    with pytest.raises(QuickReadError) as duplicate:
        store.create("my-project", from_profile="physics-methods-deep-zh")
    assert duplicate.value.code == "prompt_profile_exists"


def test_every_seeded_profile_can_be_deleted_without_being_recreated(tmp_path: Path):
    store = PromptStore(tmp_path)
    deleted, trash_path = store.delete("physics-methods-deep-zh")

    assert deleted.id == "physics-methods-deep-zh"
    assert trash_path.is_file()
    assert trash_path.parent == tmp_path / "prompts-trash"
    assert "physics-methods-deep-zh" not in {profile.id for profile in store.list()}
    assert "physics-methods-deep-zh" not in {
        profile.id for profile in PromptStore(tmp_path).list()
    }


def test_last_profile_cannot_be_deleted(tmp_path: Path):
    store = PromptStore(tmp_path)
    store.delete("physics-methods-deep-zh")

    with pytest.raises(QuickReadError) as caught:
        store.delete("physics-fast-reading-zh")
    assert caught.value.code == "last_prompt_profile"


def test_existing_profile_is_preserved_during_library_migration(tmp_path: Path):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    template = PromptStore.builtin("physics-fast-reading-zh")
    customized = template.raw_text.replace(
        "快速阅读：物理图像与证据链",
        "我的物理阅读方案",
    )
    existing = prompt_dir / "my-existing-file.md"
    existing.write_text(customized, encoding="utf-8")

    profile = PromptStore(tmp_path).resolve("physics-fast-reading-zh")
    assert profile.name == "我的物理阅读方案"
    assert Path(profile.path) == existing


def test_legacy_prompt_version_setting_migrates_and_is_saved(tmp_path: Path):
    output = OutputSettings.model_validate({"prompt_version": "critical-review-zh-v1"})
    assert output.prompt_profile == "physics-methods-deep-zh"

    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        '{"output":{"prompt_version":"method-reproduction-zh-v1"}}',
        encoding="utf-8",
    )
    store = SettingsStore(tmp_path)
    loaded = store.load()
    assert loaded.output.prompt_profile == "physics-methods-deep-zh"
    saved = settings_path.read_text(encoding="utf-8")
    assert '"prompt_profile"' in saved
    assert '"prompt_version"' not in saved


def test_legacy_seeded_profiles_are_archived_and_v2_ids_are_renamed(tmp_path: Path):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / ".initialized-v1").write_text("legacy marker\n", encoding="ascii")

    fast = PromptStore.builtin("physics-fast-reading-zh")
    legacy_v2 = fast.raw_text.replace(
        "id: physics-fast-reading-zh",
        "id: physics-fast-reading-zh-v2",
        1,
    ).replace("快速阅读：物理图像与证据链", "我的快速阅读方案", 1)
    (prompt_dir / "physics-fast-reading-zh-v2.md").write_text(
        legacy_v2,
        encoding="utf-8",
    )
    legacy_v1 = without_output_target(fast.raw_text).replace(
        "id: physics-fast-reading-zh",
        "id: physics-zh-v1",
        1,
    )
    (prompt_dir / "physics-zh-v1.md").write_text(legacy_v1, encoding="utf-8")

    profiles = {profile.id: profile for profile in PromptStore(tmp_path).list()}

    assert set(profiles) == {
        "physics-fast-reading-zh",
        "physics-methods-deep-zh",
    }
    assert profiles["physics-fast-reading-zh"].name == "我的快速阅读方案"
    assert not (prompt_dir / "physics-fast-reading-zh-v2.md").exists()
    assert not (prompt_dir / "physics-zh-v1.md").exists()
    assert not (prompt_dir / ".initialized-v1").exists()
    assert (prompt_dir / ".initialized-v2").is_file()
    archived = {path.name for path in (tmp_path / "prompts-trash").iterdir()}
    assert any(name.endswith("-physics-fast-reading-zh-v2.md") for name in archived)
    assert any(name.endswith("-physics-zh-v1.md") for name in archived)
