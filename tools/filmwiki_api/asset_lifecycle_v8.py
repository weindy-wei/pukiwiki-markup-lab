#!/usr/bin/env python3
"""Step35: synthetic-only asset lifecycle and append-only SHA256 audit.

No HTTP uploads, no malware scan, no native Wiki attachment re-enablement.
The Step34 GETs may serve only approved, non-deleted current revisions.
"""
from contextlib import closing
from datetime import datetime,timezone
import hashlib,json,os,re,sqlite3,tempfile,uuid
from pathlib import Path
from asset_catalog_v7 import (PROJECT,MAX_BYTES,KINDS,_connect,_payload_valid,
                              _db,_authorized)
GENESIS="0"*64
SCHEMA="""
CREATE TABLE IF NOT EXISTS asset_versions(
 asset_id TEXT NOT NULL, version INTEGER NOT NULL,
 file_name TEXT NOT NULL, sha256 TEXT NOT NULL,size INTEGER NOT NULL,
 media_type TEXT NOT NULL,
 review_state TEXT NOT NULL CHECK(review_state IN ('pending','approved','rejected')),
 created_utc TEXT NOT NULL,
 PRIMARY KEY(asset_id,version),
 FOREIGN KEY(asset_id) REFERENCES assets(asset_id)
);
CREATE TABLE IF NOT EXISTS asset_events(
 seq INTEGER PRIMARY KEY AUTOINCREMENT,
 asset_id TEXT NOT NULL,
 action TEXT NOT NULL, actor_id TEXT NOT NULL,
 old_json TEXT NOT NULL,new_json TEXT NOT NULL,
 time_utc TEXT NOT NULL,prev_hash TEXT NOT NULL,event_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS asset_events_asset ON asset_events(asset_id,seq);
CREATE TABLE IF NOT EXISTS asset_references(
 asset_id TEXT NOT NULL,project_id TEXT NOT NULL,
 record_id TEXT NOT NULL,relation_type TEXT NOT NULL,
 linked_by TEXT NOT NULL,created_utc TEXT NOT NULL,
 PRIMARY KEY(asset_id,project_id,record_id,relation_type),
 FOREIGN KEY(asset_id) REFERENCES assets(asset_id)
);
"""
ACTORS={"owner","reviewer","publisher"}
LINK_TYPES={"illustrates","reference","source"}
def utc():return datetime.now(timezone.utc).isoformat()
def valid_id(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f-]{36}",value) and str(uuid.UUID(value))==value
def asset_path(root,asset_id,version):
    return Path(root)/(("asset-"+asset_id+".bin") if version==1 else
                      ("asset-"+asset_id+"-v"+str(version)+".bin"))
def setup_lifecycle(root,data_root):
    from asset_catalog_v7 import setup_assets
    setup_assets(root,data_root)
    with closing(_connect(root)) as c:
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("BEGIN IMMEDIATE")
        try:
            names={r["name"] for r in c.execute("PRAGMA table_info(assets)")}
            for key,sql in [
                ("asset_state","ALTER TABLE assets ADD COLUMN asset_state TEXT NOT NULL DEFAULT 'approved'"),
                ("current_version","ALTER TABLE assets ADD COLUMN current_version INTEGER NOT NULL DEFAULT 1"),
                ("pending_version","ALTER TABLE assets ADD COLUMN pending_version INTEGER"),
                ("deleted_at","ALTER TABLE assets ADD COLUMN deleted_at TEXT")
            ]:
                if key not in names:c.execute(sql)
            for statement in SCHEMA.strip().split(";"):
                if statement.strip():c.execute(statement)
            for record in c.execute("SELECT asset_id,sha256,size,media_type,created_utc FROM assets").fetchall():
                exists=c.execute("SELECT 1 FROM asset_versions WHERE asset_id=? AND version=1",
                                (record["asset_id"],)).fetchone()
                if not exists:
                    c.execute("INSERT INTO asset_versions(asset_id,version,file_name,sha256,size,media_type,review_state,created_utc) VALUES(?,?,?,?,?,?,?,?)",
                        (record["asset_id"],1,"asset-"+record["asset_id"]+".bin",
                         record["sha256"],record["size"],record["media_type"],
                         "approved",record["created_utc"]))
            c.commit()
        except Exception:
            c.rollback();raise

def _actor(identity_db,actor_id,project_id,allowed_roles=ACTORS):
    if not re.fullmatch(r"user:[a-f0-9-]{36}",str(actor_id)) or project_id!=PROJECT:
        raise PermissionError("Invalid actor/project")
    with sqlite3.connect(Path(identity_db).resolve().as_uri()+"?mode=ro",uri=True) as c:
        roles={x[0] for x in c.execute(
            "SELECT m.role FROM memberships m JOIN users u ON m.user_id=u.user_id "
            "WHERE u.user_id=? AND u.active=1 AND m.project_id=?",
            (actor_id,PROJECT))}
    if not roles.intersection(allowed_roles):
        raise PermissionError("Not an authorized review operator")

