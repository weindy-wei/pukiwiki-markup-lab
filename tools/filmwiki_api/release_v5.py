#!/usr/bin/env python3
"""Step29 explicit release stages; never overwrite original or live API projects.

prepare -> approve -> publish. All are LOCAL operator CLI calls. "Publish"
creates an immutable versioned artifact under a private directory, NOT a
deployment to the existing 8091 API. Publishing requires reviewed promotion.
"""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from editor_store import connect,sha_file,verify_history,InvalidEdit
from workflow_v5 import ACTOR,authorize,validate_release_relations

def _utc():return datetime.now(timezone.utc).isoformat()
def _hash(obj):
    return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,
                     separators=(",",":")).encode("utf-8")).hexdigest()
def _save_new(path,data):
    # No shell, arbitrary path, symlink follow or in-place overwrites.
    flags=os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW
    fd=os.open(path,flags,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,sort_keys=True,indent=2)
        f.write("\n");f.flush();os.fsync(f.fileno())
def _meta(directory):
    return json.loads((directory/"manifest.json").read_text(encoding="utf-8"))
def _validated_root(root):
    root=Path(root).resolve(strict=True)
    if not root.is_dir():raise InvalidEdit("Release root missing")
    if root.is_symlink():raise InvalidEdit("Symlink release root")
    for name in ("prepared","published"):
        folder=root/name
        folder.mkdir(mode=0o700,parents=False,exist_ok=True)
        if folder.is_symlink() or not folder.resolve().is_relative_to(root):
            raise InvalidEdit("Unsafe release directory")
    return root

def _verify_snapshot(snapshot,project_id):
    with closing(connect(snapshot,readonly=True)) as con:
        meta=con.execute("SELECT project_id,source_sha FROM editor_meta").fetchone()
        if not meta or meta["project_id"]!=project_id:
            raise InvalidEdit("Snapshot project mismatch")
        if con.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise RuntimeError("SQLite integrity failed")
        if con.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Foreign key integrity failed")
        issues=validate_release_relations(con)
        if issues:raise InvalidEdit("Invalid relationships in release")
        unmatched=con.execute(
          "SELECT count(*) FROM cells c LEFT JOIN edit_cell_docs d "
          "ON c.record_id=d.record_id AND c.column_index=d.column_index "
          "WHERE d.doc_id IS NULL OR c.value_json<>d.value_json").fetchone()[0]
        if unmatched:raise RuntimeError("Search document is out of sync with canonical edited values")
        counts=con.execute(
          "SELECT (SELECT count(*) FROM records),(SELECT count(*) FROM cells),"
          "(SELECT count(*) FROM workflow_relation_overrides),"
          "(SELECT count(*) FROM workflow_batches)").fetchone()
        ver=con.execute("SELECT sum(version) FROM record_versions").fetchone()[0] or 0
        revisions=con.execute("SELECT count(*) FROM revision_events").fetchone()[0]
        if ver!=revisions:raise RuntimeError("Version/event count mismatch")
    audit=verify_history(snapshot)
    if audit["events"]!=revisions:raise RuntimeError("Audit verification failed")
    return {"records":counts[0],"cells":counts[1],"relations":counts[2],
            "batches":counts[3],"audit_events":audit["events"],
            "all_record_versions":ver,"search_docs_synced":True}

def prepare_release(db,release_root,project_id,release_id,actor=ACTOR):
    if type(release_id) is not str or not re.fullmatch(r"[0-9a-f-]{36}",release_id) or \
       str(uuid.UUID(release_id))!=release_id or uuid.UUID(release_id).version!=4:
        raise InvalidEdit("Release id must be UUID4")
    root=_validated_root(release_root)
    source=Path(db).resolve(strict=True)
    if not source.is_file() or not source.is_relative_to(root.parent):
        raise InvalidEdit("Editor source must stay inside private local root")
    with closing(connect(source,readonly=True)) as con:
        authorize(con,project_id,actor,"prepare_release")
    directory=root/"prepared"/release_id
    if (root/"published"/release_id).exists():
        raise FileExistsError("Release already published")
    directory.mkdir(mode=0o700)
    try:
        snap=directory/"snapshot.sqlite"
        with closing(connect(source,readonly=True)) as original, \
             closing(sqlite3.connect(snap)) as target:
            original.backup(target)
            target.execute("PRAGMA foreign_keys=ON")
            target.execute("INSERT INTO edit_fts(edit_fts) VALUES('integrity-check')")
            target.commit()
        evidence=_verify_snapshot(snap,project_id)
        os.chmod(snap,0o600)
        value={"format":"filmwiki-versioned-release","version":1,
               "release_id":release_id,"project_id":project_id,
               "stage":"prepared","created_utc":_utc(),
               "source_snapshot_sha256":sha_file(snap),"evidence":evidence,
               "prepared_by":actor,
               "deployment":"offline artifact only, not in read-only API registry"}
        _save_new(directory/"manifest.json",value)
        return value
    except Exception:
        # Only remove a newly created failed stage directory.
        for child in directory.iterdir():
            if child.is_file() and not child.is_symlink():child.unlink()
        directory.rmdir()
        raise

