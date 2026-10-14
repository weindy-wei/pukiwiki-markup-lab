# FilmWiki Step28 v0.1: Versioned Editing and Audit Trail

## Isolation and data ownership
- Original PukiWiki files and immutable imported SQLite snapshots remain unchanged under WSL ext4 staging/ and the original read-only API on 127.0.0.1:8091 remains unchanged.
- A new synthetic-only editor project demo-editor uses a **separate working-copy SQLite** in editing/synthetic-step28-editor.sqlite and a distinct editor-token.txt mode 0600.
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
- Authenticated GET /v4/manifest, /v4/records?sheet=1&after=1&limit=25&q=..., /v4/record?sheet=1&id=src_..., /v4/history?sheet=1&id=src_...
- Authenticated POST /v4/edit only on the private working copy, with exact local Origin and Content-Type application/json; payload limited to 16 KiB.
- Separate editor bearer token is memory-only in browser and never saved to localStorage, URL, source repo, logs or NAS archive. CSP and textContent protect static editor UI. The original API token on 8091 remains read-only.

## Deployment and rollback
- Static page: http://127.0.0.1:8080/filmwiki-preview/editor.html
- Editor API: http://127.0.0.1:8094, systemd unit filmwiki-editor.service, owned by administrator, writable only to private editing directory.
- Disable editor independently: sudo systemctl disable --now filmwiki-editor.service. Other FilmWiki pages and index timer are unaffected.
- The source snapshot and working copy must be backed up separately using SQLite consistent backup; do not delete an editor working copy as a rollback tactic.
- Step28 is single-operator localhost synthetic validation, not multi-user production CMS. Future work must define roles, signatures across separately stored audit logs, snapshot promotion into canonical imports, attachment/version relationships, and audit-retention policy.