def _event(c,asset_id,action,actor_id,old,new):
    previous=c.execute("SELECT event_hash FROM asset_events WHERE asset_id=? "
                       "ORDER BY seq DESC LIMIT 1",(asset_id,)).fetchone()
    prior=previous["event_hash"] if previous else GENESIS
    timestamp=utc()
    oldj=json.dumps(old,sort_keys=True,separators=(",",":"),ensure_ascii=False)
    newj=json.dumps(new,sort_keys=True,separators=(",",":"),ensure_ascii=False)
    values=[asset_id,action,actor_id,oldj,newj,timestamp,prior]
    digest=hashlib.sha256(json.dumps(values,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
    c.execute("INSERT INTO asset_events(asset_id,action,actor_id,old_json,new_json,"
              "time_utc,prev_hash,event_hash) VALUES(?,?,?,?,?,?,?,?)",(*values,digest))
    return digest

def _verify_file(root,record):
    name=record["file_name"]
    if not re.fullmatch(r"asset-[a-f0-9-]{36}(?:-v[1-9][0-9]*)?\.bin",name):
        raise RuntimeError("Invalid stored file name")
    path=Path(root)/name
    if path.is_symlink() or not path.is_file():raise RuntimeError("Missing or symlinked asset")
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        with os.fdopen(fd,"rb",closefd=False) as f:data=f.read(MAX_BYTES+1)
    finally:os.close(fd)
    if len(data)!=record["size"] or hashlib.sha256(data).hexdigest()!=record["sha256"]:
        raise RuntimeError("Corrupt asset bytes")
    return True

def _stage_bytes(root,source,mime,asset_id,version):
    root=Path(root).resolve(strict=True)
    source=Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Invalid staged source")
    source=source.resolve(strict=True)
    expected=root.parent.parent/"staging/synthetic-assets"
    if not source.is_relative_to(expected.resolve(strict=True)):
        raise ValueError("Only owner-staged synthetic files permitted")
    size=source.stat().st_size
    if not 0<size<=MAX_BYTES or mime not in KINDS:
        raise ValueError("Invalid input file size/MIME")
    data=source.read_bytes()
    if len(data)!=size or not _payload_valid(data,mime):
        raise ValueError("Invalid file signature/content")
    target=asset_path(root,asset_id,version)
    if target.exists():raise FileExistsError("Version already stored")
    fd,temp=tempfile.mkstemp(prefix=".pending-",dir=root)
    temp=Path(temp)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"wb") as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,target)
        return {"file_name":target.name,"sha256":hashlib.sha256(data).hexdigest(),
                "size":size,"media_type":mime}
    finally:temp.unlink(missing_ok=True)

def _validate_filename(name,mime):
    if type(name) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}",name) or \
        name.startswith(".") or mime not in KINDS or not name.lower().endswith(KINDS[mime][1]):
        raise ValueError("Invalid filename/MIME")

def stage_new(root,identity_db,source,project_id,actor_id,filename,mime,visibility):
    _actor(identity_db,actor_id,project_id,{"editor","owner"})
    _validate_filename(filename,mime)
    if visibility not in ("private","project"):raise ValueError("Invalid visibility")
    asset_id=str(uuid.uuid4())
    values=_stage_bytes(root,source,mime,asset_id,1)
    with closing(_connect(root)) as c:
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("BEGIN IMMEDIATE")
        try:
            timestamp=utc()
            c.execute("INSERT INTO assets(asset_id,project_id,owner_user_id,original_filename,"
                      "media_type,visibility,sha256,size,created_utc,asset_state,current_version,"
                      "pending_version,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,'pending',1,1,NULL)",
                 (asset_id,project_id,actor_id,filename,mime,visibility,
                  values["sha256"],values["size"],timestamp))
            c.execute("INSERT INTO asset_versions(asset_id,version,file_name,sha256,size,media_type,review_state,created_utc) VALUES(?,?,?,?,?,?,?,?)",
                     (asset_id,1,values["file_name"],values["sha256"],values["size"],
                      mime,"pending",timestamp))
            _event(c,asset_id,"stage-new",actor_id,{},{"version":1,"state":"pending",
                "sha256":values["sha256"],"visibility":visibility})
            c.commit()
        except Exception:
            c.rollback()
            asset_path(root,asset_id,1).unlink(missing_ok=True)
            raise
    return {"asset_id":asset_id,"state":"pending","version":1,"sha256":values["sha256"]}