def _load_checked_stage(root,release_id,where):
    root=_validated_root(root)
    if not isinstance(release_id,str) or not re.fullmatch("[0-9a-f-]{36}",release_id):
        raise InvalidEdit("Invalid release id")
    directory=root/where/release_id
    if directory.is_symlink() or not directory.is_dir() or \
       directory.resolve()!=directory or not directory.is_relative_to(root):
        raise InvalidEdit("Unknown release or unsafe directory")
    manifest=_meta(directory)
    if manifest.get("release_id")!=release_id or manifest.get("stage")!="prepared" or \
       manifest.get("format")!="filmwiki-versioned-release":
        raise InvalidEdit("Invalid release manifest")
    snap=directory/"snapshot.sqlite"
    if snap.is_symlink() or sha_file(snap)!=manifest["source_snapshot_sha256"]:
        raise InvalidEdit("Release snapshot checksum mismatch")
    return directory,manifest

def approve_release(release_root,release_id,actor=ACTOR):
    directory,manifest=_load_checked_stage(release_root,release_id,"prepared")
    snap=directory/"snapshot.sqlite"
    with closing(connect(snap,readonly=True)) as con:
        authorize(con,manifest["project_id"],actor,"approve_release")
    evidence=_verify_snapshot(snap,manifest["project_id"])
    if evidence!=manifest["evidence"]:raise InvalidEdit("Release contents changed")
    approval={"format":"filmwiki-release-approval","release_id":release_id,
              "manifest_hash":_hash(manifest),"snapshot_sha256":manifest["source_snapshot_sha256"],
              "approved_utc":_utc(),"reviewer":actor}
    _save_new(directory/"approval.json",approval)
    return approval

def publish_release(release_root,release_id,actor=ACTOR):
    root=_validated_root(release_root)
    directory,manifest=_load_checked_stage(root,release_id,"prepared")
    approved=directory/"approval.json"
    if approved.is_symlink() or not approved.is_file():
        raise InvalidEdit("Release must be explicitly approved")
    approval=json.loads(approved.read_text(encoding="utf-8"))
    if approval.get("manifest_hash")!=_hash(manifest) or \
       approval.get("snapshot_sha256")!=manifest["source_snapshot_sha256"] or \
       approval.get("release_id")!=release_id or approval.get("reviewer")!=ACTOR:
        raise InvalidEdit("Release approval or checksum mismatch")
    snap=directory/"snapshot.sqlite"
    with closing(connect(snap,readonly=True)) as con:
        authorize(con,manifest["project_id"],actor,"publish_release")
    counts=_verify_snapshot(snap,manifest["project_id"])
    if counts!=manifest["evidence"]:raise InvalidEdit("Approved snapshot no longer valid")
    target=root/"published"/release_id
    if target.exists():raise FileExistsError("Published release must not be overwritten")
    finished={"format":"filmwiki-published-release","release_id":release_id,
              "publisher":actor,"published_utc":_utc(),
              "approval_hash":_hash(approval),
              "snapshot_sha256":manifest["source_snapshot_sha256"],
              "http_deployed":False,"original_source_modified":False}
    _save_new(directory/"publication.json",finished)
    for file in directory.iterdir():
        os.chmod(file,0o400)
    os.chmod(directory,0o500)
    os.rename(directory,target)
    return finished

def inspect_release(root,release_id,where="published"):
    directory,manifest=_load_checked_stage(root,release_id,where)
    if _verify_snapshot(directory/"snapshot.sqlite",manifest["project_id"])!=manifest["evidence"]:
        raise RuntimeError("Release failed integrity recheck")
    return {"release_id":release_id,"stage":where,"project_id":manifest["project_id"],
            "sha256":manifest["source_snapshot_sha256"],
            "verified":True,"evidence":manifest["evidence"]}
