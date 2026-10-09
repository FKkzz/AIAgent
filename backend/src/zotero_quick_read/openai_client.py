from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Literal

import requests

from .auth import ChatGPTAuth
from .config import AppSettings
from .errors import (
    AuthenticationError,
    ModelResponseError,
    ProxyError,
    QuotaError,
)
from .proxy import requests_session
from .secrets import SecretStore

API_BASE_URL = "https://api.openai.com/v1"
API_KEY_SECRET = "openai_api_key"  # noqa: S105
AuthMode = Literal["chatgpt", "api_key"]


@dataclass(frozen=True)
class ResponseResult:
    text: str
    usage: dict[str, Any]
    response_id: str | None
    model: str


def parse_sse_events(lines: Iterable[str | bytes]) -> Iterator[tuple[str | None, str]]:
    """Parse an SSE byte/line iterator, including comments and multiline data."""

    event_name: str | None = None
    data_lines: list[str] = []
    for raw_line in lines:
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8")
        else:
            line = raw_line
        line = line.rstrip("\r\n")
        if not line:
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
    if data_lines:
        yield event_name, "\n".join(data_lines)


def _extract_error(payload: Any) -> tuple[str | None, str, str | None]:
    if not isinstance(payload, dict):
        return None, "OpenAI returned an error.", None
    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code") if isinstance(error.get("code"), str) else None
        message = error.get("message") if isinstance(error.get("message"), str) else None
        param = error.get("param") if isinstance(error.get("param"), str) else None
        return code, (message or code or "OpenAI returned an error.")[:500], param
    if isinstance(error, str):
        description = payload.get("error_description")
        return error, str(description or error)[:500], None
    code = payload.get("code") if isinstance(payload.get("code"), str) else None
    detail = payload.get("detail") or payload.get("message")
    param = payload.get("param") if isinstance(payload.get("param"), str) else None
    return code, str(detail or code or "OpenAI returned an error.")[:500], param


def _raise_classified_error(
    *,
    code: str | None,
    message: str,
    status: int | None = None,
    request_id: str | None = None,
    param: str | None = None,
) -> None:
    machine_code = code or (f"http_{status}" if status is not None else "response_failed")
    details = message
    if param:
        details = f"{details} (parameter: {param})"
    if request_id:
        details = f"{details} (request ID: {request_id})"

    if machine_code == "subscription_sharing_usage_limit_exceeded":
        raise QuotaError(machine_code, details, retryable=False)
    if machine_code == "subscription_sharing_usage_unavailable":
        raise QuotaError(machine_code, details, retryable=True)
    if machine_code in {
        "subscription_sharing_user_not_eligible",
        "subscription_sharing_invalid_user",
        "chatpass_v2_scope_not_authorized",
        "chatpass_v2_invalid_authorization_context",
    }:
        raise AuthenticationError(machine_code, details, retryable=False)
    if machine_code == "subscription_sharing_user_unavailable":
        raise ModelResponseError(machine_code, details, retryable=True)
    if machine_code in {
        "model_not_found",
        "subscription_sharing_unsupported_capability",
        "subscription_sharing_route_not_supported",
    }:
        raise ModelResponseError(machine_code, details, retryable=False)
    if status == 401:
        raise AuthenticationError(machine_code, details, retryable=False)
    if status == 403:
        raise AuthenticationError(machine_code, details, retryable=False)
    if status == 429 or machine_code in {"rate_limit_exceeded", "insufficient_quota"}:
        raise QuotaError(machine_code, details, retryable=machine_code != "insufficient_quota")
    raise ModelResponseError(
        machine_code,
        details,
        retryable=status is None or status >= 500,
    )


