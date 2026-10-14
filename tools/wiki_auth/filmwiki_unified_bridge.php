<?php
/**
 * FilmWiki Step32 owner-local read-only identity bridge.
 * Source of truth: Step31 SQLite users/memberships on the same WSL machine.
 * This bridge must NEVER accept a username supplied by an HTTP header.
 */
if (!defined('FILMWIKI_IDENTITY_SQLITE')) {
    define('FILMWIKI_IDENTITY_SQLITE', '/run/filmwiki/identity.sqlite');
}
if (!defined('FILMWIKI_UNIFIED_PROJECT')) {
    define('FILMWIKI_UNIFIED_PROJECT', 'demo-editor');
}
function filmwiki_unified_is_managed_page($page) {
    return is_string($page) &&
        ($page === 'FilmWiki/Projects/demo-editor' ||
         strpos($page, 'FilmWiki/Projects/demo-editor/') === 0);
}
function filmwiki_unified_store() {
    $path = FILMWIKI_IDENTITY_SQLITE;
    if (!is_file($path) || !is_readable($path)) return null;
    try {
        return new SQLite3($path, SQLITE3_OPEN_READONLY);
    } catch (Throwable $e) {
        return null; // fail closed on missing storage
    }
}
function filmwiki_unified_actor($uid) {
    if (!is_string($uid) || !preg_match('/^user:[0-9a-f-]{36}$/D', $uid)) return null;
    $db = filmwiki_unified_store();
    if ($db === null) return null;
    try {
        $stmt = $db->prepare('SELECT user_id,username,active,password_hash FROM users WHERE user_id=:uid');
        $stmt->bindValue(':uid', $uid, SQLITE3_TEXT);
        $u = $stmt->execute()->fetchArray(SQLITE3_ASSOC);
        if (!$u || (int)$u['active'] !== 1) return null;
        $stmt = $db->prepare('SELECT role FROM memberships WHERE user_id=:uid AND project_id=:project');
        $stmt->bindValue(':uid', $uid, SQLITE3_TEXT);
        $stmt->bindValue(':project', FILMWIKI_UNIFIED_PROJECT, SQLITE3_TEXT);
        $rs = $stmt->execute();
        $roles = array();
        while ($r = $rs->fetchArray(SQLITE3_ASSOC)) $roles[] = $r['role'];
        sort($roles, SORT_STRING);
        // Bind PHP native sessions to both password revision and exact ACL.
        // Role or password changes revoke existing Wiki sessions on next read.
        $guard = hash('sha256', $u['user_id']."\n".$u['password_hash']."\n".implode(",",$roles));
        return array('user_id'=>$u['user_id'], 'username'=>$u['username'],
                     'roles'=>$roles, 'session_guard'=>$guard);
    } catch (Throwable $e) {
        return null;
    } finally {
        $db->close();
    }
}
function filmwiki_unified_refresh($page) {
    // Must run on each request, not cache the role set in a PHP session.
    // Do not give PukiWiki's built-in demo accounts special FilmWiki groups.
    if (!filmwiki_unified_is_managed_page($page)) return;
    global $auth_user, $auth_user_fullname, $auth_user_groups;
    $auth_user_groups = array_values(array_diff((array)$auth_user_groups,
                             array('fw-demo-editor-read','fw-demo-editor-edit')));
    if (session_status() !== PHP_SESSION_ACTIVE) session_start();
    $uid = isset($_SESSION['filmwiki_unified_uid']) ? $_SESSION['filmwiki_unified_uid'] : null;
    $actor = filmwiki_unified_actor($uid);
    $expected=isset($_SESSION['filmwiki_unified_guard']) ? $_SESSION['filmwiki_unified_guard'] : '';
    if (!$actor || !is_string($expected) ||
        !hash_equals($actor['session_guard'], $expected)) {
        unset($_SESSION['filmwiki_unified_uid'],$_SESSION['filmwiki_unified_guard']);
        // Clear any claimed Wiki username inherited from a stale unified session.
        if (isset($_SESSION['filmwiki_unified_username'])) {
            unset($_SESSION['authenticated_user'], $_SESSION['authenticated_user_fullname'],
                  $_SESSION['filmwiki_unified_username']);
        }
        $auth_user = '';
        $auth_user_fullname = '';
        return;
    }
    $auth_user = $actor['username'];
    $auth_user_fullname = $actor['username'];
    $_SESSION['authenticated_user'] = $auth_user;
    $_SESSION['authenticated_user_fullname'] = $auth_user;
    $_SESSION['filmwiki_unified_username'] = $auth_user;
    $auth_user_groups[] = $auth_user;
    $read = array_intersect($actor['roles'], array('viewer','editor','reviewer','publisher','owner'));
    $write = array_intersect($actor['roles'], array('editor','owner'));
    if ($read) $auth_user_groups[] = 'fw-demo-editor-read';
    if ($write) $auth_user_groups[] = 'fw-demo-editor-edit';
    $auth_user_groups = array_values(array_unique($auth_user_groups));
}
function filmwiki_unified_login_password($username,$password) {
    if (!is_string($username) || !preg_match('/^[a-z][a-z0-9_.-]{2,31}$/D', $username) ||
        !is_string($password) || strlen($password)>300) return null;
    $db = filmwiki_unified_store();
    if ($db === null) return null;
    try {
        $stmt=$db->prepare('SELECT user_id,salt,password_hash,active,locked_until FROM users WHERE username=:name');
        $stmt->bindValue(':name',$username,SQLITE3_TEXT);
        $row=$stmt->execute()->fetchArray(SQLITE3_ASSOC);
        $salt = $row ? $row['salt'] : str_repeat("\0",24);
        $calculated=hash_pbkdf2('sha256',$password,$salt,600000,32,true);
        if (!$row || (int)$row['active'] !== 1 || time() < (int)$row['locked_until'] ||
            !hash_equals($row['password_hash'],$calculated)) return null;
        return $row['user_id'];
    } catch (Throwable $e) {
        return null;
    } finally {
        $db->close();
    }
}
