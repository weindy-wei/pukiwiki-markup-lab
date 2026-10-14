#!/usr/bin/env python3
"""Step37 owner-local synthetic scan queue, reviewer inbox, inert text preview.

No web file uploads, no arbitrary MIME inline rendering, no UI skin changes.
"""
from contextlib import closing
from datetime import datetime,timezone
from html import escape
import hashlib,json,re,sqlite3,time,uuid
from pathlib import Path
from asset_lifecycle_v8 import (_actor,_connect,_verify_file,valid_id,audit,review)
from asset_security_v9 import setup_security,scan_pending,scan_is_current
from asset_catalog_v7 import PROJECT

SQL="""
CREATE TABLE IF NOT EXISTS asset_scan_jobs(
 job_id TEXT PRIMARY KEY,
 asset_id TEXT NOT NULL,
 version INTEGER NOT NULL,
 expected_sha256 TEXT NOT NULL,
 requested_by TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','done','blocked','failed')),
 attempts INTEGER NOT NULL DEFAULT 0,
 next_run INTEGER NOT NULL,
 leased_at INTEGER,
 lease_id TEXT,
 scan_result TEXT,
 queued_utc TEXT NOT NULL,
 finished_utc TEXT
);
CREATE INDEX IF NOT EXISTS scan_jobs_pick ON asset_scan_jobs(status,next_run,queued_utc);
CREATE UNIQUE INDEX IF NOT EXISTS scan_jobs_single_open
ON asset_scan_jobs(asset_id,version) WHERE status IN ('queued','running');
"""
LEASE_TIMEOUT=180
RETRIES=3
MAX_PREVIEW=4096

def setup_queue(root,data_root):
    setup_security(root,data_root)
    with closing(_connect(root)) as c:
        c.executescript(SQL)
        c.commit()

def _now():return datetime.now(timezone.utc).isoformat()

def enqueue(root,identity_db,asset_id,actor_id):
    if not valid_id(asset_id):raise ValueError("Invalid asset ID")
    _actor(identity_db,actor_id,PROJECT,{"owner","editor","reviewer","publisher"})
    now=int(time.time())
    with closing(_connect(root)) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            asset=c.execute("SELECT * FROM assets WHERE asset_id=? AND project_id=?",
                            (asset_id,PROJECT)).fetchone()
            if asset is None or asset["deleted_at"] or asset["pending_version"] is None:
                raise ValueError("No pending revision")
            version=asset["pending_version"]
            if actor_id!=asset["owner_user_id"]:
                _actor(identity_db,actor_id,PROJECT,{"owner","reviewer","publisher"})
            rec=c.execute("SELECT * FROM asset_versions WHERE asset_id=? AND version=?",
                          (asset_id,version)).fetchone()
            if rec is None or rec["review_state"]!="pending":raise ValueError("No pending revision")
            _verify_file(root,rec)
            openjob=c.execute("SELECT job_id,status FROM asset_scan_jobs WHERE asset_id=? "
                 "AND version=? AND status IN ('queued','running')",(asset_id,version)).fetchone()
            if openjob:
                c.execute("COMMIT")
                return {"job_id":openjob["job_id"],"status":openjob["status"],"deduplicated":True}
            if scan_is_current(rec):
                c.execute("COMMIT")
                return {"job_id":None,"status":"already_clean","deduplicated":True}
            job_id=str(uuid.uuid4())
            c.execute("INSERT INTO asset_scan_jobs(job_id,asset_id,version,expected_sha256,"
                      "requested_by,status,attempts,next_run,queued_utc) "
                      "VALUES(?,?,?,?,?,'queued',0,?,?)",
                      (job_id,asset_id,version,rec["sha256"],actor_id,now,_now()))
            c.execute("COMMIT")
            return {"job_id":job_id,"status":"queued","deduplicated":False}
        except Exception:
            if c.in_transaction:c.execute("ROLLBACK")
            raise

def claim_one(root,now=None):
    now=int(time.time()) if now is None else int(now)
    with closing(_connect(root)) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            # Reclaim only jobs with a long-expired lease. The timer launches
            # only one systemd service; concurrent tests are atomic.
            c.execute("UPDATE asset_scan_jobs SET status='queued',lease_id=NULL,leased_at=NULL,"
                      "next_run=? WHERE status='running' AND leased_at<?",
                      (now,now-LEASE_TIMEOUT))
            row=c.execute("SELECT * FROM asset_scan_jobs WHERE status='queued' AND next_run<=? "
                          "ORDER BY next_run,queued_utc LIMIT 1",(now,)).fetchone()
            if row is None:
                c.execute("COMMIT");return None
            lease=str(uuid.uuid4())
            c.execute("UPDATE asset_scan_jobs SET status='running',lease_id=?,leased_at=?,"
                      "attempts=attempts+1 WHERE job_id=? AND status='queued'",
                      (lease,now,row["job_id"]))
            c.execute("COMMIT")
            return {**dict(row),"lease_id":lease,"attempts":row["attempts"]+1}
        except Exception:
            if c.in_transaction:c.execute("ROLLBACK")
            raise