def stage_revision(root,identity_db,source,asset_id,actor_id,mime):
    if not valid_id(asset_id):raise ValueError("Invalid asset ID")
    _actor(identity_db,actor_id,PROJECT,{"editor","owner"})
    with closing(_connect(root)) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            asset=c.execute("SELECT * FROM assets WHERE asset_id=?",(asset_id,)).fetchone()
            if asset is None or asset["deleted_at"] or asset["asset_state"]!="approved" or asset["pending_version"]:
                raise ValueError("Only active approved assets without a pending revision")
            if actor_id!=asset["owner_user_id"]:
                raise PermissionError("Only uploading member can submit a new revision")
            _validate_filename(asset["original_filename"],mime)
            latest=c.execute("SELECT COALESCE(MAX(version),0) AS n FROM asset_versions "
                             "WHERE asset_id=?",(asset_id,)).fetchone()["n"]
            version=latest+1
            data=_stage_bytes(root,source,mime,asset_id,version)
            try:
                c.execute("INSERT INTO asset_versions(asset_id,version,file_name,sha256,size,media_type,review_state,created_utc) VALUES(?,?,?,?,?,?,?,?)",
                          (asset_id,version,data["file_name"],data["sha256"],data["size"],
                           mime,"pending",utc()))
                c.execute("UPDATE assets SET pending_version=? WHERE asset_id=?",(version,asset_id))
                _event(c,asset_id,"stage-revision",actor_id,{"current_version":asset["current_version"]},
                       {"pending_version":version,"sha256":data["sha256"]})
                c.commit()
            except Exception:
                asset_path(root,asset_id,version).unlink(missing_ok=True)
                raise
        except Exception:
            if c.in_transaction:c.rollback()
            raise
    return {"asset_id":asset_id,"version":version,"state":"pending"}

def review(root,identity_db,asset_id,actor_id,decision):
    if not valid_id(asset_id) or decision not in ("approved","rejected"):raise ValueError("Invalid review")
    _actor(identity_db,actor_id,PROJECT)
    with closing(_connect(root)) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            asset=c.execute("SELECT * FROM assets WHERE asset_id=?",(asset_id,)).fetchone()
            if asset is None or asset["deleted_at"] is not None or not asset["pending_version"]:
                raise ValueError("No pending review")
            version=asset["pending_version"]
            v=c.execute("SELECT * FROM asset_versions WHERE asset_id=? AND version=?",
                        (asset_id,version)).fetchone()
            if v is None or v["review_state"]!="pending":raise ValueError("Review state conflict")
            if actor_id==asset["owner_user_id"]:
                raise PermissionError("Two-person rule: submitter cannot review own file")
            if decision=="approved":
                _verify_file(root,v)
                if "scan_state" in v.keys():
                    from asset_security_v9 import scan_is_current
                    if not scan_is_current(v):
                        raise PermissionError("Cannot approve without a fresh clean scan for this exact version")
            c.execute("UPDATE asset_versions SET review_state=? WHERE asset_id=? AND version=?",
                      (decision,asset_id,version))
            if decision=="approved":
                c.execute("UPDATE assets SET asset_state='approved',current_version=?,"
                          "sha256=?,size=?,media_type=?,pending_version=NULL WHERE asset_id=?",
                          (version,v["sha256"],v["size"],v["media_type"],asset_id))
            else:
                if asset["asset_state"]=="pending":
                    c.execute("UPDATE assets SET asset_state='rejected',pending_version=NULL "
                              "WHERE asset_id=?",(asset_id,))
                else:
                    c.execute("UPDATE assets SET pending_version=NULL WHERE asset_id=?",(asset_id,))
            _event(c,asset_id,"review-"+decision,actor_id,
                   {"pending_version":version,"state":asset["asset_state"]},
                   {"state":decision,"version":version})
            c.commit()
        except Exception:
            if c.in_transaction:c.rollback()
            raise
    return {"asset_id":asset_id,"version":version,"decision":decision}

def trash_restore(root,identity_db,asset_id,actor_id,restore=False):
    if not valid_id(asset_id):raise ValueError("Invalid asset")
    _actor(identity_db,actor_id,PROJECT,{"owner","publisher"})
    with closing(_connect(root)) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            row=c.execute("SELECT * FROM assets WHERE asset_id=?",(asset_id,)).fetchone()
            if row is None or row["asset_state"]!="approved" or row["pending_version"]:
                raise ValueError("Only approved assets without pending revision")
            if restore:
                if row["deleted_at"] is None:raise ValueError("Asset is not deleted")
                v=c.execute("SELECT * FROM asset_versions WHERE asset_id=? AND version=?",
                            (asset_id,row["current_version"])).fetchone()
                _verify_file(root,v)
                c.execute("UPDATE assets SET deleted_at=NULL WHERE asset_id=?",(asset_id,))
            else:
                if row["deleted_at"] is not None:raise ValueError("Already deleted")
                c.execute("UPDATE assets SET deleted_at=? WHERE asset_id=?",(utc(),asset_id))
            _event(c,asset_id,"restore" if restore else "soft-delete",actor_id,
                   {"deleted":row["deleted_at"] is not None},
                   {"deleted":not restore,"version":row["current_version"]})
            c.commit()
        except Exception:
            if c.in_transaction:c.rollback()
            raise
    return {"asset_id":asset_id,"deleted":not restore}

