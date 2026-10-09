from __future__ import annotations

import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa

from zotero_quick_read.auth import (
    DIRECT_SCOPE,
    DYNAMIC_CLIENT_ID,
    EXPECTED_ISSUER,
    ChatGPTAuth,
)
from zotero_quick_read.config import AppSettings
from zotero_quick_read.errors import AuthenticationError, ModelResponseError, QuotaError
from zotero_quick_read.openai_client import OpenAIClient, parse_sse_events
from zotero_quick_read.secrets import SecretStore


class _FakeResponse:
    def __init__(self, lines=(), *, status=200, payload=None, headers=None):
        self._lines = list(lines)
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.closed = False

    def iter_lines(self, decode_unicode=True):
        del decode_unicode
        yield from self._lines

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload

    def close(self):
        self.closed = True


class _FakeAuth:
    def __init__(self, access_value="oauth-token", error=None):
        self.token = access_value
        self.error = error
        self.calls = 0

    def get_access_token(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.token


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


def _client(tmp_path, *, auth=None, session=None, auth_mode="chatgpt"):
    settings = AppSettings(auth_mode=auth_mode, selected_model="available-model")
    return OpenAIClient(
        settings,
        SecretStore(tmp_path),
        auth=auth or _FakeAuth(),
        session=session or _FakeSession([]),
    )


def _event(payload):
    return [f"data: {json.dumps(payload)}", ""]


def test_sse_parser_supports_comments_event_names_and_multiline_data():
    parsed = list(
        parse_sse_events(
            [": keepalive", "event: custom", 'data: {"a":', "data: 1}", ""]
        )
    )
    assert parsed == [("custom", '{"a":\n1}')]


def test_authorization_request_uses_dynamic_registration_pkce_and_stable_host(tmp_path):
    secrets = SecretStore(tmp_path)
    auth = ChatGPTAuth(AppSettings(), secrets, session=_FakeSession([]))
    first = auth.create_authorization_request("http://127.0.0.1:24567/auth/callback")
    query = parse_qs(urlparse(first.authorization_url).query)

    assert query["client_id"] == [DYNAMIC_CLIENT_ID]
    assert query["state"] == [first.state]
    assert query["nonce"] == [first.nonce]
    assert query["code_challenge_method"] == ["S256"]
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(first.code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert query["code_challenge"] == [expected_challenge]
    assert DIRECT_SCOPE in query["scope"][0].split()

    second = auth.create_authorization_request("http://127.0.0.1:24568/auth/callback")
    second_query = parse_qs(urlparse(second.authorization_url).query)
    assert second_query["ext_agent_host_id"] == query["ext_agent_host_id"]


def test_oauth_callback_rejects_wrong_state_without_token_exchange(tmp_path):
    session = _FakeSession([])
    auth = ChatGPTAuth(AppSettings(), SecretStore(tmp_path), session=session)
    auth.create_authorization_request("http://127.0.0.1:24567/auth/callback")

    with pytest.raises(AuthenticationError) as caught:
        auth.complete_login({"state": "attacker-state", "code": "code", "client_id": "issued"})

    assert caught.value.code == "oauth_state_invalid"
    assert session.requests == []


def _integer_b64url(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_id_token_checks_jwks_signature_audience_and_nonce(tmp_path):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": "test-key",
        "n": _integer_b64url(numbers.n),
        "e": _integer_b64url(numbers.e),
    }
    discovery = {
        "issuer": EXPECTED_ISSUER,
        "jwks_uri": f"{EXPECTED_ISSUER}/.well-known/jwks.json",
        "id_token_signing_alg_values_supported": ["RS256"],
    }
    now = int(time.time())
    encoded = jwt.encode(
        {
            "iss": EXPECTED_ISSUER,
            "aud": "issued-client",
            "sub": "account-subject",
            "iat": now,
            "exp": now + 300,
            "nonce": "expected-nonce",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    session = _FakeSession(
        [
            _FakeResponse(payload=discovery),
            _FakeResponse(payload={"keys": [jwk]}),
            _FakeResponse(payload=discovery),
            _FakeResponse(payload={"keys": [jwk]}),
        ]
    )
    auth = ChatGPTAuth(AppSettings(), SecretStore(tmp_path), session=session)

    claims = auth.validate_id_token(
        encoded,
        client_id="issued-client",
        nonce="expected-nonce",
    )
    assert claims["sub"] == "account-subject"

    with pytest.raises(AuthenticationError) as caught:
        auth.validate_id_token(encoded, client_id="issued-client", nonce="wrong-nonce")
    assert caught.value.code == "id_token_nonce_invalid"


def test_refresh_rotates_tokens_and_omits_scope_parameter(tmp_path):
    secrets = SecretStore(tmp_path)
    secrets.set(
        "chatgpt.credentials",
        {
            "client_id": "issued-client",
            "subject": "account-subject",
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "scopes": [DIRECT_SCOPE, "offline_access"],
            "expires_at": 0,
        },
    )
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "expires_in": 3600,
                    "scope": f"{DIRECT_SCOPE} offline_access",
                    "token_type": "Bearer",
                }
            )
        ]
    )
    auth = ChatGPTAuth(AppSettings(), secrets, session=session)

    refreshed = auth.refresh()

    assert refreshed["access_token"] == "new-access"  # noqa: S105
    assert secrets.get("chatgpt.credentials")["refresh_token"] == "new-refresh"  # noqa: S105
    posted = session.requests[0][2]["data"]
    assert posted["grant_type"] == "refresh_token"
    assert "scope" not in posted


