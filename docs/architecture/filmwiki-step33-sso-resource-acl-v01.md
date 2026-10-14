# FilmWiki Step 33 v0.1 · One-way local SSO and resource access hardening

## Trust and scope
- Step31 private user/membership SQLite is still the only account authority. Step32 native PukiWiki reads it by a read-only container mount; the 8098 collaboration API keeps its own authenticated 8-hour bearer sessions.
- This phase provides **one-way seamless sign-in from a currently authenticated PukiWiki tab to FilmWiki's collaboration workbench**. It is NOT global federated logout, remote/TLS production SSO, or authorization for employer data.
- Entire pilot is bound to 127.0.0.1 and the synthetic `FilmWiki/Projects/demo-editor` namespace. Regular public Wiki pages and baseline read/edit APIs remain unchanged.

## One-use login exchange
1. PHP native Wiki login establishes an HttpOnly SameSite=Strict session under 8080. Its current user ID/roles/password-hash fingerprint are validated against the shared SQLite on every managed-page access.
2. `plugin=fwsso` GET checks the current PHP login and project membership, then returns only a CSRF challenge with no-store headers.
3. Browser POSTs that CSRF to the native PHP plugin with same-origin cookies and trusted localhost Origin. PHP signs a JSON assertion using HMAC-SHA256 with a 256-bit random key mounted read-only into PHP, valid for at most 45 seconds. Claims include issuer, audience, subject user ID, issue/expiry times, and a random 192-bit one-time ID. No bearer is put in the URL.
4. The browser sends the assertion and signature by cross-port JSON POST to 127.0.0.1:8098/v5/sso. The Python service independently validates exact schema, signature, time, issuer, audience, active account, and current demo project membership.
5. SQLite `BEGIN IMMEDIATE` enforces one-time redemption by unique hashed nonce and creates a new 8-hour hashed bearer session atomically. A replay, forgery, expired ticket, revoked user, or foreign-project account is rejected; concurrent redemption produces at most one successful session.
6. `collab.js` first attempts the Wiki session exchange silently on open. If absent/invalid it retains the existing account-password login form and its manual one-click SSO button. The bearer exists in current tab memory only.
- Signing key is at `/home/administrator/filmwiki-data/private/step33-sso-secret.key`, mode 0600, **never archive its plaintext or commit it**. PHP sees `/run/filmwiki/step33-sso-key` via a read-only Compose mount. The collaboration service uses `--sso-secret-file` and never returns the key. If lost, rotate it and restart PHP and 8098.

## Resource ACL and information leak audit
- The **entire native attachment subsystem remains quarantined for this synthetic protected namespace**. Both `plugin=ref` inline images and `plugin=attach` upload/download/info/list are explicitly rejected with HTTP 403 before any filename lookup, regardless of role. This is intentionally more restrictive than a per-object allowlist until actual attachment ownership and sharing rules exist.
- Nginx continues to block direct `/attach/`, `/backup/`, `/cache/`, `/diff/`, `/wiki/`, `/lib/` filesystem URLs; its .htaccess files alone are not relied upon.
- Global `list/filelist` and `backup` listings now pass each page through `is_page_readable` before rendering.
- The recent-updates sidebar also filters private page links. `lib/file.php` requests for `RecentChanges` are filtered by the Step32 current read ACL at source/HTML retrieval, without rewriting the generated page on disk. This prevents synthetic project titles from leaking through the public RecentChanges source.
- Individual managed-page read, source, diff, backup action, search and Wiki edit checks remain governed by Step32 scoped auth. Note that not all legacy plugins and extensions have undergone exhaustive review. **Real sensitive content remains disallowed**.

## Actual acceptance
- Seven standalone SSO unit tests cover one-use, concurrent redemption, expiration, invalid audiences/issuers, tampered HMAC, bad claims, revoked accounts, and outsider users. All prior 12 Step31 identity tests pass.
- Real PHP 8080 → Python test port 8099: three project member roles successfully exchange native login into scoped session; outsider denied by PHP; reused or bad signature returns 401; session logout disables subsequent requests.
- Production 8098 SSO-enabled service remains bound to loopback, with old password form operational; 8091 and 8094 unchanged.
- Actual browser DOM on local static collaboration page auto-connects via native Wiki cookie, reaches 8098, displays director user/project without re-entering password, then logs out (7 HTTP requests, 2 POSTs).
- Anonymous HTTP tests confirm protected attachment calls 403, ordinary public Wiki routes 200; backup/list/filelist/recent/index source do not show the protected synthetic page name. Earlier raw RecentChanges and global backup listings did show private page names: regression fixed.

## Limitations and next security phase
- Logging out of PHP does not automatically revoke an already issued collaboration bearer token, and vice versa. Each API obeys its own logout and revocation semantics, and account disable/role changes are checked. Full identity-provider federation and global session revocation are deferred.
- No arbitrary project namespaces, true multi-tenant role hierarchy, remote HTTPS, refresh tokens, device/session management or production attachment sharing.
- PukiWiki plugins that read `RecentChanges`/backup files directly, external feeds/exports, caches, third-party extensions and side channels require a broader negative-access audit before any company/private data is onboarded.

## Rollback
- The NAS operation folder `X:/AI/OperationArchive/FilmWiki/20261014-210453-step33-sso-resource-acl/before` contains original native file snapshots and old collaboration systemd unit. Code rollback should restore these files, then restart `filmwiki-collaboration.service` and PHP with the previous Compose override.
- Keep the Step31 identity DB, synthetic edit DB, original Wiki pages, and all revision events. Do not delete passwords, sessions or source data to roll back a feature. The new SSO nonce table is additive and may remain.
- The signing key remains local/private, **not** in the NAS operation archive.
