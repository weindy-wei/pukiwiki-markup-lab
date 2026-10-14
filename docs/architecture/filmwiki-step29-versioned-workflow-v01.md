# FilmWiki Step 29 v0.1 · Atomic batches, parent resolution and reviewed releases

## Scope and trust
Current editor API is local and tokenless on a trusted computer. The local-owner identity is a fixed application constant, NOT authenticated human identity. Project-scoped editor/reviewer/publisher grants are groundwork for future real-project permissions, not secure multiuser access.
Existing imported SQLite snapshots, Wiki source, and read-only API 8091 are not overwritten. Editing remains an isolated working copy on 8094; testing may use a disposable separate editor copy on 8096.

## Multi-record transactions
workflow_v5.setup() adds project role grants, batch metadata, and an audited relationship-override table only to a selected editable working copy. Nothing is migrated into the original source databases.
POST /v4/batch receives project_id, random UUID4 batch_id, reason, and 1–20 operations; at most one operation per record. Cell edits require the field allowlist, real project and expected record version. Parent edits must use an explicit candidate record_id on the same sheet, never an ambiguous label such as SC_06.
Every operation in one BEGIN IMMEDIATE transaction: validate project and local role, validate all records/versions/permissions and parent candidates, save batch metadata, update cells/relationships, update FTS5 through existing SQLite triggers, advance versions, and append hash-chain audit events. Any invalid operation causes one complete rollback.
The original imported shot_parent_links table remains intact. Explicitly selected links are stored in a versioned relationship override, with relation revision audit events using column_index=0. Existing /v4/edit remains unchanged.

## Explicit offline release stages
Use release_cli_v5.py from the trusted WSL operator account with --data-root, --db for prepare, --release-id UUID4 and --project-id.
PREPARE: create a new private consistent SQLite backup under releases/prepared/UUID, verify SQLite and foreign-key integrity, audit hash chains, search document equality and relationship integrity, then record snapshot SHA256 and evidence in manifest.json.
APPROVE: reread the sealed working-state snapshot, verify prepared manifest/checksum and the reviewer grant frozen into it, then create approval.json exclusively. Approval fails if the snapshot was modified.
PUBLISH: verify approval hash and publisher grant, move to new private releases/published/UUID, make files read-only, record publication.json. Existing published releases cannot be intentionally overwritten.
Publication is an OFFLINE artifact only. It does NOT deploy to the 8091 read-only API, re-register projects, rewrite Wiki pages or modify imported user files. Future approved promotion is a separate step.

## Known limitations and rollback
Without human authentication, roles are not proof that two distinct people reviewed a release. A local process with user filesystem access can spoof HTTP headers or alter the SQLite files; app-level SHA256 is not an external WORM audit system.
To roll back the feature, stop the isolated test editor or revert the server module/UI; preserve edited SQLite copies and all audit events. Use consistent SQLite snapshots verified by SHA256 for NAS backup; never run live SQLite over SMB.
Real company project data remains excluded until explicit per-user authentication, provenance mapping, fine-grained permissions, and promotion governance are implemented.
