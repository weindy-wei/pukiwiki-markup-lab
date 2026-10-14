# FilmWiki Step 32 v0.1: Shared identity and project ACL for native PukiWiki pages

## Verified trust boundary
Step31's private WSL-ext4 `private/collaboration-accounts.sqlite` remains the **single source of truth** for username, salted PBKDF2-HMAC-SHA256 password hashes, active status and project role memberships. The existing FilmWiki 8098 collaboration editor authenticates against that store; native PukiWiki PHP sees the same file only through a **read-only** bind mount at `/run/filmwiki/identity.sqlite`. No PHP password database or new username list was created.

Actual native protected page prefix: `FilmWiki/Projects/demo-editor` and its descendants. Only this **synthetic** project is supported in v0.1. Every protected native PukiWiki read/source/edit check resolves current session identity and role memberships from SQLite, not from a supplied HTTP identity header or stale session-saved role list.

Role map: `viewer`, `reviewer`, `publisher`, `editor`, `owner` may READ native Wiki pages in the authorized project; only `editor` and `owner` may EDIT those pages. This aligns the page-level rule with the Step31 structured-data collaboration editor. Users with no membership are denied, even if they possess valid login credentials. All old Wiki pages outside that prefix retain their pre-existing public behavior.

Native Wiki read_auth and edit_auth remain `0`, intentionally. Targeted gates are added to `lib/auth.php` for `read_auth`, `edit_auth`, `is_page_readable`, `is_page_writable` and `_is_page_accessible`. PukiWiki search_auth is switched to `1` so search respects read restrictions. Core `lib/html.php` adds a CSRF input to native edit forms **only** for the managed namespace. Existing other pages are not forced to log in.

## Login and session behavior
Use `http://127.0.0.1:8080/index.php?plugin=fwunified` to log in to native Wiki pages using the **same username and password** as `http://127.0.0.1:8080/filmwiki-preview/collab.html`. Native PHP uses SQLite3 read-only plus PBKDF2 with 600000 iterations, exactly matching the Step31 Python identity verifier. PHP native sessions use HttpOnly and SameSite=Strict cookies, rotated on login, with exact local Host/Origin checks and session CSRF tokens for login/logout.

Native PHP sessions do NOT share a browser authentication cookie with FilmWiki 8098's bearer-session UI. **This is shared identity and ACL, not automatic cross-app single sign-on.** A user signs in once per interface. A future SSO provider can replace the two session transport mechanisms without introducing a second user database.

On every protected Wiki page access, bridge code re-reads `active`, password hash fingerprint and ordered membership roles from the SQLite store and checks them against the PHP session guard. Disabling an account, changing its password, revoking or changing a project role invalidates the native PHP session on its next access; revoking and regranting does not revive that old session. The Step31 API already enforces own session membership/expiry.

Native editor POST requires exact `Origin` (http://127.0.0.1:8080 or localhost:8080) and a randomly generated hidden session CSRF token; unauthenticated/forged POST is denied. PukiWiki's own edit form, version collision detection, backups and author metadata are kept. File attachments on managed pages should remain disabled/reviewed before allowing real data, since this initial pilot does not exhaustively audit every third-party PukiWiki plugin.

## Deployment on WEINDY-PC (owner-only)
- Private identity SQLite source: `/home/administrator/filmwiki-data/private/collaboration-accounts.sqlite` (0600, uid1000).
- Compose override: `D:/Administrator/Documents/DevEnvironments/PukiWiki/php81/compose.filmwiki-identity.yml`, mounted read-only into PHP only. Nginx has no access to the identity file and remains bound to 127.0.0.1:8080.
- Native bridge: `<site>/lib/filmwiki_unified_bridge.php`, scoped hooks in `<site>/lib/auth.php` and `<site>/lib/html.php`, plugin `<site>/plugin/fwunified.inc.php`, search permission filtering enabled in `pukiwiki.ini.php`.
- Shared synthetic native page: `FilmWiki/Projects/demo-editor/AccessControlTest`. Do NOT use this pilot namespace for employer or personal secrets.
- Edit and read APIs 8094/8091 and collaboration editor 8098 are unchanged. Existing published release still uses its immutable read-only snapshot.
- Root-run Docker Compose always needs both files: `docker compose -f compose.yaml -f compose.filmwiki-identity.yml up -d --no-build`. Running just `compose.yaml` again could recreate PHP without the read-only shared identity mount; in that case protected pages deny access, rather than silently allowing edits.

## Acceptance
- Actual PHP container SQLite3 read-only mount checked (four synthetic identities), PHP lint on all modified files, native Wiki login HTTP 200, existing public homepage HTTP 200.
- Unauthenticated protected page read/source/edit does not disclose marker content. Owner and editor can read and access native edit form; viewer can read/source but not edit; no-project outsider cannot read or edit.
- Native owner/editor POST without the proper CSRF fails 403. A real native PukiWiki editor write succeeds with correct CSRF and authorized user, then a second PukiWiki revision restores the test text. PukiWiki adds its normal `#author` metadata and title anchors, so the final source is semantically restored but not bit-for-bit identical to the original hand-written fixture.
- Revoking a shared project role immediately denies the previous native Wiki session. Regranting does not reactivate the stale session; a fresh login restores access. A password-hash rotation likewise invalidates the previous PHP session. PHP session cookie has HttpOnly and SameSite=Strict.
- Installer/static security regression tests plus the previous Step31 identity tests, and GitHub CI.

## Rollback
Use `tools/wiki_auth/rollback_step32.py` with `--site`, `--archive` and `--apply` after confirming its printed plan. Keep native Wiki page and backup history; restore pre-Step32 `auth.php`, `html.php`, `pukiwiki.ini.php`. If desired, restart Docker Compose **without** the override to detach the readonly identity DB. Keep WSL identity DB, FilmWiki 8098, and all audit history intact.

## Limitations to resolve before staff / LAN
- Shared identity and project grants are implemented; seamless SSO cookies/tokens are not.
- PHP login has per-session login throttling, not yet a network-wide centralized failure counter; do not expose port 8080/8098 to untrusted computers.
- Additional PukiWiki plugins, attachments, backups and third-party extensions require a separate negative-access audit before storing real private project content.
- No TLS, external network access, multi-project dynamic namespace mapping or real company/project records are enabled.
