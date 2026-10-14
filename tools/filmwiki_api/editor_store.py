#!/usr/bin/env python3
"""FilmWiki Step28 isolated working-copy editor: transactional patches and audit.

This database is an owner-controlled COPY of a canonical imported snapshot.
Only cell values on explicitly allowed columns can be edited. Changes, version,
audit hash chain, and FTS5 index updates share one SQLite transaction.
"""
from contextlib import closing
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import uuid

DEFAULT_EDITABLE = {
    # Synthetic Step24 short-film fixture. Shot identity, relations, source and
    # file asset references are intentionally excluded.
    1: (2,3),
    2: (2,3),
    3: (2,3),
    4: (2,3),
    6: (4,5,8,9,10,11,12,13,14,15,16,17),
}
SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE editor_meta (
  project_id TEXT PRIMARY KEY, source_sha TEXT NOT NULL,
  snapshot_sha256 TEXT NOT NULL, created_utc TEXT NOT NULL,
  schema_version INTEGER NOT NULL CHECK(schema_version=1)
);
CREATE TABLE edit_permissions (
  sheet_index INTEGER NOT NULL, column_index INTEGER NOT NULL,
  PRIMARY KEY(sheet_index,column_index)
);
CREATE TABLE record_versions(
  record_id TEXT PRIMARY KEY REFERENCES records(record_id),
  version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0)
);
CREATE TABLE revision_events(
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  record_id TEXT NOT NULL REFERENCES records(record_id),
  sheet_index INTEGER NOT NULL,
  column_index INTEGER NOT NULL,
  version INTEGER NOT NULL CHECK(version>=1),
  old_json TEXT NOT NULL, new_json TEXT NOT NULL,
  reason TEXT NOT NULL, actor TEXT NOT NULL,
  utc_created TEXT NOT NULL, request_id TEXT UNIQUE NOT NULL,
  previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL,
  UNIQUE(record_id,version)
);
CREATE INDEX idx_revision_record ON revision_events(record_id,version);
CREATE TRIGGER no_audit_delete BEFORE DELETE ON revision_events
BEGIN SELECT RAISE(ABORT,'revision audit is append-only'); END;
CREATE TRIGGER no_audit_update BEFORE UPDATE ON revision_events
BEGIN SELECT RAISE(ABORT,'revision audit is append-only'); END;
CREATE TABLE edit_cell_docs(
  doc_id INTEGER PRIMARY KEY,
  record_id TEXT NOT NULL,
  sheet_index INTEGER NOT NULL,
  column_index INTEGER NOT NULL,
  value_json TEXT NOT NULL,
  UNIQUE(record_id,column_index)
);
CREATE INDEX idx_edit_doc_record ON edit_cell_docs(record_id,column_index);
CREATE VIRTUAL TABLE edit_fts USING fts5(
  value_json, content='edit_cell_docs', content_rowid='doc_id', tokenize='trigram'
);
CREATE TRIGGER edit_doc_update AFTER UPDATE OF value_json ON edit_cell_docs
BEGIN
  INSERT INTO edit_fts(edit_fts,rowid,value_json) VALUES('delete',old.doc_id,old.value_json);
  INSERT INTO edit_fts(rowid,value_json) VALUES(new.doc_id,new.value_json);
END;
CREATE TRIGGER edit_cell_update AFTER UPDATE OF value_json ON cells
BEGIN
  UPDATE edit_cell_docs SET value_json=new.value_json
    WHERE record_id=new.record_id AND column_index=new.column_index;
