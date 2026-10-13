# FilmWiki Step 25: owner-local project catalog and keyset paging v0.1

## Scope
- Read-only, single-user local development. No database/project creation or edits from HTTP.
- Registry with explicit, manually reviewed project IDs, titles, local SQLite filenames and source SHA-256 fingerprints, stored outside the webroot at /home/administrator/filmwiki-data/private/projects.json. The registry is verified on API startup; HTTP cannot supply file paths or arbitrary SQL.
- Nginx/PukiWiki remains at D:/Administrator/Documents/DevEnvironments/PukiWiki/php81/site. The live SQLite databases reside on WSL ext4 under /home/administrator/filmwiki-data/staging, NOT on the X:/AI SMB share.
- NAS X:/AI/FilmWiki remains for verified inert SQLite backups and media. No employer or private user workbooks are committed to public GitHub.
- API binds strictly 127.0.0.1:8091 and keeps the existing private Bearer token at /home/administrator/filmwiki-data/private/api-token.txt. Existing v1 routes remain available so the Step 24 preview is not broken.

## v2 endpoints
All require Bearer token and exact local Origin:
- GET /v2/projects: allowlisted project names/IDs, no DB paths.
- GET /v2/projects/{id}/manifest: source + original sheet/field metadata and unfiltered record counts.
- GET /v2/projects/{id}/records?sheet=1&limit=50&cursor=...&q=...&field=...&value=...
  - 1–100 full-width records per request, using (source_sha256, sheet_index, source_row) keyset boundary rather than deep OFFSET.
  - Opaque HMAC-authenticated cursor binds project, sheet, search text and exact field filter. No skipping/repeating records across pages for an unchanged dataset.
  - q is a bounded UTF-8 substring search across JSON cell values; field is a field index with exact string value match. All parameters are bound SQL values, never injected into SQL identifiers or paths.
  - Returns has_more and next_cursor, no expensive filtered COUNT(*).
- GET /v2/projects/{id}/record?sheet=1&id=src_...: one complete record, parent relation and unresolved attachment refs.
- Existing GET /v1/* endpoints continue to work for the first registered project.

## Frontend
- http://127.0.0.1:8080/filmwiki-preview/index.html remains the earlier Step 24 workbook preview.
- http://127.0.0.1:8080/filmwiki-preview/live.html opens the new server-paged workspace. User pastes a local token into a memory-only field, chooses a registered project and sheet, then Grid/Thumbnail/Detail. Every page fetches at most 50 rows for Grid and Detail, 24 for Thumbnail. Page navigation uses signed cursors.
- Browser does NOT download a whole 5,000+ row workbook. The local JSON and old API preview remain for compatibility. No image filename is treated as an image identity.
- Search is submitted explicitly, not sent on every keystroke. Project/sheet switch resets filters and cursor history.
- Token must not be saved in localStorage, URL params, logs, GitHub or OperationArchive.

## Boundaries before production
- Keyset paging helps deep navigation on indexed source rows, BUT substring queries still scan cell data. For realistic 1M-row full-text searches, build a local FTS5/typed index and run representative latency and concurrency tests before claiming high-scale readiness.
- This version supports a single trusted local bearer token, not per-user/project role-based ACL, network TLS, revision history, editing or content moderation.
- An immutable source SHA is assumed per project; editing requires a later revision model and cursor invalidation/version stamps.
- Exact field matching currently targets string-valued JSON cells. Numeric/date/enum filters and sorting require a typed indexing layer.
- Registry is read once at service startup; changes require validation and controlled service restart.
- NAS snapshots should continue using the consistent local SQLite backup + full SHA-256 verification tool; never run the live SQLite DB over SMB/CIFS.

## Verification
- Python: two independent SQLite databases, 253+4 synthetic rows, 17-column shot register, duplicate labels, empty schema-only sheet, seven-page cursor traversal without gaps/duplicates, project-bound cursor tamper detection, denies untrusted origins and writes.
- Node: 500,000-row advertised metadata never triggers full download, one page per request; project allowlist and bad cursor rejected.
- Local test: two synthetic projects, including >5,000 rows in second database; Windows HTTP/API and DOM click-through checks. No real data.