def _finish(root,job,status,outcome,now=None):
    now=int(time.time()) if now is None else int(now)
    with closing(_connect(root)) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            row=c.execute("SELECT status,lease_id FROM asset_scan_jobs WHERE job_id=?",
                          (job["job_id"],)).fetchone()
            if not row or row["status"]!="running" or row["lease_id"]!=job["lease_id"]:
                raise RuntimeError("Stale scan lease result discarded")
            if status=="retry" and job["attempts"]<RETRIES:
                c.execute("UPDATE asset_scan_jobs SET status='queued',next_run=?,leased_at=NULL,"
                          "lease_id=NULL,scan_result=? WHERE job_id=?",
                          (now+(30*(4**(job["attempts"]-1))),outcome,job["job_id"]))
                final="queued"
            else:
                final="failed" if status=="retry" else status
                c.execute("UPDATE asset_scan_jobs SET status=?,finished_utc=?,leased_at=NULL,"
                          "lease_id=NULL,scan_result=? WHERE job_id=?",
                          (final,_now(),outcome,job["job_id"]))
            c.execute("COMMIT")
            return {"job_id":job["job_id"],"status":final,"reason":outcome}
        except Exception:
            if c.in_transaction:c.execute("ROLLBACK")
            raise

def run_once(root,identity_db):
    job=claim_one(root)
    if job is None:return None
    try:
        with closing(_connect(root)) as c:
            a=c.execute("SELECT * FROM assets WHERE asset_id=?",(job["asset_id"],)).fetchone()
            v=c.execute("SELECT * FROM asset_versions WHERE asset_id=? AND version=?",
                        (job["asset_id"],job["version"])).fetchone()
            if not a or not v or a["pending_version"]!=job["version"] or \
               a["deleted_at"] is not None or v["sha256"]!=job["expected_sha256"] or \
               v["review_state"]!="pending":
                return _finish(root,job,"blocked","stale_or_changed_revision")
        verdict=scan_pending(root,identity_db,job["asset_id"],job["requested_by"])
        if verdict["scan_state"]=="clean":return _finish(root,job,"done","clean")
        if verdict["scan_state"]=="infected":return _finish(root,job,"blocked","infected_or_type_mismatch")
        return _finish(root,job,"retry","scanner_unavailable_or_failed")
    except (PermissionError,ValueError):
        return _finish(root,job,"blocked","requester_revoked_or_revision_invalid")
    except (sqlite3.Error,OSError,RuntimeError):
        return _finish(root,job,"retry","scanner_or_storage_error")

def scan_jobs(root,identity,limit=40):
    roles={r["role"] for r in identity["memberships"] if r["project_id"]==PROJECT}
    if not roles:raise PermissionError("No project role")
    privileged=bool(roles & {"owner","reviewer","publisher"})
    with closing(_connect(root)) as c:
        rows=c.execute("SELECT j.job_id,j.asset_id,j.version,j.status,j.attempts,"
                "j.queued_utc,j.finished_utc,j.requested_by "
                "FROM asset_scan_jobs j JOIN assets a ON a.asset_id=j.asset_id "
                "WHERE a.project_id=? ORDER BY j.queued_utc DESC LIMIT ?",(PROJECT,limit)).fetchall()
        return [dict(r) for r in rows if privileged or r["requested_by"]==identity["user_id"]]

def review_inbox(root,identity):
    roles={r["role"] for r in identity["memberships"] if r["project_id"]==PROJECT}
    if not (roles & {"owner","reviewer","publisher"}):
        raise PermissionError("Reviewer role required")
    with closing(_connect(root)) as c:
        rows=c.execute("SELECT a.asset_id,a.original_filename,a.owner_user_id,a.visibility,"
             "a.pending_version AS version,v.scan_state,v.scan_time,v.scan_digest,v.sha256 "
             "FROM assets a JOIN asset_versions v ON a.asset_id=v.asset_id "
             "AND a.pending_version=v.version WHERE a.project_id=? AND a.deleted_at IS NULL "
             "AND v.review_state='pending' ORDER BY a.created_utc,a.asset_id",(PROJECT,)).fetchall()
        result=[]
        for row in rows:
            item=dict(row)
            item["can_approve"]=item["owner_user_id"]!=identity["user_id"] and scan_is_current(row)
            item["scan_is_current"]=scan_is_current(row)
            result.append(item)
        return result

def decide(root,identity_db,identity,asset_id,decision):
    if decision not in ("approved","rejected"):raise ValueError("Invalid decision")
    # Step36 review() atomically re-verifies real membership, scan and
    # uploader/reviewer account inequality. Queue is informational only.
    return review(root,identity_db,asset_id,identity["user_id"],decision)

def inert_text_preview(root,identity,asset_id):
    if not valid_id(asset_id):return None
    roles={r["role"] for r in identity["memberships"] if r["project_id"]==PROJECT}
    if not roles:return None
    with closing(_connect(root)) as c:
        a=c.execute("SELECT * FROM assets WHERE asset_id=? AND project_id=?",
                    (asset_id,PROJECT)).fetchone()
        if not a or a["deleted_at"] is not None:return None
        pending=a["pending_version"]
        version=pending if pending else a["current_version"]
        v=c.execute("SELECT * FROM asset_versions WHERE asset_id=? AND version=?",
                    (asset_id,version)).fetchone()
        if not v or v["media_type"]!="text/plain" or not scan_is_current(v):
            return {"asset_id":asset_id,"available":False,"reason":"no_safe_text_preview"}
        if a["owner_user_id"]!=identity["user_id"] and not (roles & {"owner","reviewer","publisher"}):
            return None
        _verify_file(root,v)
        # Only text bytes enter the preview renderer. Executable HTML, PDF,
        # SVG and media objects are never rendered or embedded.
        raw=(Path(root)/v["file_name"]).read_bytes()
        try:decoded=raw[:MAX_PREVIEW].decode("utf8")
        except UnicodeError:return {"asset_id":asset_id,"available":False,"reason":"invalid_utf8"}
        safe="".join(ch if ch in "\n\t" or ord(ch)>=32 else " " for ch in decoded)
        return {"asset_id":asset_id,"available":True,"preview_format":"escaped_plain_text",
                "version":version,"truncated":len(raw)>MAX_PREVIEW,
                "text":escape(safe,quote=True)}
