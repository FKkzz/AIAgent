from __future__ import annotations

import socket
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

from .auth import EXPECTED_ISSUER, OIDC_CONFIGURATION_ENDPOINT, ChatGPTAuth
from .config import AppSettings
from .errors import QuickReadError
from .openai_client import OpenAIClient
from .proxy import build_proxy_url, requests_session
from .secrets import SecretStore


@dataclass(slots=True)
class DiagnosticStep:
    name: str
    status: str
    code: str
    message: str
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_diagnostics(
    settings: AppSettings,
    secrets: SecretStore,
    *,
    include_models: bool = False,
    include_inference: bool = False,
) -> list[DiagnosticStep]:
    steps: list[DiagnosticStep] = []
    firefox = Path(settings.firefox_path)
    steps.append(
        DiagnosticStep(
            "firefox",
            "ok" if firefox.is_file() else "fail",
            "firefox_found" if firefox.is_file() else "firefox_not_found",
            "已找到授权用 Firefox。" if firefox.is_file() else "配置的 Firefox 路径不存在。",
        )
    )

    proxy_url = build_proxy_url(settings.proxy, secrets)
    scheme = proxy_url.split(":", 1)[0] if proxy_url else "direct"
    steps.append(
        DiagnosticStep(
            "proxy_configuration",
            "ok",
            "proxy_configured" if proxy_url else "direct_configured",
            (
                f"显式使用 {scheme}；DNS 在"
                f"{'代理端' if settings.proxy.dns == 'proxy' else '本机'}解析。"
                if proxy_url
                else "未启用代理；外部请求将显式直连且不继承环境代理。"
            ),
            {
                "enabled": settings.proxy.enabled,
                "version": settings.proxy.version,
                "dns": settings.proxy.dns,
                "scheme": scheme,
            },
        )
    )

    if settings.proxy.enabled:
        try:
            with socket.create_connection((settings.proxy.host, settings.proxy.port), timeout=5):
                pass
            steps.append(
                DiagnosticStep("proxy_tcp", "ok", "proxy_tcp_ok", "SOCKS 代理 TCP 端口可达。")
            )
        except OSError as exc:
            steps.append(
                DiagnosticStep(
                    "proxy_tcp",
                    "fail",
                    "proxy_tcp_failed",
                    f"SOCKS 代理 TCP 端口不可达：{type(exc).__name__}。",
                )
            )
            return steps
    else:
        steps.append(DiagnosticStep("proxy_tcp", "skip", "proxy_disabled", "未启用代理。"))

    session = requests_session(
        settings.proxy,
        secrets,
        timeout=(10.0, min(float(settings.request_timeout_seconds), 30.0)),
    )
    try:
        response = session.get(OIDC_CONFIGURATION_ENDPOINT)
        response.raise_for_status()
        discovery = response.json()
        if discovery.get("issuer", "").rstrip("/") != EXPECTED_ISSUER:
            raise ValueError("unexpected issuer")
        jwks_uri = discovery.get("jwks_uri")
        if not isinstance(jwks_uri, str):
            raise ValueError("missing jwks_uri")
        jwks_response = session.get(jwks_uri)
        jwks_response.raise_for_status()
        keys = jwks_response.json().get("keys")
        if not isinstance(keys, list) or not keys:
            raise ValueError("empty JWKS")
        steps.append(
            DiagnosticStep(
                "oidc_and_jwks",
                "ok",
                "oidc_ok",
                "已通过配置的网络路径读取并校验 OpenAI OIDC 配置与签名密钥。",
            )
        )
    except QuickReadError as exc:
        steps.append(DiagnosticStep("oidc_and_jwks", "fail", exc.code, exc.message))
        return steps
    except (requests.RequestException, ValueError) as exc:
        steps.append(
            DiagnosticStep(
                "oidc_and_jwks",
                "fail",
                "oidc_failed",
                f"OIDC/JWKS 连接失败：{type(exc).__name__}。",
            )
        )
        return steps

    auth = ChatGPTAuth(settings, secrets, session=session)
    status = auth.status() if hasattr(auth, "status") else _fallback_auth_status(secrets)
    if status.get("signed_in"):
        scopes = status.get("scopes") or []
        direct = "chatgpt.tokens.use.direct" in scopes
        steps.append(
            DiagnosticStep(
                "chatgpt_authorization",
                "ok" if direct else "fail",
                "chatgpt_plan_scope_ok" if direct else "chatgpt_plan_scope_missing",
                (
                    "已保存含 ChatGPT plan usage 权限的会话。"
                    if direct
                    else "已登录，但未授予 ChatGPT plan usage 权限。"
                ),
            )
        )
    elif settings.auth_mode == "api_key" and isinstance(secrets.get("openai_api_key"), str):
        steps.append(
            DiagnosticStep(
                "authentication",
                "ok",
                "api_key_configured",
                "当前显式选择 API Key 模式；不会自动切换认证模式。",
            )
        )
    else:
        steps.append(
            DiagnosticStep(
                "authentication",
                "fail",
                "reauthorization_required",
                "尚未完成当前认证模式所需的登录。",
            )
        )
        return steps

    client = OpenAIClient(settings, secrets, auth=auth, session=session)
    if include_models:
        try:
            models = client.list_models()
            steps.append(
                DiagnosticStep(
                    "models",
                    "ok" if models else "fail",
                    "models_available" if models else "model_catalog_empty",
                    f"账号返回 {len(models)} 个可显示模型。",
                    {"models": models},
                )
            )
        except QuickReadError as exc:
            steps.append(DiagnosticStep("models", "fail", exc.code, exc.message))
            return steps
    if include_inference:
        try:
            result = client.minimal_request()
            steps.append(
                DiagnosticStep(
                    "inference",
                    "ok",
                    "response_completed",
                    "最小流式请求收到 response.completed。",
                    {"model": result.model, "text": result.text, "usage": result.usage},
                )
            )
        except QuickReadError as exc:
            steps.append(DiagnosticStep("inference", "fail", exc.code, exc.message))
    return steps


def _fallback_auth_status(secrets: SecretStore) -> dict[str, Any]:
    credentials = secrets.get("chatgpt.credentials", {})
    return {
        "signed_in": isinstance(credentials, dict) and bool(credentials.get("access_token")),
        "scopes": credentials.get("scopes", []) if isinstance(credentials, dict) else [],
    }
