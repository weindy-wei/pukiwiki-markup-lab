<?php
/**
 * Step32 local FilmWiki/PukiWiki unified login for the protected pilot namespace.
 * PukiWiki native page parser/history/edit controls remain in force.
 */
function plugin_fwunified_action() {
    if (!function_exists('filmwiki_unified_login_password')) {
        require_once LIB_DIR . 'filmwiki_unified_bridge.php';
    }
    if (session_status() !== PHP_SESSION_ACTIVE) session_start();
    $csrf = isset($_SESSION['filmwiki_csrf']) ? $_SESSION['filmwiki_csrf'] : null;
    if (!is_string($csrf) || strlen($csrf)!==64) {
        $csrf = bin2hex(random_bytes(32));
        $_SESSION['filmwiki_csrf'] = $csrf;
    }
    $host = isset($_SERVER['HTTP_HOST']) ? strtolower($_SERVER['HTTP_HOST']) : '';
    if ($host !== '127.0.0.1:8080' && $host !== 'localhost:8080') {
        header('HTTP/1.1 403 Forbidden');
        return array('msg'=>'Forbidden','body'=>'Local Wiki login only.');
    }
    $notice = '';
    if ($_SERVER['REQUEST_METHOD']==='POST') {
        $origin = isset($_SERVER['HTTP_ORIGIN']) ? $_SERVER['HTTP_ORIGIN'] : '';
        $supplied = isset($_POST['csrf']) && is_string($_POST['csrf']) ? $_POST['csrf'] : '';
        if (($origin!=='http://127.0.0.1:8080' && $origin!=='http://localhost:8080') ||
            !hash_equals($csrf,$supplied)) {
            header('HTTP/1.1 403 Forbidden');
            return array('msg'=>'Forbidden','body'=>'Invalid form origin or CSRF token.');
        }
        $action = isset($_POST['action']) ? $_POST['action'] : '';
        if ($action==='logout') {
            unset($_SESSION['filmwiki_unified_uid'],$_SESSION['filmwiki_unified_username'],
                  $_SESSION['authenticated_user'],$_SESSION['authenticated_user_fullname'],
                  $_SESSION['dynamic_member_groups']);
            session_regenerate_id(true);
            $notice = 'Logged out of native Wiki pages.';
        } elseif ($action==='login') {
            $now=time();
            $locked=isset($_SESSION['filmwiki_login_locked']) ? (int)$_SESSION['filmwiki_login_locked'] : 0;
            if ($locked > $now) {
                header('HTTP/1.1 429 Too Many Requests');
                $notice = 'Too many attempts. Retry later.';
            } else {
                $name=isset($_POST['username']) ? $_POST['username'] : '';
                $pw=isset($_POST['password']) ? $_POST['password'] : '';
                $uid=filmwiki_unified_login_password($name,$pw);
                if ($uid===null) {
                    $failures=isset($_SESSION['filmwiki_login_failures']) ?
                              (int)$_SESSION['filmwiki_login_failures']+1 : 1;
                    $_SESSION['filmwiki_login_failures']=$failures;
                    if ($failures>=5) {
                        $_SESSION['filmwiki_login_locked']=$now+900;
                        $_SESSION['filmwiki_login_failures']=0;
                    }
                    header('HTTP/1.1 401 Unauthorized');
                    $notice='Invalid credentials or account unavailable.';
                } else {
                    // Session ID rotation prevents a login-fixation pivot.
                    session_regenerate_id(true);
                    $_SESSION['filmwiki_unified_uid']=$uid;
                    $_SESSION['filmwiki_unified_username']=$name;
                    $_SESSION['authenticated_user']=$name;
                    $_SESSION['authenticated_user_fullname']=$name;
                    $_SESSION['filmwiki_login_failures']=0;
                    $_SESSION['filmwiki_login_locked']=0;
                    $notice='Authenticated with the shared FilmWiki identity.';
                }
            }
        } else {
            header('HTTP/1.1 400 Bad Request');
            $notice='Unknown operation.';
        }
    }
    $actor=isset($_SESSION['filmwiki_unified_uid']) ?
           filmwiki_unified_actor($_SESSION['filmwiki_unified_uid']) : null;
    $formUrl=htmlsc(get_base_uri().'?plugin=fwunified');
    $csrfHTML=htmlsc($csrf);
    $body='<div class="fw-unified-login"><h2>FilmWiki / PukiWiki 统一身份实验台</h2>';
    if ($notice!=='') $body.='<p>'.htmlsc($notice).'</p>';
    if ($actor) {
        $body.='<p>当前账号：<strong>'.htmlsc($actor['username']).'</strong></p>';
        $body.='<p>项目权限：'.htmlsc(implode(', ',$actor['roles'])).'</p>';
        $body.='<p>'.make_pagelink('FilmWiki/Projects/demo-editor/AccessControlTest').'</p>';
        $body.='<form method="post" action="'.$formUrl.'">'
              .'<input type="hidden" name="csrf" value="'.$csrfHTML.'">'
              .'<input type="hidden" name="action" value="logout">'
              .'<button type="submit">退出 Wiki 登录</button></form>';
    } else {
        $body.='<p>使用与 FilmWiki 协作工作台相同的账号和密码。</p>';
        $body.='<form method="post" action="'.$formUrl.'">'
              .'<input type="hidden" name="csrf" value="'.$csrfHTML.'">'
              .'<input type="hidden" name="action" value="login">'
              .'<label>用户名 <input type="text" name="username" required autocomplete="username"></label><br>'
              .'<label>密码 <input type="password" name="password" required autocomplete="current-password"></label><br>'
              .'<button type="submit">登录</button></form>';
    }
    $body.='</div>';
    return array('msg'=>'FilmWiki unified identity','body'=>$body);
}
