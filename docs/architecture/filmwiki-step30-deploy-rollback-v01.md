# FilmWiki Step30 · Reviewed release promotion, cutover and rollback

**Scope: synthetic-only pilot.** This release controller can publish only the fixed alias demo-published from a Step29-approved demo-editor release under the private owner WSL ext4 root. No user/company project is authorized, and no browser/HTTP endpoint can trigger deployment.

## Deployment invariants
- Original imported SQLite snapshots and Step29 editor working copies are never modified or deleted.
- The release manifest, approval, publication and snapshot are verified as a hash-linked reviewed artifact. The source snapshot is copied into deployments/versions/UUID.sqlite and chmod 0400. The exact source SHA and FTS5 sidecar are validated before any production restart.
- The prepared complete registry has three projects: unchanged demo-shortfilm and demo-longshots, plus demo-published. Its title includes the first eight characters of the release UUID to make the running version visible in the existing project selector.
- The read-only API reads a newly deployed immutable SQLite file, not the live editable DB or NAS. Only after source/index checks pass is it installed.
- Before cutover, the controller stops the hourly index maintenance timer and acquires its exclusive index lock, stops only filmwiki-readapi.service, writes versioned rollback journal copies of the old registry and old alias sidecar, then atomically swaps index and registry and starts the service. The authenticated /v2 project list, manifests, pagination, FTS and audited parent override are verified.
- If a post-cutover health check fails, the controller stops the read API, restores previous registry and index, restarts the read API, checks prior project list, and marks the transaction rolled back. An interrupted transaction left in prepared state can be manually recovered with its UUID. All journal files remain private.
- The Step27 hourly index reconcile timer is restarted, and both editor 8094 and original read-only projects retain their existing interfaces. Main 8091 retains its bearer token; editor 8094 retains trusted localhost no-token behavior.

## Owner-local WSL commands
Run unprivileged staging:
    python3 /home/administrator/filmwiki-data/services/deploy_cli_v6.py plan --release-id 690cbef4-2295-4ba1-bc6b-d144cfbf8814

Controlled root cutover (use the authorized WSL root session):
    python3 /home/administrator/filmwiki-data/services/deploy_cli_v6.py activate --release-id 690cbef4-2295-4ba1-bc6b-d144cfbf8814

Rollback by the **transaction UUID printed by activate** (this ID is different from the release UUID):
    python3 /home/administrator/filmwiki-data/services/deploy_cli_v6.py rollback --transaction UUID

If a maintenance operation is interrupted before completion:
    python3 /home/administrator/filmwiki-data/services/deploy_cli_v6.py recover --transaction UUID

These cutovers cause a **short read-only API outage**. They are not zero-downtime blue/green deployment and are not authorized in a shared multi-user production environment. Failure after service stop but before automatic restore may require explicit recover using the journal. Do not delete journal transaction folders.

## Review visibility and limitations
- The browser selector exposes the short release ID in the project title. Full UUID, source checksum, release audit and NAS ZIP remain in the private deployment journal and operation archive.
- Old baseline source data retains ambiguous shot-parent links; only reviewed published working-copy snapshots expose confirmed parent_record_id from workflow_relation_overrides while retaining imported candidate labels.
- A version hash and application-level audit chain are **not** proof of distinct human approvals or externally notarized WORM logs. Trusted local operator is still a single logical identity; granular authenticated employee permissions remain future work.
- No automatic promotion of arbitrary releases and no live SQLite on SMB/NAS.

## Acceptance controls
- Six+ independent synthetic unit tests including forced post-cutover health rejection, compensation rollback, interrupted recovery, release tampering, forbidden alias and repeat activation after rollback.
- An isolated HTTP canary with the entire registry + corresponding FTS5 indexes should pass before real activation. After every activation or rollback inspect project count and verified relation values as well as health.
- NAS-first operation logs under X:/AI/OperationArchive/FilmWiki, with the private registry/index backups and SHA256-verified inert database archives. Never write bearer secrets into logs.
