#!/usr/bin/env python3
"""FilmWiki Step30: approved offline release -> read-only indexed registry.

A *local synthetic-only* deployment controller. All the live changes are
performed only by a privileged WSL operator, behind a maintenance stop of the
read API; this is NOT a zero-downtime switch or a remote HTTP deploy API.
"""
from contextlib import closing
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import sqlite3
import subprocess
import tempfile
import urllib.request
import uuid

from catalog_v2 import read_registry
from catalog_v3 import attach_validated_indices
from search_index import build_index,validate_index
from release_v5 import inspect_release

ALIAS="demo-published"  # explicit synthetic-only pilot
EDIT_PROJECT="demo-editor"
SOURCE_SHA="a"*64
SERVICE="filmwiki-readapi.service"
TIMER="filmwiki-index-reconcile.timer"
ORIGIN="http://127.0.0.1:8080"
URL="http://127.0.0.1:8091"
UUID=re.compile("^[a-f0-9-]{36}$")


def _sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as reader:
        for part in iter(lambda:reader.read(1024*1024),b""):h.update(part)
    return h.hexdigest()


def _atomic(path,data,mode=0o600):
    path=Path(path)
    if path.is_symlink() or not path.parent.is_dir():
        raise ValueError("Unsafe write path")
    fd,name=tempfile.mkstemp(prefix=".step30-",dir=path.parent)
    try:
        os.fchmod(fd,mode)
        # The hourly index worker runs as administrator; preserve ownership
        # after a privileged maintenance cutover.
        if os.geteuid()==0:
            owner=pwd.getpwnam("administrator")
            os.fchown(fd,owner.pw_uid,owner.pw_gid)
        with os.fdopen(fd,"wb") as file:
            file.write(data)
            file.flush();os.fsync(file.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)


def _json(path,obj):
    _atomic(path,(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True)+"\n").encode())


def _base(root):
    root=Path(root).resolve(strict=True)
    if not root.is_dir() or root.as_posix()!="/home/administrator/filmwiki-data":
        raise ValueError("Only the owner-local FilmWiki root can be deployed")
    home=root/"deployments"
    for suffix in ("versions","plans","transactions"):
        directory=home/suffix
        directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        if directory.is_symlink() or not directory.resolve().is_relative_to(root):
            raise ValueError("Unsafe private deployment directory")
    return root,home


def _valid_release_id(release_id):
    if type(release_id) is not str or not UUID.fullmatch(release_id):
        raise ValueError("Release must be UUID4")
    value=uuid.UUID(release_id)
    if str(value)!=release_id or value.version!=4:
        raise ValueError("Release must be UUID4")


def _register(root,alias,release_path):
    # Keep two original allowlisted projects untouched, including ordering.
    reg=root/"private/projects.json"
    doc=json.loads(reg.read_text(encoding="utf-8"))
    if doc.get("version")!=1 or not isinstance(doc.get("projects"),list):
        raise ValueError("Unrecognized registry")
    if alias!=ALIAS:
        raise ValueError("Step30 permits only the synthetic demo alias")
    previous=[p for p in doc["projects"] if p["id"]==alias]
    if len(previous)>1:raise ValueError("Duplicate alias")
    other=[p for p in doc["projects"] if p["id"]!=alias]
    if [p["id"] for p in other]!=["demo-shortfilm","demo-longshots"]:
        raise ValueError("Unexpected existing projects; fail closed")
    if not all(p["id"] in ("demo-shortfilm","demo-longshots") for p in other):
        raise ValueError("Unexpected registered projects")
    value={"id":alias,"title":"已发布合成分镜 · "+release_path.stem[:8],
           "db":str(release_path.relative_to(root)), "source_sha":SOURCE_SHA}
    doc["projects"]=[*other,value]
    return doc


