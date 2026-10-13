#!/usr/bin/env python3
"""Consistent local SQLite snapshot copied and SHA-256-verified on owner NAS.

Never open the NAS copy with SQLite; CIFS/SMB database locks are not reliable.
"""
import argparse
from contextlib import closing
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile


def file_sha256(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):
            digest.update(block)
    return digest.hexdigest()


def backup_snapshot(db_path,local_root,nas_backup_root):
    local_root=Path(local_root).resolve(strict=True)
    source=Path(db_path).resolve(strict=True)
    nas=Path(nas_backup_root).resolve(strict=True)
    if not source.is_file() or not source.is_relative_to(local_root):
        raise ValueError("Source SQLite must be inside private local root")
    if nas.is_relative_to(local_root):
        raise ValueError("Backup destination must differ from the local database root")
    stamp=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    name=stamp+"-"+source.stem+".sqlite"
    final=nas/name
    manifest=nas/(name+".json")
    if final.exists() or manifest.exists():
        raise FileExistsError("Refusing to replace existing NAS backup")
    local_tmp=Path(tempfile.mkstemp(prefix=".filmwiki-backup-",suffix=".sqlite",dir=local_root)[1])
    remote_tmp=nas/("."+name+".partial-"+str(os.getpid()))
    try:
        with closing(sqlite3.connect(source.as_uri()+"?mode=ro",uri=True,timeout=10)) as original:
            original.execute("PRAGMA query_only=ON")
            with closing(sqlite3.connect(local_tmp)) as dst:
                original.backup(dst)
        with closing(sqlite3.connect(local_tmp)) as verification:
            result=verification.execute("PRAGMA integrity_check").fetchone()[0]
            if result!="ok":
                raise RuntimeError("Local SQLite snapshot failed integrity_check")
        expected=file_sha256(local_tmp)
        size=local_tmp.stat().st_size
        with local_tmp.open("rb") as src,remote_tmp.open("xb") as dest:
            shutil.copyfileobj(src,dest,1024*1024)
        if remote_tmp.stat().st_size!=size or file_sha256(remote_tmp)!=expected:
            raise RuntimeError("NAS backup content failed SHA-256 verification")
        os.replace(remote_tmp,final)
        info={"source_name":source.name,"snapshot_file":final.name,
              "sha256":expected,"bytes":size,"integrity_check":"ok",
              "verification":"full SHA-256 after NAS copy",
              "note":"SQLite database file copied as verified inert backup bytes; do not run SQLite over CIFS."}
        with manifest.open("x",encoding="utf-8") as f:
            json.dump(info,f,ensure_ascii=False,indent=2)
            f.write("\n")
        return info
    finally:
        local_tmp.unlink(missing_ok=True)
        remote_tmp.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db",required=True,type=Path)
    p.add_argument("--local-root",required=True,type=Path)
    p.add_argument("--nas-backups",required=True,type=Path,
                   help="Existing owner-controlled NAS backup directory")
    args=p.parse_args()
    print(json.dumps(backup_snapshot(args.db,args.local_root,args.nas_backups),
                     ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