END;
"""

class InvalidEdit(ValueError):pass
class VersionConflict(Exception):pass
class DuplicateRequest(Exception):pass
class MissingRecord(KeyError):pass


def canonical(value):
    return json.dumps(value,ensure_ascii=False,separators=(",",":"),allow_nan=False)


def sha_file(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for buf in iter(lambda:stream.read(1024*1024),b""):
            h.update(buf)
    return h.hexdigest()


def connect(path,readonly=False):
    path=Path(path).resolve(strict=True)
    con=sqlite3.connect(path.as_uri()+("?mode=ro" if readonly else "?mode=rw"),
                        uri=True,timeout=8,isolation_level=None)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=8000")
    if readonly:con.execute("PRAGMA query_only=ON")
    return con


def initialize_copy(source,destination,private_root,project_id,source_sha,
                    permissions=None):
    private_root=Path(private_root).resolve(strict=True)
    source=Path(source).resolve(strict=True)
    target=Path(destination)
    if (not re.fullmatch(r"[a-z][a-z0-9_-]{1,39}",project_id)
        or not re.fullmatch("[0-9a-f]{64}",source_sha)
        or not target.is_absolute() or target.is_symlink()):
        raise InvalidEdit("Invalid project, source fingerprint or destination")
    target=target.resolve()
    if not source.is_file() or not source.is_relative_to(private_root) or \
       not target.is_relative_to(private_root) or source==target or target.exists():
        raise InvalidEdit("Only a new private working-copy filename is accepted")
    if permissions is None:permissions=DEFAULT_EDITABLE
    if not target.parent.is_dir():raise InvalidEdit("Working-copy directory missing")
    source_checksum=sha_file(source)
    fd,tmp_path=tempfile.mkstemp(prefix=".filmwiki-edit-build-",suffix=".sqlite",
                                 dir=target.parent)
    os.close(fd)
    tmp=Path(tmp_path)
    try:
        with closing(connect(source,readonly=True)) as original,closing(sqlite3.connect(tmp)) as copy:
            original.backup(copy)
            copy.execute("PRAGMA foreign_keys=ON")
            copy.executescript(SCHEMA)
            source_exists=copy.execute(
                "SELECT source_sha256 FROM imports WHERE source_sha256=?",
                (source_sha,)).fetchone()
            if not source_exists:raise InvalidEdit("Source fingerprint not in database")
            utc=dt.datetime.now(dt.timezone.utc).isoformat()
            with copy:
                copy.execute("INSERT INTO editor_meta VALUES (?,?,?,?,?)",
                             (project_id,source_sha,source_checksum,utc,1))
                for sheet,cols in permissions.items():
                    for col in cols:
                        found=copy.execute("SELECT name FROM fields WHERE source_sha256=? "
                                "AND sheet_index=? AND column_index=?",(source_sha,sheet,col)).fetchone()
                        if found:
                            # Relations/images/shot IDs may require coordinated
                            # validation; never silently enable their edits.
                            if found[0] in {"镜头号","父记录","镜头编号","场景图片","角色资产","场景资产"}:
                                continue
                            copy.execute("INSERT INTO edit_permissions VALUES (?,?)",(sheet,col))
                copy.execute("INSERT INTO record_versions(record_id,version) "
                             "SELECT record_id,0 FROM records WHERE source_sha256=?",(source_sha,))
                copy.execute("INSERT INTO edit_cell_docs(record_id,sheet_index,column_index,value_json) "
                             "SELECT r.record_id,r.sheet_index,c.column_index,c.value_json "
                             "FROM records r JOIN cells c ON c.record_id=r.record_id "
                             "WHERE r.source_sha256=? ORDER BY r.sheet_index,r.source_row,c.column_index",
                             (source_sha,))
                copy.execute("INSERT INTO edit_fts(edit_fts) VALUES('rebuild')")
            if copy.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise RuntimeError("Working-copy SQLite integrity check failed")
        if sha_file(source)!=source_checksum:raise RuntimeError("Source changed while copied")
        os.chmod(tmp,0o600)
        os.replace(tmp,target)
        return {"status":"created","working_copy":str(target),
                "original_sha256":source_checksum,"source_modified":False}
    finally:
        tmp.unlink(missing_ok=True)


def get_meta(db):
    with closing(connect(db,readonly=True)) as con:
        row=con.execute("SELECT * FROM editor_meta").fetchone()
        if row is None:raise InvalidEdit("Not an editor working copy")
        return dict(row)


def manifest(db):
    with closing(connect(db,readonly=True)) as con:
        meta=con.execute("SELECT project_id,source_sha,created_utc FROM editor_meta").fetchone()
        if not meta:raise InvalidEdit("Missing editor meta")
        sheets=[]
        for s in con.execute(
            "SELECT s.sheet_index,s.name,COUNT(r.record_id) AS record_count FROM sheets s "
            "LEFT JOIN records r ON r.sheet_index=s.sheet_index AND r.source_sha256=s.source_sha256 "
            "WHERE s.source_sha256=? GROUP BY s.sheet_index,s.name ORDER BY s.sheet_index",(meta["source_sha"],)
        ):
            fields=[{"index":r["column_index"],"name":r["name"],"editable":bool(r["editable"])}
                for r in con.execute(
                    "SELECT f.column_index,f.name,(e.column_index IS NOT NULL) AS editable "
                    "FROM fields f LEFT JOIN edit_permissions e ON e.sheet_index=f.sheet_index "
                    "AND e.column_index=f.column_index "
                    "WHERE f.source_sha256=? AND f.sheet_index=? ORDER BY f.column_index",
                    (meta["source_sha"],s["sheet_index"]))]
            sheets.append({"sheet_index":s["sheet_index"],"name":s["name"],
                           "record_count":s["record_count"],"fields":fields})
        return {"format":"filmwiki-editor-manifest","version":1,
                "project":{"id":meta["project_id"]},
                "sheets":sheets,"working_copy":True}


def _record(con,sha,sheet,rid):
    row=con.execute(
        "SELECT r.record_id,r.sheet_index,r.source_row,v.version FROM records r "
        "JOIN record_versions v ON v.record_id=r.record_id "
        "WHERE r.record_id=? AND r.sheet_index=? AND r.source_sha256=?",(rid,sheet,sha)
    ).fetchone()
    if row is None:raise MissingRecord(rid)
    values=[{"index":c["column_index"],"name":c["name"],
             "value":json.loads(c["value_json"]),
             "editable":bool(c["editable"])}
       for c in con.execute(
        "SELECT c.column_index,f.name,c.value_json,"
        "(p.column_index IS NOT NULL) AS editable "
        "FROM cells c JOIN fields f ON f.source_sha256=? AND f.sheet_index=? "
        "AND f.column_index=c.column_index "
        "LEFT JOIN edit_permissions p ON p.sheet_index=? AND p.column_index=c.column_index "
        "WHERE c.record_id=? ORDER BY c.column_index",
        (sha,sheet,sheet,rid)
       )]
    return {"record_id":row["record_id"],"sheet_index":row["sheet_index"],
            "source_row":row["source_row"],"version":row["version"],"cells":values}


def record(db,sheet,rid):
    with closing(connect(db,readonly=True)) as con:
        meta=con.execute("SELECT source_sha FROM editor_meta").fetchone()
        if not meta:raise InvalidEdit("Editor not initialized")
        return _record(con,meta[0],sheet,rid)


def list_records(db,sheet,after=1,limit=25,q=""):
    if type(sheet) is not int or not 1<=sheet<=100 or type(after) is not int or after<1 or \
       type(limit) is not int or not 1<=limit<=100 or not isinstance(q,str) or len(q)>80:
        raise InvalidEdit("Invalid page arguments")
    with closing(connect(db,readonly=True)) as con:
        sha=con.execute("SELECT source_sha FROM editor_meta").fetchone()[0]
        query=("SELECT r.record_id,r.source_row,v.version FROM records r "
               "JOIN record_versions v ON v.record_id=r.record_id "
               "WHERE r.source_sha256=? AND r.sheet_index=? AND r.source_row>?")
        args=[sha,sheet,after]
        if q:
            if len(q)>=3:
                phrase='"'+q.replace('"','""')+'"'
                query+=(" AND r.record_id IN ("
                        "SELECT d.record_id FROM edit_fts "
                        "JOIN edit_cell_docs d ON d.doc_id=edit_fts.rowid "
                        "WHERE edit_fts MATCH ? AND d.sheet_index=? AND instr(d.value_json,?)>0)")
                args.extend((phrase,sheet,q))
            else:
                query+=(" AND EXISTS (SELECT 1 FROM cells c "
                        "WHERE c.record_id=r.record_id AND instr(c.value_json,?)>0)")
                args.append(q)
        query+=" ORDER BY r.source_row LIMIT ?"
        args.append(limit+1)
        rows=con.execute(query,args).fetchall()
        data=[_record(con,sha,sheet,row["record_id"]) for row in rows[:limit]]
        return {"format":"filmwiki-editor-page","version":1,
                "sheet_index":sheet,"records":data,
                "next_after":rows[limit-1]["source_row"] if len(rows)>limit else None,
                "count":len(data)}


def history(db,sheet,rid):
    with closing(connect(db,readonly=True)) as con:
        sha=con.execute("SELECT source_sha FROM editor_meta").fetchone()[0]
        _record(con,sha,sheet,rid)
        events=[]
        for e in con.execute(
            "SELECT event_id,version,column_index,old_json,new_json,reason,actor,utc_created,"
            "previous_hash,event_hash,request_id FROM revision_events "
            "WHERE record_id=? ORDER BY version",(rid,)
        ):
            events.append({**dict(e),"old_value":json.loads(e["old_json"]),
                           "new_value":json.loads(e["new_json"])})
        return {"record_id":rid,"events":events,"version":len(events)}


def _event_hash(parts):
    return hashlib.sha256(canonical(parts).encode("utf-8")).hexdigest()


def change(db,project_id,sheet,rid,column,value,expected_version,reason,request_id,
           actor="local-editor"):
    if (type(sheet) is not int or type(column) is not int
        or type(expected_version) is not int or expected_version<0
        or type(rid) is not str or not re.fullmatch(r"src_[a-zA-Z0-9_-]{1,95}",rid)
        or not isinstance(project_id,str) or not isinstance(reason,str) or not 3<=len(reason.strip())<=200
        or not isinstance(actor,str) or len(actor)>64
        or not isinstance(request_id,str)):
        raise InvalidEdit("Invalid patch identity/version/reason")
    try:
        ident=uuid.UUID(request_id)
        if ident.version!=4 or str(ident)!=request_id:raise ValueError()
    except (ValueError,AttributeError):raise InvalidEdit("Invalid request_id UUID4")
    if isinstance(value,(dict,list)) or type(value) not in (str,int,float,bool,type(None)):
        raise InvalidEdit("Only JSON scalar values may be edited")
    if isinstance(value,str) and (len(value)>10000 or "\x00" in value):
        raise InvalidEdit("Text value exceeds limit")
    after=canonical(value)
    if len(after.encode("utf-8"))>24000:raise InvalidEdit("Value too large")
    with closing(connect(db)) as con:
        try:
            con.execute("BEGIN IMMEDIATE")
            meta=con.execute("SELECT project_id,source_sha FROM editor_meta").fetchone()
            if meta is None or meta["project_id"]!=project_id:raise InvalidEdit("Project mismatch")
            row=con.execute(
                "SELECT r.record_id,v.version FROM records r "
                "JOIN record_versions v ON v.record_id=r.record_id "
                "WHERE r.record_id=? AND r.sheet_index=? AND r.source_sha256=?",
                (rid,sheet,meta["source_sha"])).fetchone()
            if row is None:raise MissingRecord(rid)
            if row["version"]!=expected_version:raise VersionConflict("Record version changed")
            permitted=con.execute("SELECT 1 FROM edit_permissions WHERE sheet_index=? AND column_index=?",
                                  (sheet,column)).fetchone()
            if not permitted:raise InvalidEdit("This field is not editable")
            used=con.execute("SELECT 1 FROM revision_events WHERE request_id=?",(request_id,)).fetchone()
            if used:raise DuplicateRequest("request_id already used")
            original=con.execute("SELECT value_json FROM cells WHERE record_id=? AND column_index=?",
                                 (rid,column)).fetchone()
            if original is None:raise InvalidEdit("Missing cell")
            before=original["value_json"]
            if before==after:raise InvalidEdit("No change")
            old_event=con.execute(
                "SELECT event_hash FROM revision_events WHERE record_id=? ORDER BY version DESC LIMIT 1",
                (rid,)).fetchone()
            prev=old_event["event_hash"] if old_event else "0"*64
            version=row["version"]+1
            when=dt.datetime.now(dt.timezone.utc).isoformat()
            event_parts=[rid,sheet,column,version,before,after,reason.strip(),actor,when,request_id,prev]
            digest=_event_hash(event_parts)
            con.execute("UPDATE cells SET value_json=?,is_null=? WHERE record_id=? AND column_index=?",
                        (after,int(value is None),rid,column))
            # Triggers update edit_cell_docs and FTS5 in this exact transaction.
            con.execute("UPDATE record_versions SET version=? WHERE record_id=?",(version,rid))
            con.execute(
                "INSERT INTO revision_events(record_id,sheet_index,column_index,version,old_json,"
                "new_json,reason,actor,utc_created,request_id,previous_hash,event_hash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid,sheet,column,version,before,after,reason.strip(),actor,when,request_id,prev,digest))
            con.execute("COMMIT")
            return {"record_id":rid,"version":version,"event_hash":digest,
                    "column_index":column,"value":value,"audit_written":True}
        except Exception:
            if con.in_transaction:con.execute("ROLLBACK")
            raise


def verify_history(db,rid=None):
    with closing(connect(db,readonly=True)) as con:
        recs=([rid] if rid else [r[0] for r in con.execute(
            "SELECT DISTINCT record_id FROM revision_events")])
        verified=0
        for record_id in recs:
            previous="0"*64
            version=0
            for e in con.execute(
                "SELECT * FROM revision_events WHERE record_id=? ORDER BY version",(record_id,)
            ):
                version+=1
                parts=[e["record_id"],e["sheet_index"],e["column_index"],e["version"],
                       e["old_json"],e["new_json"],e["reason"],e["actor"],
                       e["utc_created"],e["request_id"],e["previous_hash"]]
                if e["version"]!=version or e["previous_hash"]!=previous or \
                   e["event_hash"]!=_event_hash(parts):
                    raise RuntimeError("Audit chain mismatch")
                previous=e["event_hash"];verified+=1
            row=con.execute("SELECT version FROM record_versions WHERE record_id=?",
                            (record_id,)).fetchone()
            if row is None or row["version"]!=version:raise RuntimeError("Version/audit mismatch")
        return {"valid":True,"events":verified,"records_checked":len(recs)}


if __name__=="__main__":
    raise SystemExit("Use server_v4.py or tests; no direct source writes.")
