# FilmWiki Step28 v0.1: Versioned Editing and Audit Trail

## Isolation and data ownership
- Original PukiWiki files and immutable imported SQLite snapshots remain unchanged under WSL ext4 staging/ and the original read-only API on 127.0.0.1:8091 remains unchanged.
- A new synthetic-only editor project demo-editor uses a **separate working-copy SQLite** in editing/synthetic-step28-editor.sqlite. The previously issued 0600 editor token may remain privately stored for rollback; the default local mode no longer reads it.
- A dedicated editor API is bound to 127.0.0.1:8094, no database paths or arbitrary SQL are accepted through HTTP. The editing project is not registered in the original read-only registry.
- Only selected allowlisted ordinary fields may be changed; record identity, shot-number columns, parent-link labels and known asset/image references are excluded.

## Atomic editor transaction
- POST /v4/edit requires project_id, sheet_index, record_id, column_index, JSON scalar value, expected_version, reason (3–200 characters) and UUID4 request_id.
- On each change the editor locks the working-copy SQLite with BEGIN IMMEDIATE, verifies optimistic version and field permission, rejects UUID replay and no-op, then updates cells, FTS5 trigram document index through SQLite triggers, record_versions and a revision_events audit entry. These steps commit as one transaction or roll back together.
- Revision audit stores previous/new JSON values, actor, reason, UTC time, prior event hash and SHA-256 event hash. Direct UPDATE and DELETE on revision_events are blocked by SQL triggers.
- verify_history validates the application-level chained hashes and versions. This is tamper-evident in the local application but not a separate WORM log or external notarization. To revert values, submit a new version, never delete a historic revision.
- The current browser UI accepts textual values. The underlying validated engine supports other scalar JSON values but the initial UI does not silently coerce text to numbers. Attachments, relationships, multiple-record transactions and schema changes are out of scope.

## HTTP interface and security
- GET /v4/health is public and contains no project data.
- In tokenless local mode, GET /v4/manifest, /v4/records?sheet=1&after=1&limit=25&q=..., /v4/record?sheet=1&id=src_..., /v4/history?sheet=1&id=src_... require the expected localhost Host and the trusted FilmWiki browser Origin, not a Bearer header.
- Tokenless POST /v4/edit still requires trusted FilmWiki Origin, exact 127.0.0.1:8094 or localhost:8094 Host, Content-Type application/json, and a body limited to 16 KiB. Other write verbs remain disabled.
- The editor page auto-connects without storing or sending a token. The service explicitly starts with --local-no-token and is hard-bound to 127.0.0.1, while 8091 keeps its separate read-only token. Host/Origin checks reduce cross-site and DNS-rebinding risks, but do **not** authenticate processes already running on the same computer: any local process can connect and spoof HTTP headers. Do not use this mode on a shared/untrusted machine or over a LAN. CSP and textContent protect the static editor UI.

## Deployment and rollback
- Static page: http://127.0.0.1:8080/filmwiki-preview/editor.html
- Editor API: http://127.0.0.1:8094, systemd unit filmwiki-editor.service, owned by administrator, writable only to private editing directory.
- Disable editor independently: sudo systemctl disable --now filmwiki-editor.service. Other FilmWiki pages and index timer are unaffected.
- The source snapshot and working copy must be backed up separately using SQLite consistent backup; do not delete an editor working copy as a rollback tactic.
- Step28 is single-operator localhost synthetic validation, not multi-user production CMS. Future work must define roles, signatures across separately stored audit logs, snapshot promotion into canonical imports, attachment/version relationships, and audit-retention policy.

## Optional return to token-protected mode
Restore the previous systemd unit, which supplies `--token-file /home/administrator/filmwiki-data/private/editor-token.txt`, and restart the editor service. This does not modify or reset the working-copy SQLite or audit history. The local no-token option is explicit and mutually exclusive with `--token-file`.