def test_auth_status_is_redacted(tmp_path):
    secrets = SecretStore(tmp_path)
    secrets.set(
        "chatgpt.credentials",
        {
            "client_id": "issued-client",
            "email": "reader@example.test",
            "access_token": "private-access",
            "refresh_token": "private-refresh",
            "id_token": "private-id",
            "scopes": [DIRECT_SCOPE, "offline_access"],
            "expires_at": 12345.0,
        },
    )
    status = ChatGPTAuth(AppSettings(), secrets, session=_FakeSession([])).status()

    assert status == {
        "signed_in": True,
        "client_id": "issued-client",
        "email": "reader@example.test",
        "scopes": [DIRECT_SCOPE, "offline_access"],
        "expires_at": 12345.0,
    }
    assert not {"access_token", "refresh_token", "id_token"}.intersection(status)


def test_logout_revokes_through_discovered_endpoint_then_deletes_credentials(tmp_path):
    secrets = SecretStore(tmp_path)
    secrets.set(
        "chatgpt.credentials",
        {
            "client_id": "issued-client",
            "access_token": "private-access",
            "refresh_token": "private-refresh",
            "scopes": [DIRECT_SCOPE],
        },
    )
    revoke_endpoint = f"{EXPECTED_ISSUER}/api/accounts/oauth/revoke"
    session = _FakeSession(
        [
            _FakeResponse(
                payload={"issuer": EXPECTED_ISSUER, "revocation_endpoint": revoke_endpoint}
            ),
            _FakeResponse(payload={}),
        ]
    )
    auth = ChatGPTAuth(AppSettings(), secrets, session=session)

    status = auth.logout()

    assert status["signed_in"] is False
    assert secrets.get("chatgpt.credentials") is None
    assert session.requests[1][1] == revoke_endpoint
    assert session.requests[1][2]["data"]["token_type_hint"] == "refresh_token"  # noqa: S105


def test_logout_network_failure_retains_credentials(tmp_path):
    secrets = SecretStore(tmp_path)
    credentials = {
        "client_id": "issued-client",
        "access_token": "private-access",
        "refresh_token": "private-refresh",
        "scopes": [DIRECT_SCOPE],
    }
    secrets.set("chatgpt.credentials", credentials)
    session = _FakeSession(
        [
            _FakeResponse(
                payload={
                    "issuer": EXPECTED_ISSUER,
                    "revocation_endpoint": f"{EXPECTED_ISSUER}/api/accounts/oauth/revoke",
                }
            ),
            requests.ConnectionError("offline"),
        ]
    )
    auth = ChatGPTAuth(AppSettings(), secrets, session=session)

    with pytest.raises(AuthenticationError) as caught:
        auth.logout()

    assert caught.value.code == "oauth_revoke_network_error"
    assert secrets.get("chatgpt.credentials") == credentials


def test_completed_event_is_required_and_returns_text_usage(tmp_path):
    lines = []
    lines += _event({"type": "response.output_text.delta", "delta": "Hello"})
    lines += _event({"type": "response.output_text.delta", "delta": " world"})
    lines += _event(
        {
            "type": "response.completed",
            "response": {"id": "resp_1", "status": "completed", "usage": {"input_tokens": 3}},
        }
    )
    response = _FakeResponse(lines, headers={"x-request-id": "request_1"})

    result = _client(tmp_path)._consume_sse(response, "available-model")

    assert result.text == "Hello world"
    assert result.response_id == "resp_1"
    assert result.usage == {"input_tokens": 3}


