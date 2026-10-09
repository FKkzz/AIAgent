# Testing and acceptance matrix

## Automated and local checks

| Area | Check | Result |
|---|---|---|
| Secrets | Windows CurrentUser DPAPI round-trip; no plaintext in `secrets.bin` | Passed |
| OAuth | PKCE, state one-time use, nonce/audience/issuer/expiry, refresh rotation | Passed with mocked official endpoints |
| SOCKS | SOCKS5 handshake captures domain ATYP for proxy DNS | Passed against local fake proxy |
| Responses | SSE fragmentation, failed/incomplete/error, early EOF, completed usage | Passed |
| Models | ChatGPT `models[]` and API-key `data[]` formats | Passed |
| Queue | duplicate suppression, force regeneration, cancel, restart recovery | Passed |
| PDF | multi-page extraction, page markers, scan failure, no silent truncation, inline page image | Passed |
| Output | schema, impossible page reference, synonym normalization, safe HTML | Passed |
| Local API | loopback token, browser Origin rejection, submit/list/status | Passed |
| Packaging | JS syntax and XPI root files | Passed |
| Backend binary | EXE init, DPAPI, serve, health, authenticated info | Passed |
| Zotero runtime | isolated-profile XPI discovery on installed Zotero 9.0.6 | Passed (`active=true`, `appDisabled=false`) |

The non-Windows Fernet development fallback test is skipped on Windows by design.

## Requires the user's account/network/data

| Acceptance | Status |
|---|---|
| Real SOCKS OIDC/JWKS/token exchange | Not yet verified |
| Real `chatgpt.tokens.use.direct` grant | Not yet verified |
| Account model catalog | Not yet verified |
| Real minimal `response.completed` and usage | Not yet verified |
| Selected physics paper output quality and citations | Not yet verified |
| Visible Zotero right-click and multi-select behavior | Not yet verified |
| Real child note/tag write-back | Not yet verified |
| Connector/sync-on-demand/linked-file import events | Not yet verified |
| Manual edit protection in the user's library | Not yet verified |

These rows must not be marked passed based on mocks or an isolated empty Zotero profile.
