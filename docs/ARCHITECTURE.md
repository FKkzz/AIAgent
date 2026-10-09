# Architecture

```text
Zotero 9 plugin
  ├─ item/attachment notifier
  ├─ local Bearer authentication
  ├─ task submit/status polling
  └─ validated note + additive tags
             │ HTTP 127.0.0.1:23120
             ▼
Local backend
  ├─ FastAPI loopback boundary
  ├─ SQLite WAL queue (single worker)
  ├─ PyMuPDF page text/captions/images
  ├─ versioned reading instructions + JSON Schema
  ├─ explicit ChatGPT or API-key auth
  ├─ requests/PySocks external transport
  └─ DPAPI credential store
             │ configured SOCKS or explicit direct
             ▼
OpenAI OIDC/JWKS + public /v1/models and /v1/responses
```

## Completion invariant

A job becomes `completed` only when all conditions hold:

1. The attachment is stable and readable.
2. Every required document chunk has been processed without silent truncation.
3. The final SSE stream contains `response.completed`.
4. Output parses as JSON and passes the fixed Pydantic schema.
5. Referenced PDF page numbers exist.
6. Tags are normalized and deduplicated.
7. Note HTML is rendered from validated fields, never copied from model HTML.

An EOF, cancellation, `response.failed`, `response.incomplete`, validation failure, or unsupported capability leaves no completed result for the plugin to write.

## Deduplication and recovery

The queue key includes library ID, parent key, attachment identity/content information, prompt version, model, auth mode, and abstract/full-text mode. Forced regeneration uses a unique key. Processing rows are returned to `queued` after backend restart.

The plugin maintains local job/application records. It hashes the exact normalized note content after writing. A later regeneration overwrites only if that hash still matches; otherwise it preserves the edited note and creates a new one.
