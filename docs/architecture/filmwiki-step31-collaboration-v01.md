# FilmWiki Step 31 · Authenticated project collaboration pilot

## Principle
Wiki collaboration means persistent identity, scoped membership, concurrency control, credited authorship and reversible revision history, not merely permitting anonymous writes. The owner-controlled single-PC architecture remains authoritative. No data is uploaded to third-party accounts.

## Existing PukiWiki status
Site uses PukiWiki 1.5.4 `pukiwiki.ini.php` with native `$auth_users`, `read_auth`, `edit_auth` support, but read_auth=0 and edit_auth=0 at inspection. Step31 does not toggle those global site options and does not read or reuse PHP passwords. Unified sign-on must be separately designed.

## Isolated identity and editing
- Existing FilmWiki 8091 read API stays bearer-protected; 8094 local synthetic editor stays tokenless on 127.0.0.1. Both are unchanged and independent.
- New Step31 collaboration API on 127.0.0.1:8098 requires per-user username/password login and expiring 8-hour opaque session Bearer token on every private read/write. Session token is held only in browser tab memory, never localStorage/cookies or URLs.
- Local-private `private/collaboration-accounts.sqlite` stores PBKDF2-HMAC-SHA256 password hashes (600k iterations, unique random salts), hashed session tokens, per-project role grants, active and lockout state. Failed password attempts lock account for 15 minutes after 5 attempts; only admin CLI may provision accounts/grants or disable users.
- Separate `editing/step31-collaboration.sqlite` is made by SQLite consistent backup of the synthetic editor working copy. This is NOT the same DB behind 8094. All real/user/company databases remain excluded.
- Authentication storage and edited SQLite must reside in WSL ext4 with owner permissions 0600; do not run a live SQLite on SMB/NAS.

## Project permissions
- Project roles: viewer, editor, reviewer, publisher, owner. In the Step31 HTTP pilot only read and editor actions exist. Viewer can view project data/history but cannot edit. Editor and owner can edit only their project and allowed fields. Reviewer/publisher imply read access but **no HTTP approval/publish capability** yet.
- Every write passes auth-store membership authorization and an independent project grant recheck inside the editor DB transaction, with server-derived actor ID (`user:<UUID>`). Users cannot supply or impersonate `actor` in patch bodies.
- Simultaneous edits use prior optimistic expected_version checks, return 409 on stale changes, and preserve the winner's revision. A successful edit saves actor ID, old/new value, reason, UTC timestamp, immutable hash-chain event and live search index in one transaction.
- A disabled account has all sessions revoked and both sets of edit grants removed. Historic revision actor ID remains stable; role revocation never deletes audit history.

## Browser and safety boundary
- `/filmwiki-preview/collab.html` logs in with user credentials, shows only authorized project, sheets, record list, edit controls and attributed revision history, and supports explicit logout. DOM displays external text via textContent, not HTML; CSP restricts scripts/styles and localhost API.
- HTTP login is intentionally available **only on 127.0.0.1** for pilot. Same-origin checks, strict Host validation, JSON POST, session auth and no CORS credentials are enforced. Running it over a LAN without HTTPS and a real reverse proxy would expose passwords and bearer tokens: explicitly prohibited.
- Passwords, raw session secrets and real user data must never appear in NAS operation logs, GitHub code or status output. Synthetic demo login credentials are owner-local in a 0600 file, not in source control.
- This is a user/account prototype for the FilmWiki structured record database, not yet automatic edit/read integration with every native PukiWiki page.

## Offline administration
`python3 /home/administrator/filmwiki-data/services/collab_admin.py init` creates a fresh identity DB.
`... add-user <username>` prompts twice for a password without putting it in command arguments.
`... grant <username> <viewer|editor|reviewer|publisher|owner>` grants explicit demo project membership and mirrors editor writes to the DB role gate.
`... disable <username>` revokes sessions and both project grants.
`... members` lists current demo project members (not passwords).

## Limits and upcoming integration
- Multi-user identity is genuine on the loopback pilot, but LAN/remote usage awaits TLS/mTLS or encrypted access, native Wiki identity integration, administrator account recovery and stronger multi-user review approval.
- Current HTTP collaboration is restricted to one synthetic project, separate from the Step30 published read-only snapshot.
- Local 8094 no-token editor is retained only for owner-trusted development; it must not share storage with authenticated collaborators and cannot be a backdoor into per-project ACL.

## Rollback
`sudo systemctl disable --now filmwiki-collaboration.service` independently stops only 8098. Preserve identity DB, collaborative edit SQLite, sessions/audits and NAS consistent backups. Original 8080 wiki, 8091 read-only, 8094 local editor, and Step27 index timer stay online.
