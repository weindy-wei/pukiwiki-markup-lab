#!/usr/bin/env python3
"""Root supervisor for installed FilmWiki hourly index reconciliation.

Worker drops to WSL administrator for private sidecar changes. Restart root-owned
systemd API only after successful changes; verify authenticated page per project.
Never log bearer secret or record content. Synthetic first, owner-local only.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import time
import urllib.request

ROOT=Path("/home/administrator/filmwiki-data")
REGISTRY=ROOT/"private/projects.json"
INDEX_DIR=ROOT/"indexes"
REPORT=ROOT/"reports/index-reconcile-last.json"
TOKEN=ROOT/"private/api-token.txt"
WORKER=ROOT/"services/index_reconciler.py"
SERVICE="filmwiki-readapi.service"
BASE="http://127.0.0.1:8091"

def verify_live():
    credential=TOKEN.read_text(encoding="utf-8").strip()
    headers={"Authorization":"Bearer "+credential,"Origin":"http://127.0.0.1:8080"}
    def request(path):
        req=urllib.request.Request(BASE+path,headers=headers)
        with urllib.request.urlopen(req,timeout=6) as result:
            if result.status!=200:raise RuntimeError("API returned non-200 response")
            return json.load(result)
    projects=request("/v2/projects")["projects"]
    if not projects:raise RuntimeError("No projects in reloaded API")
    for entry in projects:
        slug=entry["id"]
        manifest=request("/v2/projects/"+slug+"/manifest")
        if manifest.get("project",{}).get("id")!=slug:
            raise RuntimeError("Project identity differs after reload")
        if manifest["sheets"]:
            number=manifest["sheets"][0]["sheet_index"]
            page=request("/v2/projects/"+slug+"/records?sheet="+str(number)+"&limit=1")
            if page.get("project_id")!=slug:
                raise RuntimeError("Indexed query project mismatch after reload")
    return len(projects)


def one_pass(apply=False,restart=True):
    p=pwd.getpwnam("administrator")
    command=[sys.executable,str(WORKER),"--data-root",str(ROOT),
             "--projects-file",str(REGISTRY),"--index-root",str(INDEX_DIR),
             "--report-file",str(REPORT)]
    if apply:command.append("--apply")
    result=subprocess.run(command,capture_output=True,text=True,timeout=900,
                          user=p.pw_uid,group=p.pw_gid,extra_groups=[])
    if result.returncode:
        raise RuntimeError("Index worker failed: exit "+str(result.returncode))
    status=json.loads(result.stdout.strip())
    if status["failures"]:
        raise RuntimeError("One or more index rebuilds failed")
    if apply and status["restart_required"] and restart:
        proc=subprocess.run(["/usr/bin/systemctl","restart",SERVICE],
                            capture_output=True,text=True,timeout=30)
        if proc.returncode:raise RuntimeError("systemd API restart failed")
        last=None
        for _ in range(12):
            try:
                count=verify_live()
                status["reloaded_projects"]=count
                return status
            except (OSError,ValueError,KeyError,RuntimeError) as error:
                last=type(error).__name__
                time.sleep(1)
        raise RuntimeError("API did not pass post-restart verification: "+str(last))
    return status


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run",action="store_true",help="Verify only, no builds or restarts")
    parser.add_argument("--no-restart",action="store_true",help="Test rebuilt indexes without service reload")
    args=parser.parse_args()
    status=one_pass(apply=not args.dry_run,restart=not args.no_restart)
    print(json.dumps({
        "result":"pass","mode":status["mode"],
        "projects":status["projects"],
        "changed":[p["project"] for p in status["changed"]],
        "failures":len(status["failures"]),
        "reloaded_projects":status.get("reloaded_projects",0)
    },ensure_ascii=False))


if __name__=="__main__":
    main()