def plan(root,release_id,alias=ALIAS):
    root,home=_base(root);_valid_release_id(release_id)
    if alias!=ALIAS:raise ValueError("Only the demo alias is permitted")
    info=inspect_release(root/"releases",release_id)
    release_dir=root/"releases/published"/release_id
    release_manifest=json.loads((release_dir/"manifest.json").read_text())
    approval=json.loads((release_dir/"approval.json").read_text())
    publication=json.loads((release_dir/"publication.json").read_text())
    from release_v5 import _hash
    if (publication.get("release_id")!=release_id or
        publication.get("http_deployed") is not False or
        publication.get("snapshot_sha256")!=info["sha256"] or
        publication.get("approval_hash")!=_hash(approval) or
        approval.get("manifest_hash")!=_hash(release_manifest) or
        approval.get("snapshot_sha256")!=info["sha256"]):
        raise ValueError("Publication chain is invalid or incomplete")
    if info["project_id"]!=EDIT_PROJECT:
        raise ValueError("Not a reviewed synthetic editor project")
    released=root/"releases/published"/release_id/"snapshot.sqlite"
    expected=info["sha256"]
    assert _sha(released)==expected
    destination=home/"versions"/(release_id+".sqlite")
    if destination.exists():
        if destination.is_symlink() or _sha(destination)!=expected:
            raise ValueError("Version file already exists with a different checksum")
    else:
        # The published release is a stable, sealed SQLite. Copy to a dedicated
        # immutable deployment path, never open SQLite on the NAS.
        fd,temp_name=tempfile.mkstemp(prefix=".stage-release-",suffix=".sqlite",
                                      dir=destination.parent)
        os.close(fd)
        temp=Path(temp_name)
        try:
            shutil.copyfile(released,temp)
            if _sha(temp)!=expected:raise ValueError("Copied version hash mismatch")
            os.chmod(temp,0o400)
            os.replace(temp,destination)
        finally:temp.unlink(missing_ok=True)
    with closing(sqlite3.connect(destination.resolve().as_uri()+"?mode=ro",uri=True)) as con:
        if con.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise ValueError("SQLite version integrity mismatch")
        x=con.execute("SELECT source_sha FROM editor_meta").fetchone()
        if not x or x[0]!=SOURCE_SHA:raise ValueError("Not the expected source fingerprint")
    new_registry=_register(root,alias,destination)
    plan_file=home/"plans"/(release_id+".json")
    candidate_index=home/"plans"/(release_id+"-"+alias+".search.sqlite")
    if not candidate_index.exists():
        build_index(destination,SOURCE_SHA,alias,candidate_index,root)
    validate_index(destination,SOURCE_SHA,alias,candidate_index)
    # Fail before touching live endpoints unless entire new project registry
    # and every FTS5 sidecar are valid together.
    original=root/"private/projects.json"
    tmp_registry=home/"plans"/(release_id+".registry.json")
    _json(tmp_registry,new_registry)
    read_registry(tmp_registry,root)
    all_projects=read_registry(tmp_registry,root)
    staged_index=home/"plans"/(release_id+"-"+alias+".search.sqlite")
    for entry in all_projects.values():
        index=(staged_index if entry["id"]==alias
               else root/"indexes"/(entry["id"]+".search.sqlite"))
        validate_index(entry["db"],entry["source_sha"],entry["id"],index)
    document={"release_id":release_id,"alias":alias,"source_sha":SOURCE_SHA,
              "snapshot_sha256":expected,
              "version_relative":str(destination.relative_to(root)),
              "index_sha256":_sha(candidate_index),
              "registry_sha256":_sha(tmp_registry),
              "current_registry_sha256":_sha(original),
              "prepared_utc":datetime.now(timezone.utc).isoformat(),
              "project_count":len(new_registry["projects"])}
    _json(plan_file,document)
    return document


def _command(*args):
    proc=subprocess.run(args,timeout=50,text=True,capture_output=True)
    if proc.returncode:
        raise RuntimeError("System service command failed: "+args[-1])
    return proc.stdout.strip()


