#!/usr/bin/env python3
"""Fail-closed ClamAV plus MIME scanner for local synthetic file review."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib,shutil,subprocess,time
from pathlib import Path
from asset_lifecycle_v8 import (_connect,_verify_file,valid_id,utc,_event)

MAX_AGE_SECONDS=24*3600

def setup_security(root,data_root):
    from asset_lifecycle_v8 import setup_lifecycle
    setup_lifecycle(root,data_root)
    with closing(_connect(root)) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            cols={r["name"] for r in c.execute("PRAGMA table_info(asset_versions)")}
            for key,ddl in (
              ("scan_state","ALTER TABLE asset_versions ADD COLUMN scan_state TEXT NOT NULL DEFAULT 'unscanned'"),
              ("scan_digest","ALTER TABLE asset_versions ADD COLUMN scan_digest TEXT"),
              ("scan_time","ALTER TABLE asset_versions ADD COLUMN scan_time TEXT"),
              ("scan_engine","ALTER TABLE asset_versions ADD COLUMN scan_engine TEXT"),
              ("scan_message","ALTER TABLE asset_versions ADD COLUMN scan_message TEXT")
            ):
                if key not in cols:c.execute(ddl)
            c.execute("CREATE TABLE IF NOT EXISTS asset_security_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
            if c.execute("SELECT 1 FROM asset_security_meta WHERE key='migration'").fetchone() is None:
                # One-time grandfathering for pre-existing SYNTHETIC approved items.
                c.execute("UPDATE asset_versions SET scan_state='legacy_unverified' "
                          "WHERE review_state='approved' AND scan_state='unscanned'")
                c.execute("INSERT INTO asset_security_meta VALUES('migration','synthetic_legacy_2026')")
            c.commit()
        except Exception:
            c.rollback();raise

def _type_check(path,expected):
    exe=shutil.which("file")
    if not exe:return ("error","mime_checker_unavailable")
    try:
        result=subprocess.run([exe,"--mime-type","--brief","--",str(path)],
            capture_output=True,text=True,timeout=6,env={"PATH":"/usr/bin:/bin","LC_ALL":"C"})
    except (OSError,subprocess.TimeoutExpired):
        return ("error","mime_checker_failed")
    actual=result.stdout.strip()
    if result.returncode!=0 or len(actual)>100:return ("error","mime_checker_failed")
    if actual!=expected:return ("infected","mime_mismatch")
    return ("clean","mime_ok")

def _engine(path):
    exe=shutil.which("clamscan")
    if not exe:return ("error","clamscan_unavailable","")
    # Signatures must exist and be recently updated; a valid executable alone
    # is not a valid malware scanner.
    root=Path("/var/lib/clamav")
    daily=[root/"daily.cvd",root/"daily.cld"]
    current=[x for x in daily if x.is_file()]
    if not current or min(time.time()-x.stat().st_mtime for x in current)>72*3600:
        return ("error","signature_database_missing_or_stale","")
    try:
        result=subprocess.run([exe,"--no-summary","--stdout","--infected",
                  "--max-filesize=20M","--max-scansize=50M","--max-recursion=8",
                  "--alert-exceeds-max=yes","--",str(path)],
                  capture_output=True,text=True,timeout=60,
                  env={"PATH":"/usr/bin:/bin","LC_ALL":"C"})
    except subprocess.TimeoutExpired:return ("error","clamscan_timeout","")
    except OSError:return ("error","clamscan_exec_failed","")
    if result.returncode==0:return ("clean","clamav_clean","clamav")
    if result.returncode==1:return ("infected","clamav_detected","clamav")
    return ("error","clamav_engine_failure","clamav")

def scan_pending(root,identity_db,asset_id,actor_id):
    from asset_lifecycle_v8 import _actor
    if not valid_id(asset_id):raise ValueError("Invalid asset ID")
    _actor(identity_db,actor_id,"demo-editor",{"owner","editor","reviewer","publisher"})
    with closing(_connect(root)) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            asset=c.execute("SELECT * FROM assets WHERE asset_id=?",(asset_id,)).fetchone()
            if asset is None or asset["deleted_at"] or not asset["pending_version"]:
                raise ValueError("No pending asset")
            version=asset["pending_version"]
            rec=c.execute("SELECT * FROM asset_versions WHERE asset_id=? AND version=?",
                         (asset_id,version)).fetchone()
            if rec is None or rec["review_state"]!="pending":raise ValueError("Not pending")
            _verify_file(root,rec)
            path=Path(root)/rec["file_name"]
            t,msg=_type_check(path,rec["media_type"])
            state,detail,engine=_engine(path) if t=="clean" else (t,msg,"file")
            sha=hashlib.sha256(path.read_bytes()).hexdigest()
            if sha!=rec["sha256"]:state,detail,engine="error","file_changed","integrity"
            c.execute("UPDATE asset_versions SET scan_state=?,scan_digest=?,scan_time=?,"
                      "scan_engine=?,scan_message=? WHERE asset_id=? AND version=?",
                      (state,sha,utc(),engine,detail,asset_id,version))
            _event(c,asset_id,"scan-"+state,actor_id,
                 {"version":version,"scan_state":rec["scan_state"]},
                 {"version":version,"state":state,"sha256":sha,"engine":engine,"reason":detail})
            c.commit()
            return {"asset_id":asset_id,"version":version,"scan_state":state,
                    "engine":engine,"message":detail}
        except Exception:
            if c.in_transaction:c.rollback()
            raise

def scan_is_current(rec):
    if rec["scan_state"]!="clean" or rec["scan_digest"]!=rec["sha256"]:
        return False
    try:
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(rec["scan_time"])).total_seconds()
        return -30<=age<=MAX_AGE_SECONDS
    except (TypeError,ValueError):return False