def test_done_without_completed_is_an_interrupted_stream(tmp_path):
    response = _FakeResponse(["data: [DONE]", ""])
    with pytest.raises(ModelResponseError) as caught:
        _client(tmp_path)._consume_sse(response, "available-model")
    assert caught.value.code == "stream_interrupted"
    assert caught.value.retryable is True


def test_response_failed_classifies_chatgpt_plan_limit(tmp_path):
    failed = {
        "type": "response.failed",
        "response": {
            "error": {
                "code": "subscription_sharing_usage_limit_exceeded",
                "message": "Usage limit reached",
            }
        },
    }
    with pytest.raises(QuotaError) as caught:
        _client(tmp_path)._consume_sse(_FakeResponse(_event(failed)), "available-model")
    assert caught.value.code == "subscription_sharing_usage_limit_exceeded"
    assert caught.value.retryable is False


def test_response_incomplete_never_counts_as_success(tmp_path):
    incomplete = {
        "type": "response.incomplete",
        "response": {"incomplete_details": {"reason": "max_output_tokens"}},
    }
    with pytest.raises(ModelResponseError) as caught:
        _client(tmp_path)._consume_sse(_FakeResponse(_event(incomplete)), "available-model")
    assert caught.value.code == "response_incomplete"


def test_top_level_sse_error_preserves_model_error_code(tmp_path):
    error = {
        "type": "error",
        "code": "subscription_sharing_unsupported_capability",
        "message": "This parameter is unsupported",
        "param": "tools",
    }
    with pytest.raises(ModelResponseError) as caught:
        _client(tmp_path)._consume_sse(_FakeResponse(_event(error)), "available-model")
    assert caught.value.code == "subscription_sharing_unsupported_capability"
    assert caught.value.retryable is False


def test_route_not_supported_is_not_misclassified_as_login_failure(tmp_path):
    response = _FakeResponse(
        status=403,
        payload={
            "error": {
                "code": "subscription_sharing_route_not_supported",
                "message": "Use POST /v1/responses",
            }
        },
    )
    with pytest.raises(ModelResponseError) as caught:
        _client(tmp_path)._raise_http_error(response)
    assert caught.value.code == "subscription_sharing_route_not_supported"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {
                "models": [
                    {"slug": "visible", "display_name": "Visible", "visibility": "list"},
                    {"slug": "hidden", "display_name": "Hidden", "visibility": "hidden"},
                ]
            },
            [{"id": "visible", "display_name": "Visible"}],
        ),
        (
            {"object": "list", "data": [{"id": "api-model", "object": "model"}]},
            [{"id": "api-model", "display_name": "api-model"}],
        ),
    ],
)
def test_model_list_accepts_chatgpt_and_api_formats(tmp_path, payload, expected):
    response = _FakeResponse(payload=payload)
    session = _FakeSession([response])
    client = _client(tmp_path, auth=_FakeAuth(), session=session)

    assert client.list_models() == expected
    assert response.closed is True


def test_chatgpt_failure_does_not_fall_back_to_saved_api_key(tmp_path):
    secrets = SecretStore(tmp_path)
    secrets.set("openai_api_key", "paid-key-that-must-not-be-used")
    auth = _FakeAuth(error=AuthenticationError("reauthorization_required", "sign in"))
    client = OpenAIClient(
        AppSettings(auth_mode="chatgpt", selected_model="model"),
        secrets,
        auth=auth,
        session=_FakeSession([]),
    )

    with pytest.raises(AuthenticationError) as caught:
        client.minimal_request()

    assert caught.value.code == "reauthorization_required"
    assert auth.calls == 1


def test_api_key_mode_is_explicit_and_does_not_call_chatgpt_auth(tmp_path):
    secrets = SecretStore(tmp_path)
    secrets.set("openai_api_key", "explicit-api-key")
    auth = _FakeAuth(error=AssertionError("ChatGPT auth must not be called"))
    completed = {
        "type": "response.completed",
        "response": {"id": "resp_api", "output_text": "ok", "usage": {}},
    }
    session = _FakeSession([_FakeResponse(_event(completed))])
    client = OpenAIClient(
        AppSettings(auth_mode="api_key", selected_model="api-model"),
        secrets,
        auth=auth,
        session=session,
    )

    result = client.minimal_request()

    assert result.text == "ok"
    assert auth.calls == 0
    request = session.requests[0][2]
    assert request["headers"]["Authorization"] == "Bearer explicit-api-key"
    assert request["json"]["store"] is False
    assert request["json"]["stream"] is True
