# FilmWiki Step 37 v0.1: queued scan, reviewer inbox and inert text preview

## Scope and PukiWiki UI restriction

This is a localhost-only synthetic demo-editor project. Original PukiWiki skin, theme, navigation, layout, styling and native attachment plugins remain UNCHANGED. A separate experimental reviewer page under filmwiki-preview/review.html is not a native Wiki redesign.

## Durable task queue and scheduler

A new asset_scan_jobs SQLite table stores job UUID, immutable asset/version SHA256, requested-by user ID, lease token, queued/next-run/finished time, scan verdict and attempt count. A partial unique index allows only one queued/running job for the same asset version. BEGIN IMMEDIATE ensures atomic enqueue and atomic claim under concurrency. A 180-second stale lease is reclaimable; completion always verifies the still-current lease token, rejecting late scanner writes.

The synthetic staging CLI automatically enqueues a task on new file and new revision. The WSL systemd filmwiki-asset-scan-queue.timer runs roughly once per minute, using a non-network oneshot service with private filesystem writes, and processes up to two jobs per run. Scanner/engine errors retry with increasing delays, up to three attempts. Infection or MIME mismatch blocks without retry. Neither queued status nor a clean scan is equivalent to review approval.

## Current authenticated localhost API

- POST /v7/scans/queue receives only an asset_id. It never accepts scanner verdicts or arbitrary file paths, checks current active roles and version SHA256, and deduplicates open jobs.
- GET /v7/scans gives reviewers project job status and ordinary uploaders only their own submitted jobs.
- GET /v7/reviews returns a reviewer-only pending-version inbox, current scan state and can_approve flag.
- POST /v7/reviews/decision rechecks Step36 clean/fresh scan, immutable version hash, distinct uploader vs reviewer user ID and active owner/reviewer/publisher membership before the transaction can approve.
- GET /v7/assets/UUID/preview serves a narrow, inert text-only preview to eligible uploader or reviewers. Anonymous and ordinary viewers cannot probe the pending object.

## Safe preview isolation

Only successfully scanned text/plain is eligible. Up to 4096 UTF-8 bytes are converted to escaped plain text; control characters are sanitized. Existing JSON responses include content type application/json, nosniff, Cache-Control no-store, Referrer-Policy no-referrer, CSP default-src none. The standalone reviewer JS only inserts via DOM textContent, never HTML injection. Source PDF, video, SVG, HTML, or other active content is never rendered inline. Unsupported or unscanned files have NO preview. This is safe, limited inert-text preview, NOT a fully sandboxed document or audiovisual transcoder.

## Real acceptance

- 13 new tests: concurrent deduplication and claim, bounded retries, infected blocking, role isolation, preview escaping, reviewer separation, revoked requester, clean-job idempotency, stale lease and unsupported media.
- Production WSL systemd timer was enabled; a real editor02 synthetic file was auto-enqueued and autonomously scanned via ClamAV; repeated enqueue returned already_clean.
- Production authenticated API 8098: viewer cannot preview or review pending, editor cannot approve, reviewer can preview escaped markup as inert text and approve once clean. Post-review v6 raw download was successful only after approval with exact bytes.
- No native PukiWiki interface changes. New independent review.html/review.js under FilmWiki experimental workbench.

## Rollback and explicit limitations

Keep the original editor/import datasets, identity DB and historical asset versions. Stop the timer and worker before code rollback. Never restore a pre-Step36/37 API read path that ignores pending, soft-deleted or unscanned asset states. The queue schema is additive; retain its scan and audit history. A consistent pre-migration copy stays only on owner-local WSL under private/assets-step34/catalog.pre-step37.sqlite (0600), not on NAS. Operation archive: X:/AI/OperationArchive/FilmWiki/20261014-233229-step37-scan-queue-review-preview.

No remote access, no arbitrary browser uploads, no real company content, no safe image/PDF/video inline rendering, no guarantee ClamAV catches all malicious input, no physical two-human identity proof, no complete legacy plugin audit. The unfinished Step35 native plugin audit still matters.
