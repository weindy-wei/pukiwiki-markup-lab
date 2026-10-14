#!/usr/bin/env python3
"""Step32 explicit owner rollback, restoring precisely the pre-install Wiki hooks.

Never deletes Wiki pages, PukiWiki revision history, FilmWiki accounts or DBs.
Docker PHP mount override needs separate controlled compose recreation.
"""
import argparse,hashlib,json
from pathlib import Path

def restore(site,archive,apply=False):
    site=Path(site).resolve(strict=True);archive=Path(archive).resolve(strict=True)
    before=archive/"before"
    original_auth=before/"auth.php"
    # First pilot installation predates the native form CSRF hook.
    original_html=(before/"html.php" if (before/"html.php").is_file()
                   else archive/"before-csrf-patch/html.php")
    original_ini=before/"pukiwiki.ini.php"
    files=((site/"lib/auth.php",original_auth),
           (site/"lib/html.php",original_html),
           (site/"pukiwiki.ini.php",original_ini))
    if not all(p.is_file() for _,p in files):raise ValueError("Missing rollback backups")
    live=(site/"lib/auth.php").read_text()
    if "FilmWiki Step32 managed namespace integration" not in live:
        raise ValueError("Refuse rollback of a site without the Step32 patch")
    if not apply:return {"can_rollback":True,"restore_files":[str(x) for x,_ in files]}
    rollback_backup=archive/"deployed-before-rollback"
    rollback_backup.mkdir(exist_ok=False)
    for target,original in files:
        (rollback_backup/target.name).write_bytes(target.read_bytes())
        target.write_bytes(original.read_bytes())
    # No deletions: the now-unused bridge/plugin are harmless and retained for
    # traceability. The original auth.php no longer loads the bridge.
    return {"rolled_back":True,"original_auth_restored":True,
            "original_html_restored":True,"original_ini_restored":True,
            "account_db_unchanged":True,"synthetic_wiki_pages_kept":True,
            "manual_next":"Recreate PHP container with original compose only to remove read-only identity mount."}

if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--site",required=True)
    p.add_argument("--archive",required=True)
    p.add_argument("--apply",action="store_true")
    a=p.parse_args()
    print(json.dumps(restore(a.site,a.archive,a.apply),ensure_ascii=False,indent=2))
