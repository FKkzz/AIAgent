from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugin"
XPI = ROOT / "dist" / "zotero-quick-read-2.0.1.xpi"


def test_manifest_targets_installed_zotero_9():
    manifest = json.loads((PLUGIN / "manifest.json").read_text(encoding="utf-8"))
    zotero = manifest["applications"]["zotero"]
    assert manifest["manifest_version"] == 2
    assert manifest["version"] == "2.0.1"
    assert zotero["strict_min_version"] == "9.0.6"
    assert zotero["strict_max_version"] == "9.0.*"
    assert zotero["update_url"].startswith("https://")


def test_plugin_uses_dedicated_loopback_port_and_safe_zotero_apis():
    bootstrap = (PLUGIN / "bootstrap.js").read_text(encoding="utf-8")
    prefs = (PLUGIN / "prefs.js").read_text(encoding="utf-8")
    assert "http://127.0.0.1:23120" in bootstrap
    assert "http://127.0.0.1:23120" in prefs
    assert "23119" not in bootstrap + prefs  # reserved by Zotero Connector
    assert "Zotero.MenuManager.registerMenu" in bootstrap
    assert "Zotero.Notifier.registerObserver" in bootstrap
    assert "Zotero.DB.executeTransaction" in bootstrap
    assert ".addTag(" in bootstrap
    assert ".setTags(" not in bootstrap
    assert "errorDelayMax: 0" in bootstrap
    assert "autoSince" in bootstrap
    assert "backend-api" not in bootstrap


def test_menu_localization_uses_xul_label_attributes_and_safe_hooks():
    bootstrap = (PLUGIN / "bootstrap.js").read_text(encoding="utf-8")
    for locale in ("en-US", "zh-CN"):
        messages = (PLUGIN / "locale" / locale / "zotero-quick-read.ftl").read_text(
            encoding="utf-8"
        )
        for message_id in (
            "zqr-menu-root",
            "zqr-menu-generate",
            "zqr-menu-regenerate",
            "zqr-menu-status",
            "zqr-menu-settings",
            "zqr-menu-auto",
        ):
            lines = messages.splitlines()
            start = lines.index(f"{message_id} =")
            block_lines = []
            for line in lines[start + 1 :]:
                if line and not line[0].isspace():
                    break
                block_lines.append(line)
            block = "\n".join(block_lines)
            assert ".label =" in block
    assert "const safeShowing" in bootstrap
    assert "plugin callback abort construction of Zotero's native menus" in bootstrap


def test_bootstrap_javascript_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is unavailable")
    result = subprocess.run(  # noqa: S603
        [node, "--check", str(PLUGIN / "bootstrap.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_built_xpi_has_required_files_at_archive_root():
    if not XPI.exists():
        pytest.skip("XPI has not been built")
    with zipfile.ZipFile(XPI) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "bootstrap.js" in names
        assert "prefs.js" in names
        assert "locale/zh-CN/zotero-quick-read.ftl" in names
        assert not any(name.startswith("plugin/") for name in names)
