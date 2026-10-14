#!/usr/bin/env python3
"""Install a narrowly scoped native PukiWiki ACL bridge with rollback copies.

Does NOT enable read_auth/edit_auth globally; only the owned demo namespace is
protected. New PHP library+plugin are in git. Identity database must be mounted
read-only into PHP container at /run/filmwiki/identity.sqlite beforehand.
"""
import argparse,hashlib,json,os
from pathlib import Path

PREFIX="FilmWiki/Projects/demo-editor"
BEGIN="// FilmWiki Step32 managed namespace integration"
EXPECTED="function _is_page_accessible($page, $auth_pages) {"
PROJECT="demo-editor"
ACL='#^FilmWiki/Projects/demo-editor(?:/|$)#u'

def plan_auth(old):
    if BEGIN in old:return old
    replacements=[
      (
       'function is_page_readable($page) {\n\tglobal $read_auth_pages, $read_auth;\n',
       'function is_page_readable($page) {\n\tglobal $read_auth_pages, $read_auth;\n'
       '\tif (filmwiki_unified_is_managed_page($page)) {\n'
       '\t\treturn _is_page_accessible($page, array("'+ACL+'"=>"fw-demo-editor-read"));\n'
       '\t}\n'
      ),
      (
       'function is_page_writable($page) {\n\tglobal $edit_auth_pages, $edit_auth;\n',
       'function is_page_writable($page) {\n\tglobal $edit_auth_pages, $edit_auth;\n'
       '\tif (filmwiki_unified_is_managed_page($page)) {\n'
       '\t\tif (!filmwiki_unified_edit_csrf_ok()) return FALSE;\n'
       '\t\treturn _is_page_accessible($page, array("'+ACL+'"=>"fw-demo-editor-edit"));\n'
       '\t}\n'
      ),
      (
       'function edit_auth($page, $auth_enabled = TRUE, $exit_on_fail = TRUE)\n{\n'
       '\tglobal $edit_auth, $edit_auth_pages, $_title_cannotedit;\n',
       'function edit_auth($page, $auth_enabled = TRUE, $exit_on_fail = TRUE)\n{\n'
       '\tglobal $edit_auth, $edit_auth_pages, $_title_cannotedit;\n'
       '\tif (filmwiki_unified_is_managed_page($page)) {\n'
       '\t\tif (!filmwiki_unified_edit_csrf_ok()) {\n'
       '\t\t\tif ($exit_on_fail) { header("HTTP/1.1 403 Forbidden"); die_message("Invalid Wiki edit form token"); exit; }\n'
       '\t\t\treturn FALSE;\n'
       '\t\t}\n'
       '\t\treturn basic_auth($page,$auth_enabled,$exit_on_fail,\n'
       '\t\t\tarray("'+ACL+'"=>"fw-demo-editor-edit"),$_title_cannotedit);\n'
       '\t}\n'
      ),
      (
       'function read_auth($page, $auth_enabled = TRUE, $exit_on_fail = TRUE)\n{\n'
       '\tglobal $read_auth, $read_auth_pages, $_title_cannotread;\n',
       'function read_auth($page, $auth_enabled = TRUE, $exit_on_fail = TRUE)\n{\n'
       '\tglobal $read_auth, $read_auth_pages, $_title_cannotread;\n'
       '\tif (filmwiki_unified_is_managed_page($page)) {\n'
       '\t\treturn basic_auth($page,$auth_enabled,$exit_on_fail,\n'
       '\t\t\tarray("'+ACL+'"=>"fw-demo-editor-read"),$_title_cannotread);\n'
       '\t}\n'
      ),
      (
       EXPECTED+'\n\tglobal $auth_method_type, $auth_user_groups, $auth_user;\n',
       EXPECTED+'\n\tglobal $auth_method_type, $auth_user_groups, $auth_user;\n'
       '\tif (filmwiki_unified_is_managed_page($page)) filmwiki_unified_refresh($page);\n'
      )
    ]
    for original,changed in replacements:
        if old.count(original)!=1:raise ValueError("PukiWiki auth.php has diverged: "+original[:70])
        old=old.replace(original,changed,1)
    if old.count("define('AUTH_TYPE_FORM', 3);")!=1:
        raise ValueError("PukiWiki base auth constants diverged")
    old=old.replace("define('AUTH_TYPE_FORM', 3);",
         "define('AUTH_TYPE_FORM', 3);\n"+BEGIN+
         "\nrequire_once __DIR__ . '/filmwiki_unified_bridge.php';",1)
    return old

