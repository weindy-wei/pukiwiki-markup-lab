#!/usr/bin/env python3
"""Build a disposable FilmWiki FTS5/typed-field sidecar on local disk.

Source SQLite is opened mode=ro/query_only; never run live SQLite on SMB.
A fresh sidecar is validated before atomic installation. This is not an online
incremental write index: a changed source requires an explicit rebuild.
"""
import argparse
from contextlib import closing
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE meta (
  project_id TEXT PRIMARY KEY,
  source_sha256 TEXT NOT NULL,
  preview_sha256 TEXT NOT NULL,
  source_size INTEGER NOT NULL,
  source_mtime_ns INTEGER NOT NULL,
  record_count INTEGER NOT NULL,
  cell_count INTEGER NOT NULL,
  format_version INTEGER NOT NULL,
  created_utc TEXT NOT NULL
);
CREATE TABLE cell_docs (
  doc_id INTEGER PRIMARY KEY,
  record_id TEXT NOT NULL,
  sheet_index INTEGER NOT NULL,
  source_row INTEGER NOT NULL,
  column_index INTEGER NOT NULL,
  value_json TEXT NOT NULL,
  value_type TEXT NOT NULL
);
CREATE INDEX ix_cell_docs_sheet_record ON cell_docs(sheet_index,record_id);
CREATE VIRTUAL TABLE cell_fts USING fts5(
  value_json, content='cell_docs', content_rowid='doc_id', tokenize='trigram'
);
CREATE TABLE exact_fields (
  record_id TEXT NOT NULL,
  sheet_index INTEGER NOT NULL,
  source_row INTEGER NOT NULL,
  column_index INTEGER NOT NULL,
  value_type TEXT NOT NULL,
  value_json TEXT NOT NULL,
  number_value REAL,
  PRIMARY KEY (record_id,column_index)
);
CREATE INDEX ix_exact_json ON exact_fields(sheet_index,column_index,value_type,value_json,source_row);
CREATE INDEX ix_numeric ON exact_fields(sheet_index,column_index,value_type,number_value,source_row);
"""

def fingerprint(db,source_sha):
    db=Path(db).resolve(strict=True)
    stat=db.stat()
    if not db.is_file() or not re.fullmatch(r"[0-9a-f]{64}",source_sha):
        raise ValueError("Invalid source database/sha")
    with closing(sqlite3.connect(db.as_uri()+"?mode=ro",uri=True)) as source:
        source.execute("PRAGMA query_only=ON")
        item=source.execute(
            "SELECT preview_sha256 FROM imports WHERE source_sha256=?",(source_sha,)
        ).fetchone()
        if item is None:raise ValueError("Source not found in database")
        records=source.execute("SELECT COUNT(*) FROM records WHERE source_sha256=?",(source_sha,)).fetchone()[0]
        cells=source.execute(
            "SELECT COUNT(*) FROM cells c JOIN records r ON r.record_id=c.record_id WHERE r.source_sha256=?",
            (source_sha,)
        ).fetchone()[0]
    return {"source_sha256":source_sha,"preview_sha256":item[0],
            "source_size":stat.st_size,"source_mtime_ns":stat.st_mtime_ns,
            "record_count":records,"cell_count":cells}


def _kind(value):
    if value is None:return "null",None
    if isinstance(value,bool):return "boolean",None
    if isinstance(value,(int,float)):
        if not isinstance(value,bool) and abs(float(value)) <= 1e300:
            return "number",float(value)
        return "number",None
    if isinstance(value,str):return "string",None
    return "json",None


def build_index(db,source_sha,project_id,index_file,private_root,replace=False):
    private_root=Path(private_root).resolve(strict=True)
    db=Path(db).resolve(strict=True)
    target=Path(index_file)
    if not target.is_absolute() or target.is_symlink():
        raise ValueError("Index target must be absolute and not a symlink")
    target=target.resolve()
    if not db.is_relative_to(private_root) or not target.is_relative_to(private_root) or db==target:
        raise ValueError("Source and index must be separate files inside the private local root")
    if not target.parent.is_dir() or not re.fullmatch(r"[a-z][a-z0-9_-]{1,39}",project_id):
        raise ValueError("Invalid index path/project id")
    if target.exists() and not replace:
        raise FileExistsError("Index exists, use --replace for reviewed rebuild")
    before=fingerprint(db,source_sha)
    fd,tempfile_path=tempfile.mkstemp(prefix=".fts5-build-",suffix=".sqlite",dir=target.parent)
    os.close(fd)
    temp=Path(tempfile_path)
    try:
        with closing(sqlite3.connect(temp)) as index, closing(
            sqlite3.connect(db.as_uri()+"?mode=ro",uri=True,timeout=10)
        ) as src:
            src.execute("PRAGMA query_only=ON")
            index.executescript(SCHEMA)
            now=dt.datetime.now(dt.timezone.utc).isoformat()
            with index:
                index.execute("INSERT INTO meta VALUES (?,?,?,?,?,?,?,?,?)",
                    (project_id,source_sha,before["preview_sha256"],before["source_size"],
                     before["source_mtime_ns"],before["record_count"],before["cell_count"],1,now))
                sql=(
                    "SELECT r.record_id,r.sheet_index,r.source_row,c.column_index,c.value_json "
                    "FROM records r JOIN cells c ON c.record_id=r.record_id "
                    "WHERE r.source_sha256=? ORDER BY r.sheet_index,r.source_row,c.column_index"
                )
                doc_id=0
                batch_docs=[];batch_exact=[]
                for rid,si,row,col,raw in src.execute(sql,(source_sha,)):
                    try: value=json.loads(raw)
                    except json.JSONDecodeError as exc:raise ValueError("Invalid canonical value JSON") from exc
                    kind,numeric=_kind(value)
                    doc_id+=1
                    batch_docs.append((doc_id,rid,si,row,col,raw,kind))
                    batch_exact.append((rid,si,row,col,kind,raw,numeric))
                    if len(batch_docs)>=1000:
                        index.executemany("INSERT INTO cell_docs VALUES (?,?,?,?,?,?,?)",batch_docs)
                        index.executemany("INSERT INTO exact_fields VALUES (?,?,?,?,?,?,?)",batch_exact)
                        batch_docs.clear();batch_exact.clear()
                if batch_docs:
                    index.executemany("INSERT INTO cell_docs VALUES (?,?,?,?,?,?,?)",batch_docs)
                    index.executemany("INSERT INTO exact_fields VALUES (?,?,?,?,?,?,?)",batch_exact)
                # External content table is fully populated first.
                index.execute("INSERT INTO cell_fts(cell_fts) VALUES('rebuild')")
                if doc_id!=before["cell_count"]:
                    raise RuntimeError("Source cells changed during index build")
            assert index.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
            result=index.execute("SELECT count(*) FROM exact_fields").fetchone()[0]
            if result!=before["cell_count"]:raise RuntimeError("Index count mismatch")
            index.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if fingerprint(db,source_sha)!=before:
            raise RuntimeError("Source changed during index build; refusing stale sidecar")
        os.chmod(temp,0o600)
        os.replace(temp,target)
        return {"project_id":project_id,"database":str(db),"index":str(target),
                "records":before["record_count"],"indexed_cells":before["cell_count"],
                "source_sha256":source_sha,"index_size":target.stat().st_size,
                "original_source_modified":False,"index_replaced":replace}
    finally:
        temp.unlink(missing_ok=True)


def validate_index(db,source_sha,project_id,index_file):
    source=fingerprint(db,source_sha)
    file=Path(index_file).resolve(strict=True)
    if not file.is_file():raise ValueError("Missing search index")
    with closing(sqlite3.connect(file.as_uri()+"?mode=ro",uri=True)) as index:
        row=index.execute("SELECT source_sha256,preview_sha256,source_size,source_mtime_ns,"
                          "record_count,cell_count,format_version FROM meta WHERE project_id=?",
                          (project_id,)).fetchone()
        expected=(source["source_sha256"],source["preview_sha256"],
                  source["source_size"],source["source_mtime_ns"],
                  source["record_count"],source["cell_count"],1)
        if tuple(row or ())!=expected:
            raise ValueError("Stale/mismatched FTS index; rebuild required")
        if index.execute("SELECT count(*) FROM exact_fields").fetchone()[0]!=source["cell_count"]:
            raise ValueError("Corrupt/partial exact index")
    return True


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root",type=Path,required=True)
    ap.add_argument("--db",type=Path,required=True)
    ap.add_argument("--source-sha",required=True)
    ap.add_argument("--project-id",required=True)
    ap.add_argument("--index",type=Path,required=True)
    ap.add_argument("--replace",action="store_true")
    args=ap.parse_args()
    result=build_index(args.db,args.source_sha,args.project_id,args.index,args.data_root,
                       replace=args.replace)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
