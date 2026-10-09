from __future__ import annotations

import socketserver
import threading

import pytest

from zotero_quick_read.config import ProxySettings
from zotero_quick_read.proxy import _proxy_failure, build_proxy_url, requests_session
from zotero_quick_read.secrets import SecretStore


@pytest.mark.parametrize(
    ("version", "dns", "scheme"),
    [
        ("4", "local", "socks4"),
        ("4", "proxy", "socks4a"),
        ("5", "local", "socks5"),
        ("5", "proxy", "socks5h"),
    ],
)
def test_proxy_scheme_encodes_version_and_dns_mode(version, dns, scheme):
    settings = ProxySettings(enabled=True, version=version, dns=dns, host="proxy.test", port=1080)
    assert build_proxy_url(settings) == f"{scheme}://proxy.test:1080"


def test_proxy_credentials_are_escaped_and_loaded_from_secret_store(tmp_path):
    secrets = SecretStore(tmp_path)
    secrets.set("proxy_password", "p@ss:/ word")
    settings = ProxySettings(
        enabled=True,
        version="5",
        dns="proxy",
        host="127.0.0.1",
        port=1080,
        username="name@example.com",
    )
    assert build_proxy_url(settings, secrets) == (
        "socks5h://name%40example.com:p%40ss%3A%2F%20word@127.0.0.1:1080"
    )


def test_disabled_proxy_session_does_not_inherit_environment():
    session = requests_session(ProxySettings(enabled=False), timeout=7.0)
    try:
        assert session.trust_env is False
        assert session.proxies == {}
        assert session.default_timeout == 7.0
    finally:
        session.close()


def test_proxy_auth_classification_does_not_confuse_auth_openai_hostname():
    ordinary = RuntimeError("proxy could not connect to auth.openai.com")
    assert _proxy_failure(ordinary)[0] == "proxy_connection_failed"

    class SOCKS5AuthError(RuntimeError):
        pass

    rejected = SOCKS5AuthError("authentication failed")
    assert _proxy_failure(rejected)[0] == "proxy_authentication_failed"


class _SocksCaptureServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, _SocksCaptureHandler)
        self.requested_host = None


def _read_exact(stream, length: int) -> bytes:
    result = b""
    while len(result) < length:
        chunk = stream.recv(length - len(result))
        if not chunk:
            raise ConnectionError("unexpected EOF")
        result += chunk
    return result


class _SocksCaptureHandler(socketserver.BaseRequestHandler):
    def handle(self):
        version, method_count = _read_exact(self.request, 2)
        assert version == 5
        _read_exact(self.request, method_count)
        self.request.sendall(b"\x05\x00")

        version, command, reserved, address_type = _read_exact(self.request, 4)
        assert (version, command, reserved) == (5, 1, 0)
        if address_type == 3:
            length = _read_exact(self.request, 1)[0]
            host = _read_exact(self.request, length).decode("ascii")
        elif address_type == 1:
            host = ".".join(str(part) for part in _read_exact(self.request, 4))
        else:
            raise AssertionError(f"unexpected SOCKS address type: {address_type}")
        _read_exact(self.request, 2)
        self.server.requested_host = (address_type, host)
        self.request.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")

        request = b""
        while b"\r\n\r\n" not in request:
            request += self.request.recv(4096)
        self.request.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"
        )


def test_socks5h_sends_hostname_to_proxy_instead_of_resolving_locally():
    pytest.importorskip("socks")
    server = _SocksCaptureServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = ProxySettings(
        enabled=True,
        version="5",
        dns="proxy",
        host="127.0.0.1",
        port=server.server_address[1],
    )
    session = requests_session(settings, timeout=2.0)
    try:
        response = session.get("http://name-that-must-not-resolve.invalid/test")
        assert response.text == "OK"
        # SOCKS5 ATYP 0x03 proves that the proxy, not this machine, receives DNS work.
        assert server.requested_host == (3, "name-that-must-not-resolve.invalid")
    finally:
        session.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