def plan_edit_html(old):
    if "filmwiki_unified_csrf_input" in old:return old
    needle="\t$s_original  = isset($vars['original']) ? htmlsc($vars['original']) : $s_postdata;\n"
    if old.count(needle)!=1:raise ValueError("Native edit form diverged: original")
    injection=(
        "\t$filmwiki_unified_csrf_input = '';\n"
        "\tif (function_exists('filmwiki_unified_is_managed_page') && filmwiki_unified_is_managed_page($page)) {\n"
        "\t\tfilmwiki_unified_session_start();\n"
        "\t\tif (!isset($_SESSION['filmwiki_csrf']) || !is_string($_SESSION['filmwiki_csrf']) || strlen($_SESSION['filmwiki_csrf'])!==64)\n"
        "\t\t\t$_SESSION['filmwiki_csrf']=bin2hex(random_bytes(32));\n"
        "\t\t$filmwiki_unified_csrf_input='<input type=\"hidden\" name=\"filmwiki_csrf\" value=\"'.htmlsc($_SESSION['filmwiki_csrf']).'\" />';\n"
        "\t}\n"
    )
    old=old.replace(needle,needle+injection)
    needle2='  <input type="hidden" name="cmd"    value="edit" />'
    if old.count(needle2)!=2:raise ValueError("Native edit form diverged: cmd")
    old=old.replace(needle2,'  $filmwiki_unified_csrf_input\n'+needle2,1)
    return old

def install(site,source,archive,apply):
    site=Path(site).resolve(strict=True);source=Path(source).resolve(strict=True)
    archive=Path(archive).resolve(strict=True)
    auth=site/"lib/auth.php"
    html=site/"lib/html.php"
    plugin=site/"plugin/fwunified.inc.php"
    bridge=site/"lib/filmwiki_unified_bridge.php"
    ini=site/"pukiwiki.ini.php"
    if not all(x.is_file() for x in (auth,html,ini)):
        raise ValueError("Not an existing PukiWiki site")
    old=auth.read_text(encoding="utf8")
    changed=plan_auth(old)
    original_html=html.read_text(encoding="utf8")
    candidate_html=plan_edit_html(original_html)
    original_ini=ini.read_text(encoding="utf8")
    if "$read_auth = 0;" not in original_ini or "$edit_auth = 0;" not in original_ini:
        raise ValueError("Refusing to alter live site with unexpected global auth configuration")
    # Security: search must honor the new read restrictions.
    candidate_ini=original_ini.replace("$search_auth = 0;","$search_auth = 1;",1)
    if candidate_ini==original_ini:raise ValueError("search_auth setting unexpected")
    for new in (plugin,bridge):
        if new.exists():raise FileExistsError("No overwrite of existing PukiWiki plugin/library")
    php_files={
      plugin:source/"fwunified.inc.php",
      bridge:source/"filmwiki_unified_bridge.php",
    }
    if not all(x.is_file() for x in php_files.values()):raise ValueError("Missing PHP bridge files")
    baseline={"auth_php_sha256":hashlib.sha256(old.encode()).hexdigest(),
              "html_php_sha256":hashlib.sha256(original_html.encode()).hexdigest(),
              "ini_sha256":hashlib.sha256(original_ini.encode()).hexdigest(),
              "managed_namespace":PREFIX,"other_wiki_pages_auth_unchanged":True}
    if not apply:
        print(json.dumps({"planned":True,**baseline},ensure_ascii=False))
        return
    origdir=archive/"before";origdir.mkdir(exist_ok=True)
    for dest,s in ((origdir/"auth.php",old),(origdir/"html.php",original_html),
                   (origdir/"pukiwiki.ini.php",original_ini)):
        if dest.exists():raise FileExistsError("Backup would overwrite")
        dest.write_text(s,encoding="utf8")
    for dest,file in php_files.items():
        # Never overwrite, plugin must be a new explicit namespace.
        with dest.open("x",encoding="utf8") as handle:handle.write(file.read_text(encoding="utf8"))
    auth.write_text(changed,encoding="utf8")
    html.write_text(candidate_html,encoding="utf8")
    ini.write_text(candidate_ini,encoding="utf8")
    result={"status":"installed","namespace":PREFIX,"global_read_auth":False,
       "global_edit_auth":False,"search_auth":True,"backups":str(origdir),
       "source_sha":baseline}
    (archive/"step32-install-report.json").write_text(
       json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print(json.dumps(result,ensure_ascii=False))

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--site",required=True)
    p.add_argument("--source",required=True)
    p.add_argument("--archive",required=True)
    p.add_argument("--apply",action="store_true")
    a=p.parse_args()
    install(a.site,a.source,a.archive,a.apply)
