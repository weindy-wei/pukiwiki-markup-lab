#!/usr/bin/env python3
"""FilmWiki Step27: reconcile allowlisted FTS5 indexes, no canonical DB writes.

Dry-run is default. --apply only touches private sidecar paths. Returns a JSON
status report; a privileged supervisor may restart API *only if* changes occur.
"""
import argparse
from contextlib import contextmanager
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from catalog_v2 import read_registry
from search_index import build_index, validate_index

@contextmanager
def locked_file(folder):
    lock=folder/".index-reconciler.lock"
    fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)


def atomic_json(path,data):
    path=Path(path)
    if path.is_symlink() or not path.parent.is_dir():
        raise ValueError("Invalid report file")
    fd,name=tempfile.mkstemp(prefix=".reconcile-report-",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as out:
            json.dump(data,out,ensure_ascii=False,indent=2)
            out.write("\n")
            out.flush();os.fsync(out.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def reconcile(root,registry,index_root,apply=False,report_file=None):
    root=Path(root).resolve(strict=True)
    index_root=Path(index_root).resolve(strict=True)
    if not index_root.is_dir() or not index_root.is_relative_to(root):
        raise ValueError("Index directory must be private and inside owner root")
    if report_file:
        report_file=Path(report_file).absolute()
        if not report_file.parent.resolve(strict=True).is_relative_to(root):
            raise ValueError("Report cannot escape private owner root")
    started=dt.datetime.now(dt.timezone.utc).isoformat()
    projects=read_registry(registry,root)
    changes=[];unchanged=[];failed=[]
    with locked_file(index_root):
        for project in projects.values():
            pid=project["id"]
            path=index_root/(pid+".search.sqlite")
            if path.is_symlink():
                failed.append({"project":pid,"error":"symlink_index_refused"})
                continue
            stale=False
            if path.exists():
                try:validate_index(project["db"],project["source_sha"],pid,path)
                except (ValueError,sqlite3.Error,OSError):
                    stale=True
            else:stale=True
            if not stale:
                unchanged.append(pid)
                continue
            if not apply:
                changes.append({"project":pid,"status":"needs_rebuild","exists":path.exists()})
                continue
            previous=None
            try:
                # Keep a prior immutable sidecar by hardlink for reviewed recovery.
                # The canonical source is never copied, changed or unlocked.
                if path.is_file():
                    history=index_root/"history"
                    history.mkdir(mode=0o700,exist_ok=True)
                    stamp=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    previous=history/(pid+"-"+stamp+".sqlite")
                    os.link(path,previous,follow_symlinks=False)
                result=build_index(project["db"],project["source_sha"],pid,
                                   path,root,replace=path.exists())
                validate_index(project["db"],project["source_sha"],pid,path)
                changes.append({"project":pid,"status":"rebuilt","rows":result["records"],
                                "indexed_cells":result["indexed_cells"],
                                "prior_index_preserved":previous is not None})
            except (ValueError,RuntimeError,sqlite3.Error,OSError) as exc:
                failed.append({"project":pid,"error":type(exc).__name__})
        result={
            "format":"filmwiki-index-reconcile","version":1,
            "started_utc":started,"finished_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
            "mode":"apply" if apply else "dry_run",
            "projects":len(projects),"unchanged":unchanged,
            "changed":changes,"failures":failed,"restart_required":bool(apply and changes),
            "canonical_data_modified":False,
        }
        if report_file:atomic_json(report_file,result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root",type=Path,required=True)
    parser.add_argument("--projects-file",type=Path,required=True)
    parser.add_argument("--index-root",type=Path,required=True)
    parser.add_argument("--report-file",type=Path)
    parser.add_argument("--apply",action="store_true")
    a=parser.parse_args()
    out=reconcile(a.data_root,a.projects_file,a.index_root,a.apply,a.report_file)
    print(json.dumps(out,ensure_ascii=False))
    if out["failures"]:raise SystemExit(2)


if __name__=="__main__":
    main()
