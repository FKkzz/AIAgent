from __future__ import annotations

import ipaddress
from typing import Any
from urllib.parse import quote

import requests

from .config import ProxySettings
from .errors import ProxyError
from .secrets import SecretStore


def _proxy_failure(exc: BaseException) -> tuple[str, str]:
    current: BaseException | None = exc
    fragments: list[str] = []
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        fragments.append(type(current).__name__.casefold())
        fragments.append(str(current).casefold())
        current = current.__cause__ or current.__context__
    diagnostic = " ".join(fragments)
    authentication_markers = (
        "socks5autherror",
        "socks4autherror",
        "authentication failed",
        "authentication is required",
        "username/password authentication",
    )
    if any(marker in diagnostic for marker in authentication_markers):
        return (
            "proxy_authentication_failed",
            "The configured SOCKS proxy rejected its username or password.",
        )
    return (
        "proxy_connection_failed",
        "The configured SOCKS proxy rejected or could not establish the connection.",
    )


def _coerce_settings(proxy: ProxySettings | dict[str, Any]) -> ProxySettings:
    if isinstance(proxy, ProxySettings):
        return proxy
    return ProxySettings.model_validate(proxy)


def _proxy_scheme(proxy: ProxySettings) -> str:
    # PySocks uses the *h/*a variants to pass a hostname to the SOCKS server.
    if proxy.version == "5":
        return "socks5h" if proxy.dns == "proxy" else "socks5"
    return "socks4a" if proxy.dns == "proxy" else "socks4"


def _format_host(host: str) -> str:
    try:
        parsed = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return host
    return f"[{host}]" if parsed.version == 6 else host


def build_proxy_url(
    proxy: ProxySettings | dict[str, Any],
    secrets: SecretStore | None = None,
    *,
    password: str | None = None,
) -> str | None:
    """Build the explicit requests/PySocks URL without consulting the environment."""

    proxy = _coerce_settings(proxy)
    if not proxy.enabled:
        return None

    if password is None and secrets is not None:
        stored = secrets.get(proxy.password_secret)
        if stored is not None and not isinstance(stored, str):
            raise ProxyError(
                "proxy_credentials_invalid",
                "The stored proxy password has an invalid type.",
            )
        password = stored

    credentials = ""
    if proxy.username is not None:
        username = quote(proxy.username, safe="")
        credentials = username
        if password is not None:
            credentials += f":{quote(password, safe='')}"
        credentials += "@"

    return f"{_proxy_scheme(proxy)}://{credentials}{_format_host(proxy.host)}:{proxy.port}"


class ProxySession(requests.Session):
    """A requests session with a default timeout and stable proxy error codes."""

    def __init__(self, *, timeout: float | tuple[float, float], proxy_enabled: bool):
        super().__init__()
        self.default_timeout = timeout
        self.proxy_enabled = proxy_enabled
        # Never inherit Firefox, Windows, or shell proxy configuration. Every
        # external request uses the ProxySettings supplied to this session.
        self.trust_env = False

    def request(self, method: str, url: str, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", self.default_timeout)
        try:
            return super().request(method, url, **kwargs)
        except requests.exceptions.ProxyError as exc:
            code, message = _proxy_failure(exc)
            raise ProxyError(
                code,
                message,
                retryable=True,
            ) from exc
        except requests.exceptions.ConnectTimeout as exc:
            if not self.proxy_enabled:
                raise
            raise ProxyError(
                "proxy_timeout",
                "The configured SOCKS proxy did not respond before the connection timeout.",
                retryable=True,
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            if not self.proxy_enabled:
                raise
            code, message = _proxy_failure(exc)
            raise ProxyError(
                code,
                message,
                retryable=True,
            ) from exc


def requests_session(
    proxy: ProxySettings | dict[str, Any],
    secrets: SecretStore | None = None,
    *,
    timeout: float | tuple[float, float] = (15.0, 180.0),
) -> ProxySession:
    """Create an isolated session for all non-loopback HTTP requests."""

    proxy = _coerce_settings(proxy)
    session = ProxySession(timeout=timeout, proxy_enabled=proxy.enabled)
    proxy_url = build_proxy_url(proxy, secrets)
    if proxy_url is not None:
        session.proxies.update({"http": proxy_url, "https": proxy_url})
    else:
        # Explicitly empty, combined with trust_env=False, means direct access.
        session.proxies.clear()
    session.headers.update({"User-Agent": "ZoteroQuickRead/0.1"})
    return session
