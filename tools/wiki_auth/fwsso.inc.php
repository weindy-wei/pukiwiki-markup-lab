<?php
/**
 * Step33 local-only, one-use HMAC assertion from native PukiWiki cookie to
 * authenticated FilmWiki collaboration API. No bearer in URL or PHP logs.
 * This is a one-way SSO login bridge, not global logout federation.
 */
function plugin_fwsso_action()
{
    if (!function_exists('filmwiki_unified_actor')) {
        require_once LIB_DIR . 'filmwiki_unified_bridge.php';
    }
    header('Content-Type: application/json; charset=UTF-8');
    header('Cache-Control: no-store, private');
    header('Pragma: no-cache');
    header('X-Content-Type-Options: nosniff');
    $host = isset($_SERVER['HTTP_HOST']) ? strtolower($_SERVER['HTTP_HOST']) : '';
    if (!in_array($host, array('127.0.0.1:8080','localhost:8080'), true)) {
        header('HTTP/1.1 403 Forbidden');
        echo json_encode(array('error'=>'invalid_host'));exit;
    }
    filmwiki_unified_session_start();
    $uid=isset($_SESSION['filmwiki_unified_uid']) ? $_SESSION['filmwiki_unified_uid'] : null;
    $guard=isset($_SESSION['filmwiki_unified_guard']) ? $_SESSION['filmwiki_unified_guard'] : '';
    $actor=filmwiki_unified_actor($uid);
    if (!$actor || !is_string($guard) || !hash_equals($actor['session_guard'],$guard)) {
        header('HTTP/1.1 401 Unauthorized');
        echo json_encode(array('error'=>'wiki_login_required'));exit;
    }
    if (!array_intersect($actor['roles'],array('viewer','editor','reviewer','publisher','owner'))) {
        header('HTTP/1.1 403 Forbidden');
        echo json_encode(array('error'=>'project_not_authorized'));exit;
    }
    if (!isset($_SESSION['filmwiki_csrf']) ||
        !is_string($_SESSION['filmwiki_csrf']) ||
        !preg_match('/^[a-f0-9]{64}$/D',$_SESSION['filmwiki_csrf'])) {
        $_SESSION['filmwiki_csrf']=bin2hex(random_bytes(32));
    }
    if ($_SERVER['REQUEST_METHOD']==='GET') {
        echo json_encode(array('authenticated'=>true,'username'=>$actor['username'],
                 'csrf'=>$_SESSION['filmwiki_csrf']));exit;
    }
    if ($_SERVER['REQUEST_METHOD']!=='POST') {
        header('HTTP/1.1 405 Method Not Allowed');
        echo json_encode(array('error'=>'method_not_allowed'));exit;
    }
    $origin=isset($_SERVER['HTTP_ORIGIN']) ? $_SERVER['HTTP_ORIGIN'] : '';
    $csrf=isset($_POST['csrf']) ? $_POST['csrf'] : null;
    if (!in_array($origin,array('http://127.0.0.1:8080','http://localhost:8080'),true) ||
        !is_string($csrf) || !hash_equals($_SESSION['filmwiki_csrf'],$csrf)) {
        header('HTTP/1.1 403 Forbidden');
        echo json_encode(array('error'=>'csrf_or_origin_denied'));exit;
    }
    $keyfile='/run/filmwiki/step33-sso-key';
    if (!is_readable($keyfile)) {
        header('HTTP/1.1 503 Service Unavailable');
        echo json_encode(array('error'=>'exchange_unavailable'));exit;
    }
    $hex=trim(file_get_contents($keyfile));
    if (!preg_match('/^[a-f0-9]{64}$/D',$hex)) {
        header('HTTP/1.1 503 Service Unavailable');
        echo json_encode(array('error'=>'invalid_exchange_config'));exit;
    }
    $now=time();
    $payload=json_encode(array('v'=>1,'iss'=>'filmwiki-native-local',
       'aud'=>'filmwiki-collab-local','sub'=>$actor['user_id'],
       'iat'=>$now,'exp'=>$now+45,'jti'=>bin2hex(random_bytes(24))),
       JSON_UNESCAPED_SLASHES);
    $wire=rtrim(strtr(base64_encode($payload),'+/','-_'),'=');
    $signature=hash_hmac('sha256',$wire,hex2bin($hex));
    echo json_encode(array('assertion'=>$wire,'signature'=>$signature));exit;
}
