# FilmWiki Step 34 v0.1: session lifecycle and object-level assets

## Scope

Local WEINDY-PC synthetic-only pilot. Identity source remains the Step31 SQLite database, with Step32 PHP native Wiki login and Step33 one-way SSO. No remote access, real project or company files. Native PukiWiki attachment plugins stay quarantined for the managed demo-editor namespace.

## Shared revocation model

Add an integer session_epoch to users, and a per-session UUID, sign-in source and issued epoch to API sessions. The migration is additive and backfills old sessions. Every Python API request checks its issued epoch against the active user; the native PHP session guard also incorporates the shared current user epoch. When the epoch changes, every previously authenticated PHP cookie and API bearer fails its next protected request.

The collaboration API offers GET /v6/sessions, POST /v6/sessions/revoke (own session UUID only), and POST /v6/sessions/revoke-all (empty JSON object). The last operation increments the user epoch in one transaction and removes all API sessions. The administrative CLI supports sessions USERNAME and revoke-all-sessions USERNAME. The second command forces every session of that account offline and should be used intentionally; no existing director/editor demo account was forcibly logged out in acceptance testing. This is revocation on next request, not push notification or full multi-application logout federation.

Sessions expose only public UUID, sign-in source and timestamps. Neither bearer secrets nor token hashes are listed. Sign-in sources indicate password vs Wiki SSO, NOT a securely established physical device identity.

## Object ACL

A separate private local ext4 assets-step34 directory contains a mode-0600 SQLite object catalog plus mode-0600 opaque asset-UUID.bin files. Objects are never stored in the Nginx document root or NAS. Only explicitly staged synthetic files can be imported using the local administrator CLI; there are no public uploads.

Visibility project: every active demo-editor project member may download.
Visibility private: only its uploading active project member may download.

The GET /v6/assets route lists visible metadata. GET /v6/assets/UUID/content authenticates again, checks the current project's membership and asset visibility, opens the opaque file with no symlink following, checks its SHA-256 and length, and sends it as a no-store attachment with nosniff. Unauthorized private objects yield 404. Only basic file header/MIME checks are performed; there is no complete file decoding or antivirus scan, so this must remain synthetic-only.

The browser has per-session revoke controls, logout-all, visible asset listing and authenticated downloads. The Step33 SSO and password form are preserved. Native PukiWiki attach/ref remains disabled in the protected namespace.

## Actual validation

- Seven Step34 HTTP and SQLite unit tests covering multiple concurrent sessions, per-device logout, epoch logout-all, private and project assets, tampered bytes, malformed paths, and idempotent migration passed.
- Real native PukiWiki PHP cookie plus two independent 8099 collaboration sessions: logout-all invalidated both API tokens, the old Wiki cookie could not see protected content or request SSO, and fresh Wiki login worked. Temporary synthetic login removed.
- Local admin force-logout command tested against a separate temporary account with two sessions, then cleaned.
- Two synthetic 1-pixel PNG objects tested over authenticated 8099: owner sees both; viewer sees only the project-shared object; private object returned 404; unauthenticated access returned 401.
- Production 8098 updated, 8091 and 8094 unaffected. Browser Wiki SSO and the session/asset panes passed a DOM-based live API test.
- All 62 Step28 through Step34 regression tests passed. Verify final GitHub CI and merged revision independently.

## Operational and rollback rules

Owner-local SQLite backup created before schema migration, within private/collaboration-accounts.pre-step34.sqlite and mode 0600, NOT on NAS. NS archive contains only before-files, hashes, test reports and source changes, no identity DB, session bearer, private signing key or passwords.

The NAS-first operation archive is X:/AI/OperationArchive/FilmWiki/20261014-214555-step34-session-asset-acl. Rollback may restore old PHP bridge, login plugin, collaboration UI and systemd service with old source files in its before/ directory; restart PHP/8098. Keep the additive SQLite columns, existing sessions, version history, and private asset data. Do NOT blindly restore the pre-migration identity database over the live one.

## Explicit limitations

No LAN or Internet exposure; no HTTPS identity service; no push-logout to idle tabs; no trusted device fingerprints; no native PukiWiki attachment reopening; no scanning, retention, transcoding, online upload or asset sharing outside one synthetic project. Third-party Wiki plugin/feeds/export permissions need a separate comprehensive audit before real private data is allowed.
