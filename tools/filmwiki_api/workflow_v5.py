#!/usr/bin/env python3
"""Step29 local-only: per-project roles, atomic multi-record patches and parent IDs.

This is an application-level authorization framework, NOT user authentication:
only the fixed trusted local operator identity may be used by the current API.
Real-user grants must wait for actual login and separate permissions.
"""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid

from editor_store import (connect,canonical,InvalidEdit,VersionConflict,
                          DuplicateRequest,MissingRecord,_event_hash)

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_role_grants (
  project_id TEXT NOT NULL,actor TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('editor','reviewer','publisher')),
  PRIMARY KEY(project_id,actor,role)
);
CREATE TABLE IF NOT EXISTS workflow_batches (
  batch_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  actor TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_utc TEXT NOT NULL,
  events INTEGER NOT NULL,
  batch_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_relation_overrides (
  child_record_id TEXT PRIMARY KEY REFERENCES records(record_id),
  parent_record_id TEXT REFERENCES records(record_id),
  batch_id TEXT NOT NULL REFERENCES workflow_batches(batch_id),
  version INTEGER NOT NULL,
  CHECK(child_record_id!=parent_record_id)
);
CREATE INDEX IF NOT EXISTS ix_workflow_relation_parent
  ON workflow_relation_overrides(parent_record_id);
"""
ROLE_ACTIONS={
    "batch_edit":"editor",
    "prepare_release":"editor",
    "approve_release":"reviewer",
    "publish_release":"publisher"
}
MAX_BATCH=20
SAFE_RECORD=re.compile(r"src_[a-zA-Z0-9_-]{1,95}")
ACTOR="local-owner"  # Not supplied by an unauthenticated HTTP client.


def setup(db,project_id,owner=ACTOR):
    if not re.fullmatch("[a-z][a-z0-9_-]{1,39}",project_id):
        raise InvalidEdit("Invalid project")
    with closing(connect(db)) as con:
        try:
            meta=con.execute("SELECT project_id FROM editor_meta").fetchone()
            if not meta or meta["project_id"]!=project_id:
                raise InvalidEdit("Working copy does not match project")
            # sqlite3.executescript commits prior transactions. Migrate schema
            # before beginning the atomic role-grant transaction.
            con.executescript(SCHEMA)
            con.execute("BEGIN IMMEDIATE")
            # Only a server-internal owner identity, never an anonymous caller.
            for role in ("editor","reviewer","publisher"):
                con.execute("INSERT OR IGNORE INTO workflow_role_grants VALUES(?,?,?)",
                            (project_id,owner,role))
            con.commit()
        except Exception:
            if con.in_transaction:con.rollback()
            raise
    return {"project":project_id,"role_count":3}


def authorize(con,project_id,actor,action):
    required=ROLE_ACTIONS.get(action)
    if required is None or (actor!=ACTOR and not re.fullmatch(r"user:[0-9a-f-]{36}",actor)):
        raise PermissionError("No approved local operator identity")
    if not con.execute(
        "SELECT 1 FROM workflow_role_grants WHERE project_id=? AND actor=? AND role=?",
        (project_id,actor,required)
    ).fetchone():
        raise PermissionError("Missing project permission: "+required)


def _uuid4(value):
    try:
        parsed=uuid.UUID(value)
        return type(value) is str and parsed.version==4 and str(parsed)==value
    except (TypeError,ValueError,AttributeError):return False


def _relation_candidates(con,sha,sheet,child,target):
    link=con.execute(
        "SELECT status,parent_label,parent_record_id,candidate_record_ids_json "
        "FROM shot_parent_links WHERE child_record_id=?",(child,)
    ).fetchone()
    if not link:
        raise InvalidEdit("This record has no imported parent relation to resolve")
    if target is None:
        return
    if not SAFE_RECORD.fullmatch(target) or child==target:
        raise InvalidEdit("Invalid/self parent ID")
    parent=con.execute(
        "SELECT record_id FROM records WHERE record_id=? AND sheet_index=? AND source_sha256=?",
        (target,sheet,sha)
    ).fetchone()
    if not parent:raise InvalidEdit("Parent must be an existing record on the same worksheet")
    # Imported SC_06 is duplicated; never invent a target from its label.
    candidates=json.loads(link["candidate_record_ids_json"])
    if target not in candidates:
        raise InvalidEdit("Parent must be explicitly selected from imported relation candidates")


def relation(db,sheet,rid):
    with closing(connect(db,readonly=True)) as con:
        meta=con.execute("SELECT source_sha FROM editor_meta").fetchone()
        if not meta:raise InvalidEdit("Editor has no metadata")
        row=con.execute("SELECT r.record_id,v.version FROM records r JOIN record_versions v USING(record_id) "
                        "WHERE r.source_sha256=? AND r.sheet_index=? AND r.record_id=?",
                        (meta[0],sheet,rid)).fetchone()
        if not row:raise MissingRecord(rid)
        link=con.execute(
            "SELECT parent_label,status,parent_record_id,candidate_record_ids_json "
            "FROM shot_parent_links WHERE child_record_id=?",(rid,)).fetchone()
        if not link:
            return {"record_id":rid,"version":row["version"],"relation":None}
        override=con.execute("SELECT parent_record_id,batch_id FROM workflow_relation_overrides "
                             "WHERE child_record_id=?",(rid,)).fetchone()
        parent=(override["parent_record_id"] if override else link["parent_record_id"])
        return {"record_id":rid,"version":row["version"],
                "relation":{"label":link["parent_label"],
                   "import_status":link["status"],
                   "candidate_record_ids":json.loads(link["candidate_record_ids_json"]),
                   "parent_record_id":parent,
                   "effective_status":"cleared" if override and parent is None
                         else ("resolved" if parent else link["status"]),
                   "has_override":bool(override)}}


def _validate_item(item):
    if not isinstance(item,dict) or set(item) not in (
        {"kind","sheet_index","record_id","column_index","value","expected_version"},
        {"kind","sheet_index","record_id","parent_record_id","expected_version"}
    ):
        raise InvalidEdit("Invalid batch patch shape")
    if item["kind"] not in ("cell","parent"):
        raise InvalidEdit("Invalid edit operation")
    required={"kind","sheet_index","record_id","expected_version"}
    if item["kind"]=="cell" and set(item)!=required|{"column_index","value"}:
        raise InvalidEdit("Invalid cell payload")
    if item["kind"]=="parent" and set(item)!=required|{"parent_record_id"}:
        raise InvalidEdit("Invalid relationship payload")
    if type(item["sheet_index"]) is not int or not 1<=item["sheet_index"]<=100 or \
       type(item["expected_version"]) is not int or item["expected_version"]<0 or \
       type(item["record_id"]) is not str or not SAFE_RECORD.fullmatch(item["record_id"]):
        raise InvalidEdit("Invalid record and expected version")
    if item["kind"]=="cell":
        val=item["value"]
        if type(item["column_index"]) is not int or not 1<=item["column_index"]<=256 or \
           type(val) not in (str,int,float,bool,type(None)) or \
           isinstance(val,str) and (len(val)>10000 or "\x00" in val):
            raise InvalidEdit("Invalid field/JSON scalar")
        value=canonical(val)
        if len(value.encode("utf-8"))>24000:raise InvalidEdit("Value exceeds limit")
    elif item["parent_record_id"] is not None and (
        type(item["parent_record_id"]) is not str or
        not SAFE_RECORD.fullmatch(item["parent_record_id"])
    ):raise InvalidEdit("Invalid parent record ID")


def batch_change(db,project_id,operations,reason,batch_id,actor=ACTOR):
    if not isinstance(operations,list) or not 1<=len(operations)<=MAX_BATCH or \
       type(reason) is not str or not 3<=len(reason.strip())<=200 or \
       not _uuid4(batch_id):
        raise InvalidEdit("Bad batch input/size/reason/UUID")
    seen=set()
    for item in operations:
        _validate_item(item)
        if item["record_id"] in seen:
            raise InvalidEdit("One operation per record per atomic batch")
        seen.add(item["record_id"])
    with closing(connect(db)) as con:
        try:
            con.execute("BEGIN IMMEDIATE")
            meta=con.execute("SELECT project_id,source_sha FROM editor_meta").fetchone()
            if not meta or meta["project_id"]!=project_id:raise InvalidEdit("Wrong project")
            authorize(con,project_id,actor,"batch_edit")
            if con.execute("SELECT 1 FROM workflow_batches WHERE batch_id=?",(batch_id,)).fetchone():
                raise DuplicateRequest("Batch already applied")
            prepared=[]
            for item in operations:
                rid=item["record_id"];sheet=item["sheet_index"]
                row=con.execute("SELECT v.version FROM records r JOIN record_versions v USING(record_id) "
                                "WHERE r.record_id=? AND r.source_sha256=? AND r.sheet_index=?",
                                (rid,meta["source_sha"],sheet)).fetchone()
                if not row:raise MissingRecord(rid)
                if row[0]!=item["expected_version"]:raise VersionConflict("Stale record in batch")
                if item["kind"]=="cell":
                    col=item["column_index"]
                    if not con.execute("SELECT 1 FROM edit_permissions WHERE sheet_index=? "
                                       "AND column_index=?",(sheet,col)).fetchone():
                        raise InvalidEdit("Field not editable")
                    original=con.execute(
                        "SELECT value_json FROM cells WHERE record_id=? AND column_index=?",
                        (rid,col)).fetchone()
                    if original is None:raise InvalidEdit("Missing cell")
                    before=original[0];after=canonical(item["value"])
                else:
                    _relation_candidates(con,meta["source_sha"],sheet,rid,item["parent_record_id"])
                    col=0
                    override=con.execute("SELECT parent_record_id FROM workflow_relation_overrides "
                                         "WHERE child_record_id=?",(rid,)).fetchone()
                    if override is not None:current=override[0]
                    else:
                        imported=con.execute("SELECT parent_record_id FROM shot_parent_links "
                                             "WHERE child_record_id=?",(rid,)).fetchone()
                        current=imported[0] if imported else None
                    before=canonical(current);after=canonical(item["parent_record_id"])
                if before==after:raise InvalidEdit("No-op batch operation")
                old=con.execute("SELECT event_hash FROM revision_events WHERE record_id=? "
                                "ORDER BY version DESC LIMIT 1",(rid,)).fetchone()
                prepared.append((item,col,before,after,row[0]+1,old[0] if old else "0"*64))
            now=datetime.now(timezone.utc).isoformat()
            digest=hashlib.sha256(canonical([batch_id,project_id,actor,reason.strip(),
                                            operations,now]).encode()).hexdigest()
            con.execute("INSERT INTO workflow_batches VALUES(?,?,?,?,?,?,?)",
                        (batch_id,project_id,actor,reason.strip(),now,len(operations),digest))
            changes=[]
            for item,col,before,after,version,previous in prepared:
                rid=item["record_id"];sheet=item["sheet_index"]
                event_id=str(uuid.uuid5(uuid.UUID(batch_id),rid))
                event_hash=_event_hash([rid,sheet,col,version,before,after,reason.strip(),
                                        actor,now,event_id,previous])
                if item["kind"]=="cell":
                    con.execute("UPDATE cells SET value_json=?,is_null=? "
                                "WHERE record_id=? AND column_index=?",
                                (after,int(item["value"] is None),rid,col))
                else:
                    con.execute("INSERT INTO workflow_relation_overrides "
                                "(child_record_id,parent_record_id,batch_id,version) VALUES(?,?,?,?) "
                                "ON CONFLICT(child_record_id) DO UPDATE SET "
                                "parent_record_id=excluded.parent_record_id,"
                                "batch_id=excluded.batch_id,version=excluded.version",
                                (rid,item["parent_record_id"],batch_id,version))
                con.execute("UPDATE record_versions SET version=? WHERE record_id=?",(version,rid))
                con.execute("INSERT INTO revision_events "
                    "(record_id,sheet_index,column_index,version,old_json,new_json,reason,"
                    "actor,utc_created,request_id,previous_hash,event_hash)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid,sheet,col,version,before,after,reason.strip(),actor,
                     now,event_id,previous,event_hash))
                changes.append({"record_id":rid,"version":version,"kind":item["kind"]})
            con.execute("COMMIT")
            return {"batch_id":batch_id,"project_id":project_id,"changes":changes,
                    "audit_events":len(changes),"atomic":True}
        except Exception:
            if con.in_transaction:con.rollback()
            raise


def validate_release_relations(con):
    rows=con.execute(
        "SELECT o.child_record_id,o.parent_record_id,r.source_sha256,r.sheet_index,"
        "p.candidate_record_ids_json FROM workflow_relation_overrides o "
        "JOIN records r ON r.record_id=o.child_record_id "
        "JOIN shot_parent_links p ON p.child_record_id=o.child_record_id"
    ).fetchall()
    issues=[]
    for item in rows:
        target=item["parent_record_id"]
        if target is None:continue
        match=con.execute("SELECT 1 FROM records WHERE record_id=? AND source_sha256=? "
                          "AND sheet_index=?",(target,item["source_sha256"],item["sheet_index"])).fetchone()
        if (not match or target==item["child_record_id"] or
            target not in json.loads(item["candidate_record_ids_json"])):
            issues.append({"child_record_id":item["child_record_id"],"issue":"invalid_parent"})
    return issues
