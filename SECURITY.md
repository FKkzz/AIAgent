# Security

## Trust boundaries

PDF text, captions, images, bibliographic metadata, and model output are untrusted. They cannot change the reading instructions, invoke programs, request credentials, or directly provide HTML written into Zotero.

The backend binds only to `127.0.0.1`, validates the Host header, rejects browser Origin requests, and requires a random Bearer token for all non-health endpoints. Port 23120 is used because Zotero reserves 23119 for Connector communication.

## Secret storage

- OAuth access, refresh, and retained ID tokens: Windows CurrentUser DPAPI.
- Optional OpenAI API key: Windows CurrentUser DPAPI.
- Optional SOCKS password: Windows CurrentUser DPAPI.
- Plugin/backend token: a random 384-bit value mirrored to `%LOCALAPPDATA%\ZoteroQuickRead\plugin-token`; the installer removes inherited ACLs and grants read/write only to the current Windows user.

Secrets are excluded from source control, logs, analytics, Zotero items, and Zotero sync data. Authorization URLs containing an `id_token_hint` are never logged.

## Network policy

Every non-loopback request for discovery, JWKS, OAuth token exchange, refresh, revocation, model listing, and Responses inference uses the configured explicit SOCKS session. Environment and Windows proxy settings are ignored. TLS verification remains enabled.

The implementation uses only documented OpenAI authorization and public API endpoints. It does not read browser cookies, copy another application's credentials, or call ChatGPT private web endpoints.

## Reporting a problem

Do not include tokens, authorization URLs, proxy passwords, full papers, private Zotero databases, or `%LOCALAPPDATA%\ZoteroQuickRead\secrets.bin` in an issue. Include only the stable error code, redacted message, software versions, and whether the failure occurred at proxy, OIDC, authorization, model-list, or inference stage.
