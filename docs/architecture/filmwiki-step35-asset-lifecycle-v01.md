# FilmWiki Step35 v0.1 Asset Lifecycle & Scoped Plugin ACL

Original PukiWiki visual UI redesign is explicitly deferred until the end of the project. Step35 changes only structured asset storage, local administration, API authorization and targeted legacy plugin back-end access checks. No PHP skin, menu layout, themes or original-page CSS has been changed.

## Lifecycle (synthetic demo-editor project only)

- The original two approved Step34 synthetic assets are preserved, and their v1 hashes and bytes are unchanged. Schema migration adds asset_state, current_version, pending_version and deleted_at, plus asset_versions, asset_references and asset_events tables.
- New file intake is pending until owner/reviewer/publisher manual review. Rejected revisions are retained, with version numbers never recycled. The old v6 list/download path rejects pending, rejected and soft-deleted assets, and only delivers the current approved revision. Last approved revision remains active while a new revision awaits review.
- The version store is made of opaque owner-only local WSL files. Each version has a SHA256 and byte-size check at download or restore. Approved assets support project or uploader-private visibility. Revisions and soft deletion do not erase older bytes.
- Structured-record references are validated against an existing synthetic editor record ID. Link/unlink are audited, not automatically shown on native Wiki pages.
- The asset_events table uses per-object SHA256 hash chains to identify straightforward accidental or malicious log edits. This is not externally signed or WORM immutable logging.
- The former direct-approved legacy admin import command is explicitly disabled. Owner-local assets_lifecycle_admin_v8.py is the only supported operator CLI for synthetic intake, approval, revision, reject, soft deletion, restoration, reference changes, history and audit checks. No HTML upload is allowed.
- Important limits: no antivirus scanning, no active two-person approval requirement, no remote HTTPS, no real/company data, no safe native Wiki upload or preview.

## Security review of legacy PukiWiki plugins

A read-only scan of 90 PHP plugins identified 47 matching page enumeration/raw read/cache/storage access patterns, with 37 items still requiring manual review. A match is not proof of a vulnerability. Actual anonymous HTTP confirmed protected page name disclosure in rss, ls2, map and yetlist. These were scoped with is_page_readable, along with deleted. Anonymous retests now hide the protected synthetic page name and valid project member retests on rss2, ls2 and map still show the permitted page. Step33 native PukiWiki attach/ref protected-project quarantine remains in force.

Other plugin routes, exports, feeds and cached data remain to be audited. Do not consider the complete native Wiki secure for sensitive real company material.

## Verified operations

- 10 new asset lifecycle unit tests passed: intake, pending denial through v6, accept/reject, immutable versions, rejected-version number reservation, deletion/recovery, references, hash-chain detection, staged-source restrictions, and preservation of existing assets.
- 72 tests in the Step28-Step35 regression suites passed.
- Real owner-local synthetic text file: v1 pending (download 404) then approved; v2 rejected; v3 approved; valid storyboard record relation linked/unlinked; soft-deleted, restored, soft-deleted. Final audit contained three versions and eleven SHA256-chained events. Stage inputs removed, object history retained but hidden from user download APIs.
- Native PukiWiki original UI remains unchanged. PHP changes limited to the scoped data-access filters in plugin files.

## Backup, rollback and remaining work

NAS operation archive: X:/AI/OperationArchive/FilmWiki/20261014-222537-step35-asset-lifecycle-permission-audit. Old native plugins are preserved in before/. Owner-local pre-migration asset SQLite snapshot: private/assets-step34/catalog.pre-step35.sqlite, mode 0600 and NOT uploaded to NAS.

Roll back by restoring preserved PHP plugin files and checking PHP lint plus anonymous/authorized tests. Do not revert to an older unfiltered asset_catalog_v7.py: it may read pending/deleted records. Do not overwrite a new SQLite catalog with a pre-migration backup without reconciling newer versions and events. No physical deletion of asset objects is part of rollback.

Next: independent upload/reviewer identities, scanning/quarantine, retention and reference version pinning, secure preview/upload, further scoped legacy plugin review, then original Wiki interface changes near the end.
