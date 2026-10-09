zqr-menu-root = Zotero AI Quick Read
zqr-menu-generate = Generate AI Quick Read
zqr-menu-regenerate = Regenerate
zqr-menu-status = View Status
zqr-menu-settings = Settings
zqr-menu-auto =
    Automatic processing: { $enabled ->
        [true] On
       *[false] Off
    }

zqr-dialog-title = Zotero AI Quick Read
zqr-settings-url = Local backend URL (HTTP loopback only):
zqr-settings-token = Backend Bearer token. Leave blank to keep the current value, or select the checkbox to clear the saved token.
zqr-settings-clear-token = Clear preference token (the token file remains available)
zqr-settings-saved = Settings saved.
zqr-settings-connected = Backend connection and authentication succeeded.
zqr-settings-health-only = The backend is reachable, but authentication failed: { $error }
zqr-settings-invalid-url = Invalid URL. Use only http://127.0.0.1:port, without a path, credentials, query, or fragment.
zqr-settings-invalid-token = The token cannot be whitespace, contain a line break, or exceed 4096 characters.

zqr-notify-submitted = Submitted { $count } quick-read task(s).
zqr-notify-no-ready-pdf = No downloaded, readable PDF with a regular parent item was found in the selection.
zqr-notify-token-missing = No backend token was found. Enter one in Settings or save it to %LOCALAPPDATA%\ZoteroQuickRead\plugin-token.
zqr-notify-backend-error = Local backend request failed: { $error }
zqr-notify-applied = AI Quick Read was saved to “{ $title }”.
zqr-notify-manual-edit = The existing AI note was edited manually. It was preserved and a new quick-read note was created.
zqr-notify-auto-on = Automatic processing is on. Only ready PDFs added or changed from now on are observed; existing items are not scanned.
zqr-notify-auto-off = Automatic processing is off.

zqr-status-title = Zotero AI Quick Read Status
zqr-status-empty = The selected paper has no quick-read task yet.
zqr-status-line = { $title }: { $status }
zqr-status-detail = { $message }
zqr-status-waiting-fulltext = Waiting for the PDF download
zqr-status-queued = Queued
zqr-status-processing = Generating
zqr-status-completed = Completed; waiting to be saved
zqr-status-applied = Completed and saved to Zotero
zqr-status-failed = Failed
zqr-status-waiting-quota = Waiting for quota
zqr-status-reauthorization-required = Backend reauthorization required
zqr-status-cancelled = Cancelled
zqr-status-unknown = Unknown status
zqr-status-backend-unavailable = Backend status could not be refreshed; showing the local cache: { $error }
