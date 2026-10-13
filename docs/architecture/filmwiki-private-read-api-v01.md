# FilmWiki Step 24 · Private Read-only Data API v0.1

The Tabler workspace can now read data from the owner's local NAS-backed SQLite staging database. This is a **test-only read API**, not a production permission, authentication, editing or collaboration platform.

## Security boundary

- Database stored outside the PukiWiki webroot, e.g. X:/AI/FilmWiki/staging/project.sqlite (WSL: /mnt/nas/AI/FilmWiki/staging/project.sqlite).
- Python stdlib-only API binds **127.0.0.1:8091** (not 0.0.0.0 and not a LAN/NAS service).
- Startup requires a specific SQLite file inside --data-root, and a preselected source SHA-256. The HTTP client cannot supply filesystem paths or SQL.
- SQLite opened in mode=ro plus PRAGMA query_only=ON; the API contains no write handlers.
- Long random token supplied in Authorization: Bearer, loaded from a file outside the static webroot. Generate once with server.py --token-file ABSOLUTE_PATH --init-token. **Never store the token in GitHub, OperationArchive, app.js, a URL or browser storage.**
- Browser CORS allows only http://127.0.0.1:8080 or http://localhost:8080. Requests from other origins, missing/wrong tokens, and mutation verbs are rejected.
- CSS/JavaScript remain local-only. Content Security Policy allows only http://127.0.0.1:8091 for network requests, never an external SaaS.
- This is still a **single local token**, not user-level ACL. Do not expose to LAN or Internet; do not connect corporate data without explicit permission.

## API contract

- GET /v1/health returns status only (unauthenticated); no database contents.
- GET /v1/manifest requires token; returns source fingerprint and worksheet metadata (name, stable sheet index, field names, total records).
- GET /v1/records?sheet=1&offset=0&limit=100 requires token; returns original rows, cells (including null), source row, shot_number, parent-link status/candidates, asset filename references, total and next offset.
- Max 200 rows per request, max offset 200000. No query-supplied filenames, DB paths, SQL, writes or file downloading.

## Running locally

1. Authorize and stage a SQLite snapshot in a private NAS directory. For early tests create a **synthetic** demo dataset instead of copying a real workbook into the webroot.
2. Create a token: python3 tools/filmwiki_api/server.py --init-token --token-file /PRIVATE/ONLY/api-token.txt
3. Launch: python3 tools/filmwiki_api/server.py --data-root /mnt/nas/AI/FilmWiki --db /mnt/nas/AI/FilmWiki/staging/synthetic-step24.sqlite --source-sha SOURCE_SHA --token-file /PRIVATE/ONLY/api-token.txt --port 8091
4. In FilmWiki's local page at http://127.0.0.1:8080/filmwiki-preview/, choose **连接本地数据库** and paste the token from the private file. It is not saved by the browser.
5. Or continue to use **导入 JSON** exactly as in Step 23.

The browser currently loads all pages (200 records per API call) into memory up to **5,000 rows** and only then swaps the view. Larger datasets and streaming/server-side Grid paging are future work. This deliberate limit prevents silent truncation, not a storage limit in SQLite.

## Verification and next gates

- Python tests build synthetic 17-column SQLite data, verify CORS/preflight/token rejection, pagination, duplicate shot numbers and ambiguous parent links, empty schema tables, and rejected POST/PUT/PATCH/DELETE.
- Node tests mock the API and verify client paging, fail-closed behavior and 5,000-row guard.
- Before production: authentication/ACL per user and project, TLS/secure gateway for remote access, CSRF/rate limits/monitoring, asset SHA-256 registry, server-side paging and actual data transfer authorization.
- **Avoid serving any SQLite file or token via Nginx/PukiWiki**, even as a test.