def _verify_live(root,expected_ids):
    token=(root/"private/api-token.txt").read_text().strip()
    def get(path):
        req=urllib.request.Request(URL+path,
              headers={"Origin":ORIGIN,"Authorization":"Bearer "+token})
        with urllib.request.urlopen(req,timeout=8) as response:
            if response.status!=200:raise RuntimeError("Bad read API status")
            return json.load(response)
    projects=get("/v2/projects")["projects"]
    actual=[p["id"] for p in projects]
    if actual!=expected_ids:raise RuntimeError("Wrong API project list")
    for project in projects:
        pid=project["id"]
        m=get("/v2/projects/"+pid+"/manifest")
        if m["project"]["id"]!=pid:raise RuntimeError("Wrong manifest identity")
        if m["sheets"]:
            si=m["sheets"][0]["sheet_index"]
            row=get(f"/v2/projects/{pid}/records?sheet={si}&limit=1")
            if row["project_id"]!=pid:raise RuntimeError("Wrong page project")
    if ALIAS in expected_ids:
        from urllib.parse import urlencode
        page=get(f"/v2/projects/{ALIAS}/records?"+urlencode({"sheet":6,"q":"合成街景","limit":10}))
        if len(page["records"])==0:raise RuntimeError("New project's FTS failed")
        detail=get(f"/v2/projects/{ALIAS}/record?sheet=6&id=src_demo_6009")
        parent=(detail["record"].get("parent_link") or {}).get("parent_record_id")
        if parent!="src_demo_6008":
            raise RuntimeError("Published parent override missing: "+str(parent))
    return actual


def _with_maintenance(root,operation):
    # A root-only local operator operation; suspend hourly check and acquire
    # exactly the same lock as index_reconciler before editing the sidecar.
    if os.geteuid()!=0:raise PermissionError("Deployment cutover requires local root")
    timer_active=subprocess.run(["/usr/bin/systemctl","is-active",TIMER],
         capture_output=True,text=True).stdout.strip()=="active"
    if timer_active:_command("/usr/bin/systemctl","stop",TIMER)
    lock_path=root/"indexes/.index-reconciler.lock"
    fd=os.open(lock_path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        return operation()
    finally:
        fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)
        if timer_active:_command("/usr/bin/systemctl","start",TIMER)


def _restore(root,registry_data,index_data):
    _atomic(root/"private/projects.json",registry_data)
    idx=root/"indexes"/(ALIAS+".search.sqlite")
    if index_data is None:
        idx.unlink(missing_ok=True)
    else:
        _atomic(idx,index_data)


def activate(root,release_id):
    root,home=_base(root);_valid_release_id(release_id)
    prepared=plan(root,release_id)
    plan_registry=home/"plans"/(release_id+".registry.json")
    candidate=home/"plans"/(release_id+"-"+ALIAS+".search.sqlite")
    if (_sha(plan_registry)!=prepared["registry_sha256"] or
        _sha(candidate)!=prepared["index_sha256"]):
        raise RuntimeError("Stage altered before activation")
    new_registry=plan_registry.read_bytes()
    old_registry=(root/"private/projects.json").read_bytes()
    old_index_file=root/"indexes"/(ALIAS+".search.sqlite")
    old_index=old_index_file.read_bytes() if old_index_file.exists() else None
    new_ids=[p["id"] for p in json.loads(new_registry)["projects"]]
    old_ids=[p["id"] for p in json.loads(old_registry)["projects"]]
    transaction=str(uuid.uuid4())
    txn=home/"transactions"/transaction
    txn.mkdir(mode=0o700)
    _atomic(txn/"registry.before.json",old_registry)
    if old_index is not None:_atomic(txn/"index.before.sqlite",old_index)
    saved={"transaction":transaction,"release_id":release_id,
           "registry_before_sha256":_sha(txn/"registry.before.json"),
           "registry_after_sha256":hashlib.sha256(new_registry).hexdigest(),
           "prior_index_existed":old_index is not None,
           "state":"prepared","old_projects":old_ids,"new_projects":new_ids}
    _json(txn/"transaction.json",saved)
    def execute():
        _command("/usr/bin/systemctl","stop",SERVICE)
        try:
            _atomic(old_index_file,candidate.read_bytes())
            _atomic(root/"private/projects.json",new_registry)
            _command("/usr/bin/systemctl","start",SERVICE)
            _verify_live(root,new_ids)
            saved["state"]="active";saved["confirmed_utc"]=datetime.now(timezone.utc).isoformat()
            _json(txn/"transaction.json",saved)
            return saved
        except Exception:
            _command("/usr/bin/systemctl","stop",SERVICE)
            _restore(root,old_registry,old_index)
            _command("/usr/bin/systemctl","start",SERVICE)
            _verify_live(root,old_ids)
            saved["state"]="rolled_back_after_failure"
            _json(txn/"transaction.json",saved)
            raise
    return _with_maintenance(root,execute)


