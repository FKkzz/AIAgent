from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets as random_secrets
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
import requests

from .config import AppSettings
from .errors import AuthenticationError, ProxyError
from .proxy import requests_session
from .secrets import SecretStore

AUTHORIZATION_ENDPOINT = "https://auth.openai.com/api/accounts/authorize"
TOKEN_ENDPOINT = "https://auth.openai.com/api/accounts/oauth/token"  # noqa: S105
OIDC_CONFIGURATION_ENDPOINT = "https://auth.openai.com/.well-known/openid-configuration"
EXPECTED_ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
DYNAMIC_CLIENT_ID = "dynamic_agent_client"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"
REQUESTED_SCOPES = (
    "openid",
    "profile",
    "email",
    "offline_access",
    "resource.invoke",
    DIRECT_SCOPE,
)
_CREDENTIAL_KEY = "chatgpt.credentials"
_HOST_ID_KEY = "chatgpt.host_id"
_REFRESH_TERMINAL_ERRORS = {
    "invalid_grant",
    "invalid_refresh_token",
    "token_expired",
    "refresh_token_expired",
    "refresh_token_invalidated",
    "refresh_token_reused",
}
_SAFE_JWT_ALGORITHMS = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256"}


@dataclass(frozen=True)
class AuthorizationAttempt:
    authorization_url: str
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    requested_client_id: str
    new_registration: bool


def generate_pkce() -> tuple[str, str]:
    verifier = random_secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _scopes(value: Any) -> list[str]:
    if isinstance(value, str):
        items = value.split()
    elif isinstance(value, list):
        items = [item for item in value if isinstance(item, str)]
    else:
        return []
    return list(dict.fromkeys(item.strip() for item in items if item.strip()))


def _safe_message(payload: Any, fallback: str) -> str:
    if not isinstance(payload, dict):
        return fallback
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("code")
    elif isinstance(error, str):
        message = payload.get("error_description") or error
    else:
        message = payload.get("detail") or payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return fallback
    return message.strip()[:500]


def _error_code(payload: Any, fallback: str) -> str:
    if not isinstance(payload, dict):
        return fallback
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    if isinstance(error, str):
        return error
    if isinstance(payload.get("code"), str):
        return payload["code"]
    return fallback


