# FilmWiki Step 26: SQLite FTS5 trigram and typed-field indexes (v0.1)

## Source of truth

Original FilmWiki records remain untouched on owner-local WSL ext4 under /home/administrator/filmwiki-data/staging. A separate **disposable, reproducible** FTS5/typed-field sidecar for each project lives under /home/administrator/filmwiki-data/indexes, outside Nginx/PukiWiki public webroot. The API opens source and index read-only. Do NOT use NAS SMB/CIFS shares as live SQLite databases; NAS remains for consistent backup snapshots and media.

A sidecar contains copies of searchable values and is private project data. Never publish or upload it to GitHub.

## Indexes

- cell_docs: one row per original cell containing canonical JSON value and source/provenance.
- cell_fts: FTS5 trigram over JSON-serialized cell values (including original nulls, numbers and strings).
- exact_fields: index by project worksheet, column index, original JSON type and JSON string; separate numeric index over real JSON numbers.
- No source data migration or changes. Never auto-parse textual duration ranges such as 12–18 秒 into numeric seconds.

## Index creation / rebuilding

Run search_index.py with --data-root, --db, --source-sha, --project-id and --index, all referencing private WSL local files. The builder uses a local scratch database, verifies integrity and cell counts, then installs the new sidecar atomically. Existing indexes require explicit --replace. Source SHA, preview SHA, row/cell counts, source size and mtime provide staleness verification. A changed source causes the API to refuse indexed pages until rebuilt.

## API and UI

server_v3.py retains the private loopback 127.0.0.1:8091 token and v1/v2 routes. On /v2/projects/ID/records the optional filter arguments are:

- kind=string (default), op=eq (default), field=1-based index, value=literal: exact, case-sensitive string match.
- kind=number, op=eq|lt|le|gt|ge, field=index, value=decimal: compare *actual JSON number cells* without coercing stored strings, dates, ranges or labels.
- q=literal substring: 3+ Unicode codepoints use FTS5 trigram candidates, then case-sensitive instr post-filter to retain the v2 search semantics.
- 1–2-character Chinese or other short terms fall back to original scans because trigrams cannot index them.
- High-frequency matches (>=257 indexed cells in a cheap capped probe) intentionally fall back to keyset row scan. This prevents pathological FTS materialization of thousands of hits.
- Optional response field search_backend reports the selected search path. Existing response version stays v2 for compatibility.

The original Tabler Grid/Thumbnail/Detail and JSON import remain supported; new live UI numeric controls are additive. Bearer secrets must never enter URLs, GitHub, operation logs or browser storage.

## Evidence and limitations

- On local SQLite 3.46.1 FTS5 trigram: 20 synthetic records / 199 indexed cells; 5,207 synthetic records / 20,828 indexed cells.
- Original canonical SQLite SHA256 hashes before/after indexing matched.
- Validated Chinese substring queries, punctuation, literal matching, signed cursors, duplicate shot labels, empty sheets and typed numbers against legacy SQL.
- Small synthetic, 10-iteration local benchmark: rare Chinese keyword about 4.18ms old vs 1.24ms indexed; frequent term 0.64ms old vs 0.85ms fallback. These numbers are NOT million-row production estimates or SLAs.
- Still needed later: robust FTS scaling benchmark with 1M rows, ranking, search term tokenization, project-user ACLs, controlled index rebuild on changes, and concurrency testing.

## Rollback

Restore the backed-up server_v2.py systemd ExecStart; restart the read-only API; retain or rebuild independent sidecar index files. Source SQLite, original Wiki and existing project metadata are not modified by Step26.