class OpenAIClient:
    """Requests-based client with explicit, never-implicit authentication modes."""

    def __init__(
        self,
        config: AppSettings,
        secrets: SecretStore,
        *,
        auth: ChatGPTAuth | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config
        self.secrets = secrets
        self.session = session or requests_session(
            config.proxy,
            secrets,
            timeout=(15.0, float(config.request_timeout_seconds)),
        )
        self.auth = auth or ChatGPTAuth(config, secrets)

    def list_models(self, auth_mode: AuthMode | None = None) -> list[dict[str, str]]:
        mode = self._auth_mode(auth_mode)
        response = self._request(
            "GET",
            f"{API_BASE_URL}/models",
            mode=mode,
            headers={"Accept": "application/json"},
        )
        try:
            if not 200 <= response.status_code < 300:
                self._raise_http_error(response)
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise ModelResponseError(
                    "model_catalog_invalid",
                    "OpenAI returned an invalid model catalog.",
                ) from exc
        finally:
            response.close()
        if not isinstance(payload, dict):
            raise ModelResponseError("model_catalog_invalid", "Model catalog is not an object.")

        result: list[dict[str, str]] = []
        if isinstance(payload.get("models"), list):
            # ChatGPT plan-usage catalog: slug/display_name/visibility.
            for item in payload["models"]:
                if not isinstance(item, dict) or item.get("visibility") != "list":
                    continue
                slug = item.get("slug") or item.get("id")
                if not isinstance(slug, str) or not slug:
                    continue
                display_name = item.get("display_name")
                result.append(
                    {
                        "id": slug,
                        "display_name": display_name if isinstance(display_name, str) else slug,
                    }
                )
            return result
        if isinstance(payload.get("data"), list):
            # Standard API-key model list.
            for item in payload["data"]:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    continue
                model_id = item["id"]
                result.append({"id": model_id, "display_name": model_id})
            return result
        raise ModelResponseError(
            "model_catalog_invalid",
            "OpenAI returned neither a ChatGPT nor API model-list format.",
        )

    def minimal_request(
        self,
        *,
        model: str | None = None,
        auth_mode: AuthMode | None = None,
    ) -> ResponseResult:
        return self.respond(
            instructions=None,
            input_content="Say exactly: Hello, world!",
            model=model,
            auth_mode=auth_mode,
        )

    def respond(
        self,
        instructions: str | None,
        input_content: str | list[Any] | dict[str, Any],
        model: str | None = None,
        auth_mode: AuthMode | None = None,
    ) -> ResponseResult:
        mode = self._auth_mode(auth_mode)
        selected_model = model or self.config.selected_model
        if not selected_model:
            raise ModelResponseError(
                "model_not_selected",
                "Select a model returned by the active account before inference.",
            )
        if isinstance(input_content, str):
            request_input: Any = [{"role": "user", "content": input_content}]
        elif isinstance(input_content, dict):
            request_input = [input_content]
        else:
            request_input = input_content
        body: dict[str, Any] = {
            "model": selected_model,
            "input": request_input,
            "store": False,
            "stream": True,
        }
        if instructions:
            body["instructions"] = instructions

        response = self._request(
            "POST",
            f"{API_BASE_URL}/responses",
            mode=mode,
            headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
            json=body,
            stream=True,
        )
        try:
            if not 200 <= response.status_code < 300:
                self._raise_http_error(response)
            return self._consume_sse(response, selected_model)
        finally:
            response.close()

    def _consume_sse(self, response: requests.Response, model: str) -> ResponseResult:
        text_parts: list[str] = []
        completed_response: dict[str, Any] | None = None
        request_id = response.headers.get("x-request-id")
        try:
            lines = response.iter_lines(decode_unicode=True)
            for event_name, data in parse_sse_events(lines):
                if data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ModelResponseError(
                        "stream_event_invalid",
                        "The Responses stream contained invalid JSON.",
                        retryable=True,
                    ) from exc
                if not isinstance(event, dict):
                    raise ModelResponseError(
                        "stream_event_invalid",
                        "The Responses stream contained a non-object event.",
                        retryable=True,
                    )
                event_type = event.get("type") or event_name
                if event_type == "response.output_text.delta":
                    delta = event.get("delta")
                    if isinstance(delta, str):
                        text_parts.append(delta)
                elif event_type == "response.completed":
                    candidate = event.get("response")
                    if not isinstance(candidate, dict):
                        raise ModelResponseError(
                            "completed_response_invalid",
                            "The completed event did not contain a response object.",
                        )
                    completed_response = candidate
                    break
                elif event_type == "response.failed":
                    failed = event.get("response")
                    error_payload = (
                        failed.get("error") if isinstance(failed, dict) else event.get("error")
                    )
                    code, message, param = _extract_error({"error": error_payload})
                    _raise_classified_error(
                        code=code,
                        message=message,
                        request_id=request_id,
                        param=param,
                    )
                elif event_type == "response.incomplete":
                    incomplete = event.get("response")
                    reason = None
                    if isinstance(incomplete, dict):
                        details = incomplete.get("incomplete_details")
                        if isinstance(details, dict) and isinstance(details.get("reason"), str):
                            reason = details["reason"]
                    raise ModelResponseError(
                        "response_incomplete",
                        f"The model response was incomplete{f': {reason}' if reason else ''}.",
                        retryable=True,
                    )
                elif event_type == "error":
                    code, message, param = _extract_error(event)
                    _raise_classified_error(
                        code=code,
                        message=message,
                        request_id=request_id,
                        param=param,
                    )
        except ProxyError:
            raise
        except UnicodeError as exc:
            raise ModelResponseError(
                "stream_encoding_invalid",
                "The Responses stream was not valid UTF-8.",
                retryable=True,
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise ModelResponseError(
                "stream_timeout",
                "The Responses stream timed out before completion.",
                retryable=True,
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise ModelResponseError(
                "stream_interrupted",
                "The Responses stream was interrupted before completion.",
                retryable=True,
            ) from exc

        if completed_response is None:
            raise ModelResponseError(
                "stream_interrupted",
                "The Responses stream ended without response.completed.",
                retryable=True,
            )
        text = "".join(text_parts) or self._text_from_completed(completed_response)
        usage = completed_response.get("usage")
        response_id = completed_response.get("id")
        return ResponseResult(
            text=text,
            usage=dict(usage) if isinstance(usage, dict) else {},
            response_id=response_id if isinstance(response_id, str) else None,
            model=model,
        )

    def _request(self, method: str, url: str, *, mode: AuthMode, **kwargs) -> requests.Response:
        token = self._bearer_token(mode)
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {token}"
        try:
            return self.session.request(method, url, headers=headers, **kwargs)
        except ProxyError:
            raise
        except requests.exceptions.Timeout as exc:
            raise ModelResponseError(
                "network_timeout",
                "The OpenAI request timed out.",
                retryable=True,
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise ModelResponseError(
                "network_error",
                "The OpenAI request could not be completed.",
                retryable=True,
            ) from exc

    def _bearer_token(self, mode: AuthMode) -> str:
        if mode == "chatgpt":
            return self.auth.get_access_token()
        value = self.secrets.get(API_KEY_SECRET)
        if not isinstance(value, str) or not value.strip():
            raise AuthenticationError(
                "api_key_missing",
                "API-key mode was selected, but no OpenAI API key is configured.",
            )
        return value.strip()

    def _auth_mode(self, requested: AuthMode | None) -> AuthMode:
        mode = requested or self.config.auth_mode
        if mode not in ("chatgpt", "api_key"):
            raise ValueError(f"unsupported authentication mode: {mode}")
        # Selection is final for this request. Failures never trigger another
        # credential source or a paid API fallback.
        return mode

    @staticmethod
    def _raise_http_error(response: requests.Response) -> None:
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = None
        code, message, param = _extract_error(payload)
        _raise_classified_error(
            code=code,
            message=message,
            status=response.status_code,
            request_id=response.headers.get("x-request-id"),
            param=param,
        )

    @staticmethod
    def _text_from_completed(response: dict[str, Any]) -> str:
        direct = response.get("output_text")
        if isinstance(direct, str):
            return direct
        parts: list[str] = []
        output = response.get("output")
        if not isinstance(output, list):
            return ""
        for item in output:
            if not isinstance(item, dict) or not isinstance(item.get("content"), list):
                continue
            for content in item["content"]:
                if not isinstance(content, dict):
                    continue
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    parts.append(content["text"])
        return "".join(parts)