class ChatGPTAuth:
    """Official Sign in with ChatGPT public-client flow for one selected account."""

    def __init__(
        self,
        config: AppSettings,
        secrets: SecretStore,
        *,
        session: requests.Session | None = None,
        agent_name: str = "Zotero Quick Read",
    ) -> None:
        self.config = config
        self.secrets = secrets
        self.agent_name = agent_name
        self.session = session or requests_session(
            config.proxy,
            secrets,
            timeout=(15.0, float(config.request_timeout_seconds)),
        )
        self._pending: dict[str, AuthorizationAttempt] = {}
        self._refresh_lock = threading.Lock()

    def get_or_create_host_id(self) -> str:
        host_id = self.secrets.get(_HOST_ID_KEY)
        if isinstance(host_id, str) and host_id.startswith("urn:uuid:"):
            return host_id
        host_id = f"urn:uuid:{uuid.uuid4()}"
        self.secrets.set(_HOST_ID_KEY, host_id)
        return host_id

    def create_authorization_request(
        self,
        redirect_uri: str,
        *,
        force_new_registration: bool = False,
    ) -> AuthorizationAttempt:
        self._validate_redirect_uri(redirect_uri)
        credentials = self._load_credentials()
        issued_client_id = credentials.get("client_id") if credentials else None
        new_registration = force_new_registration or not isinstance(issued_client_id, str)
        requested_client_id = DYNAMIC_CLIENT_ID if new_registration else issued_client_id

        state = random_secrets.token_urlsafe(32)
        nonce = random_secrets.token_urlsafe(32)
        verifier, challenge = generate_pkce()
        query: dict[str, str] = {
            "client_id": requested_client_id,
            "ext_agent_host_id": self.get_or_create_host_id(),
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(REQUESTED_SCOPES),
            "resource": RESOURCE,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        if new_registration:
            query["agent_name_hint"] = self.agent_name
        else:
            retained_id_token = credentials.get("id_token")
            email = credentials.get("email")
            if isinstance(retained_id_token, str) and retained_id_token:
                query["id_token_hint"] = retained_id_token
            if isinstance(email, str) and email:
                query["login_hint"] = email

        attempt = AuthorizationAttempt(
            authorization_url=f"{AUTHORIZATION_ENDPOINT}?{urlencode(query)}",
            state=state,
            nonce=nonce,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            requested_client_id=requested_client_id,
            new_registration=new_registration,
        )
        # A single process only needs one interactive attempt at a time. Removing
        # older attempts also prevents a stale callback from becoming usable.
        self._pending.clear()
        self._pending[state] = attempt
        return attempt

    def login(
        self,
        *,
        timeout_seconds: float = 300.0,
        force_new_registration: bool = False,
        browser_opener: Callable[[str], Any] | None = None,
    ) -> dict[str, Any]:
        callback: dict[str, list[str]] = {}
        callback_received = threading.Event()

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(handler_self) -> None:  # noqa: N802
                parsed = urlparse(handler_self.path)
                if parsed.path != "/auth/callback":
                    handler_self.send_error(404)
                    return
                callback.update(parse_qs(parsed.query, keep_blank_values=True))
                body = (
                    b"<!doctype html><meta charset=utf-8>"
                    b"<title>Zotero Quick Read</title>"
                    b"<p>Authorization received. You may close this tab.</p>"
                )
                handler_self.send_response(200)
                handler_self.send_header("Content-Type", "text/html; charset=utf-8")
                handler_self.send_header("Cache-Control", "no-store")
                handler_self.send_header("Content-Length", str(len(body)))
                handler_self.end_headers()
                handler_self.wfile.write(body)
                callback_received.set()

            def log_message(self, _format: str, *_args: Any) -> None:
                # Callback URLs contain authorization material and must not be logged.
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
        server.daemon_threads = True
        port = int(server.server_address[1])
        redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
        attempt = self.create_authorization_request(
            redirect_uri,
            force_new_registration=force_new_registration,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            if browser_opener is not None:
                browser_opener(attempt.authorization_url)
            else:
                self._open_firefox(attempt.authorization_url)
            if not callback_received.wait(timeout_seconds):
                self._pending.pop(attempt.state, None)
                raise AuthenticationError(
                    "oauth_timeout",
                    "Timed out waiting for the browser authorization callback.",
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)
        return self.complete_login(callback)

    def complete_login(self, callback_params: Mapping[str, Any]) -> dict[str, Any]:
        params = self._single_value_params(callback_params)
        returned_state = params.get("state", "")
        attempt = next(
            (
                item
                for state, item in self._pending.items()
                if hmac.compare_digest(state, returned_state)
            ),
            None,
        )
        if attempt is None:
            raise AuthenticationError(
                "oauth_state_invalid",
                "The authorization callback state is missing, expired, or invalid.",
            )
        # Consume before doing any network work, so callbacks cannot be replayed.
        self._pending.pop(attempt.state, None)

        oauth_error = params.get("error")
        if oauth_error:
            message = params.get("error_description") or "Authorization was not completed."
            raise AuthenticationError(oauth_error, message[:500])
        code = params.get("code")
        if not code:
            raise AuthenticationError("oauth_code_missing", "The callback did not include a code.")

        callback_client_id = params.get("client_id")
        if attempt.new_registration:
            if not callback_client_id or callback_client_id == DYNAMIC_CLIENT_ID:
                raise AuthenticationError(
                    "dynamic_registration_incomplete",
                    "OpenAI did not return an issued client ID for this registration.",
                )
            issued_client_id = callback_client_id
        else:
            issued_client_id = attempt.requested_client_id
            if callback_client_id and not hmac.compare_digest(callback_client_id, issued_client_id):
                raise AuthenticationError(
                    "oauth_client_mismatch",
                    "The callback client ID does not match the selected registration.",
                )

        token_payload = self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": issued_client_id,
                "code": code,
                "code_verifier": attempt.code_verifier,
                "redirect_uri": attempt.redirect_uri,
                "resource": RESOURCE,
            },
            operation="authorization code exchange",
        )
        credentials = self._credentials_from_login_token(
            token_payload,
            client_id=issued_client_id,
            nonce=attempt.nonce,
        )

        previous = self._load_credentials()
        if (
            previous
            and not attempt.new_registration
            and previous.get("subject")
            and previous.get("subject") != credentials.get("subject")
        ):
            raise AuthenticationError(
                "oauth_identity_mismatch",
                "The signed-in identity does not match the selected ChatGPT account.",
            )
        self.secrets.set(_CREDENTIAL_KEY, credentials)
        if DIRECT_SCOPE not in credentials["scopes"]:
            raise AuthenticationError(
                "chatgpt_plan_scope_missing",
                "Sign-in succeeded, but ChatGPT plan usage permission was not granted.",
            )
        return credentials

    def refresh(self) -> dict[str, Any]:
        with self._refresh_lock:
            credentials = self._load_credentials()
            if not credentials:
                raise AuthenticationError(
                    "reauthorization_required",
                    "No saved ChatGPT authorization is available.",
                )
            client_id = credentials.get("client_id")
            refresh_token = credentials.get("refresh_token")
            if not isinstance(client_id, str) or not isinstance(refresh_token, str):
                raise AuthenticationError(
                    "reauthorization_required",
                    "The saved ChatGPT session cannot be refreshed; sign in again.",
                )
            try:
                payload = self._token_request(
                    {
                        "grant_type": "refresh_token",
                        "client_id": client_id,
                        "refresh_token": refresh_token,
                        "resource": RESOURCE,
                    },
                    operation="token refresh",
                )
            except AuthenticationError as exc:
                if exc.code in _REFRESH_TERMINAL_ERRORS:
                    self._clear_tokens(credentials)
                    raise AuthenticationError(
                        "reauthorization_required",
                        "The ChatGPT session can no longer be refreshed; sign in again.",
                    ) from exc
                raise

            access_token = payload.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise AuthenticationError(
                    "token_response_invalid",
                    "The refresh response did not contain an access token.",
                )
            scopes = _scopes(payload.get("scope")) or list(credentials.get("scopes", []))
            expires_in = self._expires_in(payload)
            updated = dict(credentials)
            updated.update(
                {
                    "access_token": access_token,
                    "refresh_token": payload.get("refresh_token") or refresh_token,
                    "token_type": payload.get("token_type")
                    or credentials.get("token_type", "Bearer"),
                    "expires_in": expires_in,
                    "expires_at": time.time() + expires_in,
                    "scopes": scopes,
                    "saved_at": _utc_now(),
                }
            )
            new_id_token = payload.get("id_token")
            if isinstance(new_id_token, str) and new_id_token:
                claims = self.validate_id_token(new_id_token, client_id=client_id, nonce=None)
                if claims.get("sub") != credentials.get("subject"):
                    raise AuthenticationError(
                        "oauth_identity_mismatch",
                        "The refreshed identity does not match the saved ChatGPT account.",
                    )
                updated["id_token"] = new_id_token
                updated["email"] = claims.get("email") or updated.get("email")
                updated["issuer"] = claims["iss"]
            # Save a rotated refresh token atomically with its access token even
            # when permission was removed, otherwise the old token may be lost.
            self.secrets.set(_CREDENTIAL_KEY, updated)
            if DIRECT_SCOPE not in scopes:
                raise AuthenticationError(
                    "chatgpt_plan_scope_missing",
                    "The refreshed session lacks ChatGPT plan usage permission.",
                )
            return updated

    def get_access_token(self, *, refresh_skew_seconds: float = 60.0) -> str:
        credentials = self._load_credentials()
        if not credentials or DIRECT_SCOPE not in credentials.get("scopes", []):
            raise AuthenticationError(
                "reauthorization_required",
                "Sign in with ChatGPT and grant plan usage permission.",
            )
        expires_at = credentials.get("expires_at")
        if (
            not isinstance(expires_at, (int, float))
            or expires_at <= time.time() + refresh_skew_seconds
        ):
            credentials = self.refresh()
        token = credentials.get("access_token")
        if not isinstance(token, str) or not token:
            raise AuthenticationError(
                "reauthorization_required",
                "The saved ChatGPT access token is unavailable.",
            )
        return token

    def status(self) -> dict[str, Any]:
        """Return a UI-safe view that never exposes bearer or refresh credentials."""

        credentials = self._load_credentials()
        scopes = _scopes(credentials.get("scopes"))
        signed_in = all(
            isinstance(credentials.get(key), str) and bool(credentials.get(key))
            for key in ("client_id", "access_token", "refresh_token")
        ) and DIRECT_SCOPE in scopes
        return {
            "signed_in": signed_in,
            "client_id": credentials.get("client_id")
            if isinstance(credentials.get("client_id"), str)
            else None,
            "email": credentials.get("email")
            if isinstance(credentials.get("email"), str)
            else None,
            "scopes": list(scopes),
            "expires_at": credentials.get("expires_at")
            if isinstance(credentials.get("expires_at"), (int, float))
            else None,
        }

    def logout(self, *, revoke: bool = True) -> dict[str, Any]:
        """End the renewable session before deleting its protected local record."""

        credentials = self._load_credentials()
        if not credentials:
            return self.status()
        refresh_token = credentials.get("refresh_token")
        client_id = credentials.get("client_id")
        if revoke and isinstance(refresh_token, str) and refresh_token:
            if not isinstance(client_id, str) or not client_id:
                raise AuthenticationError(
                    "oauth_client_missing",
                    "The saved session has no issued client ID and cannot be revoked safely.",
                )
            discovery = self._get_json(OIDC_CONFIGURATION_ENDPOINT, "OIDC discovery")
            endpoint = discovery.get("revocation_endpoint")
            parsed_endpoint = urlparse(endpoint) if isinstance(endpoint, str) else None
            if (
                parsed_endpoint is None
                or parsed_endpoint.scheme != "https"
                or parsed_endpoint.hostname != "auth.openai.com"
            ):
                raise AuthenticationError(
                    "oidc_configuration_invalid",
                    "OpenAI OIDC discovery returned an unexpected revocation endpoint.",
                )
            try:
                response = self.session.post(
                    endpoint,
                    data={
                        "token": refresh_token,
                        "token_type_hint": "refresh_token",
                        "client_id": client_id,
                    },
                    headers={"Accept": "application/json"},
                )
            except ProxyError:
                raise
            except requests.exceptions.Timeout as exc:
                raise AuthenticationError(
                    "oauth_revoke_timeout",
                    "OpenAI session revocation timed out; local credentials were retained.",
                    retryable=True,
                ) from exc
            except requests.exceptions.RequestException as exc:
                raise AuthenticationError(
                    "oauth_revoke_network_error",
                    "OpenAI session revocation failed; local credentials were retained.",
                    retryable=True,
                ) from exc
            payload = self._response_json(response)
            status_code = response.status_code
            response.close()
            if not 200 <= status_code < 300:
                code = _error_code(payload, f"oauth_revoke_http_{status_code}")
                raise AuthenticationError(
                    code,
                    _safe_message(
                        payload,
                        "OpenAI session revocation failed; local credentials were retained.",
                    ),
                    retryable=status_code >= 500,
                )
        # Explicit local-only sign-out (revoke=False), a confirmed revocation,
        # or a record without a renewable token can now be removed safely.
        self.secrets.delete(_CREDENTIAL_KEY)
        return self.status()

    def validate_id_token(
        self,
        id_token: str,
        *,
        client_id: str,
        nonce: str | None,
    ) -> dict[str, Any]:
        discovery = self._get_json(OIDC_CONFIGURATION_ENDPOINT, "OIDC discovery")
        issuer = discovery.get("issuer")
        if not isinstance(issuer, str) or issuer.rstrip("/") != EXPECTED_ISSUER:
            raise AuthenticationError(
                "oidc_configuration_invalid",
                "OpenAI OIDC discovery returned an unexpected issuer.",
            )
        jwks_uri = discovery.get("jwks_uri")
        parsed_jwks = urlparse(jwks_uri) if isinstance(jwks_uri, str) else None
        if (
            parsed_jwks is None
            or parsed_jwks.scheme != "https"
            or parsed_jwks.hostname != "auth.openai.com"
        ):
            raise AuthenticationError(
                "oidc_configuration_invalid",
                "OpenAI OIDC discovery returned an unexpected JWKS endpoint.",
            )
        jwks = self._get_json(jwks_uri, "OIDC signing keys")
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise AuthenticationError(
                "id_token_invalid",
                "The ID token header is invalid.",
            ) from exc
        algorithm = header.get("alg")
        key_id = header.get("kid")
        advertised = discovery.get("id_token_signing_alg_values_supported", [])
        if (
            not isinstance(algorithm, str)
            or algorithm not in _SAFE_JWT_ALGORITHMS
            or (isinstance(advertised, list) and advertised and algorithm not in advertised)
        ):
            raise AuthenticationError(
                "id_token_algorithm_invalid",
                "The ID token uses an unapproved signing algorithm.",
            )
        keys = jwks.get("keys") if isinstance(jwks, dict) else None
        if not isinstance(keys, list):
            raise AuthenticationError("jwks_invalid", "OpenAI returned an invalid JWKS document.")
        key_data = next(
            (
                item
                for item in keys
                if isinstance(item, dict)
                and isinstance(key_id, str)
                and item.get("kid") == key_id
            ),
            None,
        )
        if key_data is None:
            raise AuthenticationError(
                "id_token_key_unknown",
                "No matching OpenAI signing key was found.",
            )
        try:
            signing_key = jwt.PyJWK.from_dict(key_data, algorithm=algorithm).key
            claims = jwt.decode(
                id_token,
                signing_key,
                algorithms=[algorithm],
                audience=client_id,
                issuer=issuer,
                leeway=5,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError(
                "id_token_invalid",
                "The ID token signature or required claims are invalid.",
            ) from exc
        if nonce is not None:
            returned_nonce = claims.get("nonce")
            if not isinstance(returned_nonce, str) or not hmac.compare_digest(
                returned_nonce,
                nonce,
            ):
                raise AuthenticationError(
                    "id_token_nonce_invalid",
                    "The ID token nonce does not match this authorization attempt.",
                )
        audience = claims.get("aud")
        if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != client_id:
            raise AuthenticationError(
                "id_token_authorized_party_invalid",
                "The ID token authorized party does not match this client.",
            )
        return claims

    def _credentials_from_login_token(
        self,
        payload: dict[str, Any],
        *,
        client_id: str,
        nonce: str,
    ) -> dict[str, Any]:
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        id_token = payload.get("id_token")
        required_tokens = (access_token, refresh_token, id_token)
        if not all(isinstance(value, str) and value for value in required_tokens):
            raise AuthenticationError(
                "token_response_invalid",
                "OpenAI returned an incomplete token response.",
            )
        claims = self.validate_id_token(id_token, client_id=client_id, nonce=nonce)
        scopes = _scopes(payload.get("scope"))
        expires_in = self._expires_in(payload)
        return {
            "email": claims.get("email"),
            "issuer": claims["iss"],
            "subject": claims["sub"],
            "client_id": client_id,
            "ext_agent_host_id": self.get_or_create_host_id(),
            "id_token": id_token,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": payload.get("token_type") or "Bearer",
            "expires_in": expires_in,
            "expires_at": time.time() + expires_in,
            "scopes": scopes,
            "saved_at": _utc_now(),
        }

    def _token_request(self, data: dict[str, str], *, operation: str) -> dict[str, Any]:
        try:
            response = self.session.post(
                TOKEN_ENDPOINT,
                data=data,
                headers={"Accept": "application/json"},
            )
        except ProxyError:
            raise
        except requests.exceptions.Timeout as exc:
            raise AuthenticationError(
                "oauth_network_timeout",
                f"OpenAI {operation} timed out.",
                retryable=True,
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise AuthenticationError(
                "oauth_network_error",
                f"OpenAI {operation} could not be reached.",
                retryable=True,
            ) from exc
        payload = self._response_json(response)
        status_code = response.status_code
        response.close()
        if not 200 <= status_code < 300:
            code = _error_code(payload, f"oauth_http_{status_code}")
            raise AuthenticationError(
                code,
                _safe_message(payload, f"OpenAI {operation} failed (HTTP {status_code})."),
                retryable=status_code >= 500,
            )
        if not isinstance(payload, dict):
            raise AuthenticationError(
                "token_response_invalid",
                "OpenAI returned a non-object token response.",
            )
        return payload

    def _get_json(self, url: str, operation: str) -> dict[str, Any]:
        try:
            response = self.session.get(url, headers={"Accept": "application/json"})
        except ProxyError:
            raise
        except requests.exceptions.Timeout as exc:
            raise AuthenticationError(
                "oidc_network_timeout",
                f"OpenAI {operation} timed out.",
                retryable=True,
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise AuthenticationError(
                "oidc_network_error",
                f"OpenAI {operation} could not be reached.",
                retryable=True,
            ) from exc
        payload = self._response_json(response)
        status_code = response.status_code
        response.close()
        if not 200 <= status_code < 300 or not isinstance(payload, dict):
            raise AuthenticationError(
                "oidc_endpoint_error",
                f"OpenAI {operation} failed (HTTP {status_code}).",
                retryable=status_code >= 500,
            )
        return payload

    @staticmethod
    def _response_json(response: requests.Response) -> Any:
        try:
            return response.json()
        except (requests.exceptions.JSONDecodeError, json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _expires_in(payload: dict[str, Any]) -> float:
        value = payload.get("expires_in")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise AuthenticationError(
                "token_response_invalid",
                "OpenAI returned an invalid token lifetime.",
            )
        return float(value)

    def _load_credentials(self) -> dict[str, Any]:
        value = self.secrets.get(_CREDENTIAL_KEY, {})
        return dict(value) if isinstance(value, dict) else {}

    def _clear_tokens(self, credentials: dict[str, Any]) -> None:
        retained = {
            key: credentials[key]
            for key in ("client_id", "subject", "email", "issuer", "ext_agent_host_id")
            if key in credentials
        }
        self.secrets.set(_CREDENTIAL_KEY, retained)

    def _open_firefox(self, authorization_url: str) -> None:
        firefox = Path(self.config.firefox_path)
        if not firefox.is_file():
            raise AuthenticationError(
                "firefox_not_found",
                f"Configured Firefox executable was not found: {firefox}",
            )
        try:
            subprocess.Popen(  # noqa: S603
                [str(firefox), authorization_url],
                close_fds=True,
            )
        except OSError as exc:
            raise AuthenticationError(
                "firefox_launch_failed",
                "Firefox could not be started for authorization.",
            ) from exc

    @staticmethod
    def _validate_redirect_uri(redirect_uri: str) -> None:
        parsed = urlparse(redirect_uri)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or parsed.path != "/auth/callback"
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("redirect_uri must be an exact 127.0.0.1 HTTP loopback callback")

    @staticmethod
    def _single_value_params(params: Mapping[str, Any]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, raw_value in params.items():
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            if len(values) != 1 or not isinstance(values[0], str):
                raise AuthenticationError(
                    "oauth_callback_invalid",
                    "The authorization callback contains duplicate or invalid parameters.",
                )
            result[str(key)] = values[0]
        return result
