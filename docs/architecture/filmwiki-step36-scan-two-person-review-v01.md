# FilmWiki Step 36 v0.1: scan quarantine and independent reviewer approval

## Scope and UI policy

Owner-controlled localhost synthetic pilot for the existing demo-editor project. The original PukiWiki visual UI, themes, navigation and CSS are NOT modified, as requested. The existing native PukiWiki attachment routes remain quarantined. Company and genuine sensitive files must not be onboarded.

## Genuine file scan before new release

The owner-local scanning command now runs two checks against the actual opaque file held under WSL private/assets-step34:

1. System libmagic via the trusted file(1) executable checks that the detected MIME exactly matches the declared allowlisted type. An unknown type or failed tool invocation is not accepted.
2. ClamAV clamscan runs with the installed signature database, bounded scanning/unpacking options, and a 60-second subprocess timeout. Return code 0 is clean, 1 is infected, and any other return code or unavailable/stale scanner is treated as error. No caller can supply a pretend "clean" result to the production CLI.

The result is written on the specific immutable asset revision as scan_state, scan_digest, scan_time, scan_engine, and a non-sensitive reason code; a SHA256-linked audit event records the attempt. An approval must match a CLEAN result on precisely the current version SHA256 and be no older than 24 hours. If scanning fails, approval fails; a reviewer cannot override this with a CLI flag.

This is signature-based malware detection, not proof that a document is harmless. It does not include full PDF/image/video parsing, content disarm/reconstruction, macro sandboxing, safe transcoding, remote TLS or antivirus database health notifications.

## Distinct user identities for submission and review

Even an Owner who submitted the pending file may not approve it. The reviewer must have an active Owner / Reviewer / Publisher membership AND must have a different user ID from the uploader. Role membership and file SHA256 are checked again inside the same SQLite write transaction that approves the revision. Review/reject retains the Step35 immutable version/event history.

This enforces independent user accounts but DOES NOT verify that they correspond to two physically different humans. Production organizational enrollment is future work.

## Existing data and fail-closed compatibility

Only pre-existing Step34/35 synthetic approved objects at the time of the one-time migration are explicitly labeled legacy_unverified and remain readable for compatibility. They are NOT relabeled "clean". Newly staged objects default to unscanned; they cannot become approved without actual clean scan and an independent reviewer. Legacy direct-approved imports are prohibited once the security migration marker exists. The old v6 list/download endpoint checks the scan gate for each version.

For real protected projects, legacy-unverified files must be re-scanned/quarantined rather than granted a production exception. All objects here are non-sensitive synthetic test fixtures.

## Local operator workflow

- Stage via assets_lifecycle_admin_v8.py stage with --actor (uploader) and a trusted local synthetic source file.
- Scan via assets_lifecycle_admin_v8.py scan --actor (authorized reviewer) --asset (UUID); the command actually runs ClamAV and file type detection.
- A DIFFERENT authorized identity then approves via assets_lifecycle_admin_v8.py approve --actor (reviewer) --asset (UUID).
- Reject, stage-revision, soft-delete, restore, link, unlink, history and audit work as before. Every new version must be scanned again before approval. Never use the obsolete assets_admin_v7.py direct-approved import, which is disabled.
- Old PukiWiki "attach"/"ref" remain off for private project. No public web upload or remote access.

## Verification and security limitations

Ten new unit tests exercise missing scan, submitted-self approval, infected/error/type mismatch, tampered bytes, revoked reviewer, independent revision scanning, rejected old import path and additive schema idempotence. Tests use a mocked scanner only inside unittest and cannot affect the production CLI. Separately verify a live clean sample with installed real ClamAV. Verify missing-signature/engine-error behavior and actual two-identity approval over the live 8098 API before merging.

The underlying audit events are SHA256-chained inside the local SQLite database, not signed externally. The signature database is managed by clamav-freshclam; its freshness needs continued monitoring. ClamAV may have false positives and false negatives, and an "approved" state is not a safety guarantee.

## Archiving and rollback

NAS-first operation logs are under X:/AI/OperationArchive/FilmWiki/20261014-225924-step36-scanner-dual-review; prior source copies are in before/ and the identity/signing key are NOT copied. The pre-migration consistent catalog snapshot is only in WSL private/assets-step34/catalog.pre-step36.sqlite, mode 0600.

Do not rollback to an old asset catalog or old review function in isolation, as that would allow new pending/unscanned revisions to leak. Preserve the scanner migration columns and all audit history. If the scanner or signature DB is unavailable, keep new uploads pending rather than reverting the approval gate. Original PukiWiki UI revisions remain at the end of the roadmap.
