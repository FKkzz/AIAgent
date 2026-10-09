from __future__ import annotations

import os
import stat

import pytest

from zotero_quick_read.errors import QuickReadError
from zotero_quick_read.secrets import SecretStore


def test_secret_store_round_trip_is_encrypted(tmp_path):
    store = SecretStore(tmp_path)
    marker = "secret-value-that-must-not-appear-on-disk"

    store.set("oauth.tokens", {"access_token": marker, "scopes": ["openid"]})

    assert store.get("oauth.tokens")["access_token"] == marker
    assert marker.encode() not in store.path.read_bytes()
    assert SecretStore(tmp_path).get("oauth.tokens")["scopes"] == ["openid"]


def test_secret_store_set_delete_and_default(tmp_path):
    store = SecretStore(tmp_path)
    assert store.get("missing", "fallback") == "fallback"
    store.set("proxy_password", "p@ssword")
    assert store.get("proxy_password") == "p@ssword"
    assert store.delete("proxy_password") is True
    assert store.delete("proxy_password") is False
    assert store.get("proxy_password") is None


def test_secret_store_rejects_invalid_name_and_value(tmp_path):
    store = SecretStore(tmp_path)
    with pytest.raises(ValueError):
        store.set("../escape", "value")
    with pytest.raises(ValueError):
        store.set("valid", object())


def test_secret_store_detects_corruption(tmp_path):
    store = SecretStore(tmp_path)
    store.set("token", "value")
    store.path.write_text('{"version":1,"payload":"not-base64!"}', encoding="ascii")

    with pytest.raises(QuickReadError) as caught:
        store.get("token")

    assert caught.value.code == "secret_store_corrupt"


@pytest.mark.skipif(os.name == "nt", reason="portable fallback is only used off Windows")
def test_portable_fallback_key_is_owner_only(tmp_path):
    SecretStore(tmp_path).set("token", "value")
    mode = stat.S_IMODE((tmp_path / "secrets.key").stat().st_mode)
    assert mode & 0o077 == 0
