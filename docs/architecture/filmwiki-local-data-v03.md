# FilmWiki Step 22: self-owned canonical data staging (v0.3)

**Separation of concerns:** XLSX / Feishu .base are input formats. The persistent FilmWiki schema is independent of Excel, PukiWiki markup, Feishu, and Tabler. PukiWiki is a documentation/rendering surface, not the only copy of production data.

## Data ownership

- Storage is a path explicitly selected by the data owner, never a cloud API account.
- Use SQLite for local initial deployment, with a documented export to portable JSON.
- Never put imported workbooks, images, metadata or SQLite databases into this public source repository. Do not place private data in a web-accessible PukiWiki directory.
- Keep the original XLSX and, when available, original attachments alongside protected source archives under owner-controlled storage.
- The existing X:/AI/OperationArchive is only for operational logs, not the primary media/database store. Provision a dedicated private FilmWiki data directory before any production deployment.
- Do not import employer/client material without company authorization.

## Pipeline

1. Create user-approved XLSX import preview, including source SHA-256 and original row/column provenance. The repository has an OOXML preview tool. The uploaded sample was inspected with artifact_tool, preserving all 7 tables in a JSON preview.
2. Run local_store.py INPUT_PREVIEW.json to validate only (no writes).
3. After reviewing, run local_store.py INPUT_PREVIEW.json --apply --db /PRIVATE/PATH/filmwiki.sqlite for an atomic SQLite staging import.
4. Use --project-sheet "第1集" --columns "镜头号,场景,时长（秒）,父记录" to preview a 4-column view while retaining all 17 underlying fields.
5. Export an open JSON snapshot with export_store.py /PRIVATE/PATH/filmwiki.sqlite --source-sha SHA256 --output /PRIVATE/PATH/export.json.

These are CLI tools, not yet integrated into a Wiki editing UI. The SQLite importer does not modify PukiWiki pages.

## Invariants

- imports tracks source content fingerprint; repeated identical imports are idempotent. Same source SHA with a changed JSON preview fails rather than silently overwriting values.
- sheets + fields preserve names, order, and schema-only empty sheets.
- records have independent provenance-stable record_id and source_row. Shot labels such as SC_06 are not unique keys.
- cells contain a JSON representation of every original typed value, including null and fields with multiline text. No fixed 8-column ceiling.
- shot_parent_links resolves by a unique label within the worksheet. If multiple rows share a label, status is ambiguous and candidates are preserved. Missing parents remain missing. No guessed edges.
- asset_references stores unresolved attachment filename references. A repeated basename like image.png is not an asset identity. Attachments are not imported until SHA-256 file identity and source metadata can be reviewed.
- A view limits display to at most eight selected columns for an eventual PukiWiki/Tabler adapter. It never drops columns in the database.
- SQLite PRAGMA integrity_check and offline unit tests protect the initial local store. This is not yet a complete production migration, versioning, authorization or backup system.

## Original Feishu test case

User-provided 短剧项目表设定表.xlsx has 7 sheets and 20 data rows; 第1集 has 17 fields and 10 records with duplicate SC_06, nullable fields and ambiguous parent labels; two sheets are schema-only. The XLSX includes 11 filename references, not embedded image bytes.

This real workbook's content must not be committed to the public repository. Validation evidence and synthetic representative cases only go in source control.

## Next gates

- Verify local staging against the actual workbook preview and perform a SQLite -> portable JSON round trip; retain evidence in NAS OperationArchive.
- Provision non-web-exposed NAS data directory and access control separate from operational logs.
- Add stable record-update identity/version history, attachment hashing and explicit field mapping (including dates/durations) before production write APIs.
- For complex cinematography data, Tabler should read a view/query over canonical records, not drive storage schema.