def link(root,identity_db,editor_db,asset_id,actor_id,record_id,kind,remove=False):
    if not valid_id(asset_id) or type(record_id) is not str or \
       not re.fullmatch(r"src_[a-zA-Z0-9_-]{1,95}",record_id) or kind not in LINK_TYPES:
        raise ValueError("Invalid asset record reference")
    _actor(identity_db,actor_id,PROJECT,{"editor","owner"})
    # Confirm the reference target really belongs to the synthetic editor.
    from editor_store import get_meta,record,MissingRecord
    if get_meta(editor_db)["project_id"]!=PROJECT:raise PermissionError("Editor project mismatch")
    exists=False
    for sheet_index in range(1,32):
        try:
            record(editor_db,sheet_index,record_id)
            exists=True;break
        except (MissingRecord,ValueError,IndexError):continue
    if not exists:raise ValueError("Referenced record not found")
    with closing(_connect(root)) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            asset=c.execute("SELECT * FROM assets WHERE asset_id=?",(asset_id,)).fetchone()
            if asset is None or asset["asset_state"]!="approved" or asset["deleted_at"]:
                raise PermissionError("Asset not active")
            if asset["visibility"]=="private" and asset["owner_user_id"]!=actor_id:
                raise PermissionError("Cannot reference someone else's private asset")
            if remove:
                rows=c.execute("DELETE FROM asset_references WHERE asset_id=? AND "
                               "project_id=? AND record_id=? AND relation_type=?",
                               (asset_id,PROJECT,record_id,kind)).rowcount
                if not rows:raise ValueError("Missing reference")
            else:
                c.execute("INSERT INTO asset_references VALUES(?,?,?,?,?,?)",
                          (asset_id,PROJECT,record_id,kind,actor_id,utc()))
            _event(c,asset_id,"unlink" if remove else "link",actor_id,
                   {"record_id":record_id,"relation":kind,"linked":remove},
                   {"record_id":record_id,"relation":kind,"linked":not remove})
            c.commit()
        except Exception:
            if c.in_transaction:c.rollback()
            raise
    return {"asset_id":asset_id,"record_id":record_id,"linked":not remove}

def audit(root,asset_id=None):
    with closing(_connect(root)) as c:
        if asset_id:
            if not valid_id(asset_id):raise ValueError("Invalid asset ID")
            ids=[asset_id]
        else:
            ids=[r["asset_id"] for r in c.execute("SELECT DISTINCT asset_id FROM asset_events")]
        total=0
        for aid in ids:
            prior=GENESIS
            for row in c.execute("SELECT * FROM asset_events WHERE asset_id=? ORDER BY seq",(aid,)):
                values=[row[k] for k in ("asset_id","action","actor_id","old_json",
                                         "new_json","time_utc","prev_hash")]
                expected=hashlib.sha256(json.dumps(values,separators=(",",":"),
                                         ensure_ascii=False).encode()).hexdigest()
                if expected!=row["event_hash"] or row["prev_hash"]!=prior:
                    raise RuntimeError("Asset audit chain corrupted")
                prior=row["event_hash"];total+=1
        return {"valid":True,"assets_with_events":len(ids),"events":total}


def history(root,asset_id):
    """Owner-local audit and version inventory; not exposed through HTTP."""
    if not valid_id(asset_id):raise ValueError("Invalid asset ID")
    with closing(_connect(root)) as c:
        row=c.execute("SELECT * FROM assets WHERE asset_id=?",(asset_id,)).fetchone()
        if row is None:raise ValueError("Unknown asset")
        v=[dict(x) for x in c.execute("SELECT * FROM asset_versions WHERE asset_id=? "
                                      "ORDER BY version",(asset_id,))]
        events=[dict(x) for x in c.execute("SELECT * FROM asset_events WHERE asset_id=? "
                                           "ORDER BY seq",(asset_id,))]
        refs=[dict(x) for x in c.execute("SELECT * FROM asset_references WHERE asset_id=?",
                                         (asset_id,))]
    checked=audit(root,asset_id)
    return {"asset":{"asset_id":row["asset_id"],"state":row["asset_state"],
                     "current_version":row["current_version"],
                     "pending_version":row["pending_version"],
                     "deleted":row["deleted_at"] is not None},
            "versions":v,"references":refs,"events":events,"audit":checked}