def recover(root,transaction):
    """Restore pre-cutover state if the controller crashed before confirmation.

    Only transactions still marked PREPARED are eligible. This is a manual,
    local root recovery command; no guessing based on HTTP success.
    """
    root,home=_base(root)
    if type(transaction) is not str or not UUID.fullmatch(transaction) or \
       str(uuid.UUID(transaction))!=transaction:
        raise ValueError("Invalid transaction")
    txn=home/"transactions"/transaction
    if txn.is_symlink() or not txn.is_dir():raise ValueError("Unknown transaction")
    saved=json.loads((txn/"transaction.json").read_text())
    if saved.get("transaction")!=transaction or saved.get("state")!="prepared":
        raise ValueError("Only interrupted transactions can be recovered")
    previous=(txn/"registry.before.json").read_bytes()
    if hashlib.sha256(previous).hexdigest()!=saved["registry_before_sha256"]:
        raise RuntimeError("Rollback journal was modified")
    current_sha=_sha(root/"private/projects.json")
    if current_sha not in (saved["registry_before_sha256"],
                          saved["registry_after_sha256"]):
        raise RuntimeError("Unknown registry state; manual inspection required")
    idx=txn/"index.before.sqlite"
    prior=idx.read_bytes() if saved["prior_index_existed"] else None
    def execute():
        _command("/usr/bin/systemctl","stop",SERVICE)
        _restore(root,previous,prior)
        _command("/usr/bin/systemctl","start",SERVICE)
        _verify_live(root,saved["old_projects"])
        saved["state"]="recovered_after_interruption"
        saved["recovered_utc"]=datetime.now(timezone.utc).isoformat()
        _json(txn/"transaction.json",saved)
        return saved
    return _with_maintenance(root,execute)


def rollback(root,transaction):
    root,home=_base(root)
    if type(transaction) is not str or not UUID.fullmatch(transaction) or \
       str(uuid.UUID(transaction))!=transaction:raise ValueError("Invalid transaction ID")
    txn=home/"transactions"/transaction
    if txn.is_symlink() or not txn.is_dir():raise ValueError("Unknown transaction")
    saved=json.loads((txn/"transaction.json").read_text())
    if saved["transaction"]!=transaction or saved["state"]!="active":
        raise ValueError("Transaction is not active")
    now=(root/"private/projects.json").read_bytes()
    if hashlib.sha256(now).hexdigest()!=saved["registry_after_sha256"]:
        raise RuntimeError("Registry changed since deployment; refusing blind rollback")
    previous=(txn/"registry.before.json").read_bytes()
    if hashlib.sha256(previous).hexdigest()!=saved["registry_before_sha256"]:
        raise RuntimeError("Rollback journal checksum changed")
    idx=txn/"index.before.sqlite"
    previous_idx=idx.read_bytes() if saved["prior_index_existed"] else None
    def execute():
        _command("/usr/bin/systemctl","stop",SERVICE)
        _restore(root,previous,previous_idx)
        _command("/usr/bin/systemctl","start",SERVICE)
        _verify_live(root,saved["old_projects"])
        saved["state"]="rolled_back";saved["rolled_back_utc"]=datetime.now(timezone.utc).isoformat()
        _json(txn/"transaction.json",saved)
        return saved
    return _with_maintenance(root,execute)
