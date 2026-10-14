#!/usr/bin/env python3
"""FilmWiki Step34 owner-local synthetic asset catalog.

Native PukiWiki upload/ref remains quarantined. Each asset is authorized at
download time; bytes never reside in the Nginx document root or SMB/NAS.
"""
from contextlib import closing
from datetime import datetime,timezone
import hashlib,os,re,secrets,sqlite3,tempfile,uuid
from pathlib import Path

PROJECT="demo-editor"
MAX_BYTES=10*1024*1024
KINDS={
 "image/png":(b"\x89PNG\r\n\x1a\n",".png"),
 "image/jpeg":(b"\xff\xd8\xff",".jpg"),
 "image/webp":(b"RIFF",".webp"),
 "application/pdf":(b"%PDF-",".pdf"),
 "video/mp4":(None,".mp4"),
 "text/plain":(None,".txt")
}
SCHEMA="""
CREATE TABLE IF NOT EXISTS assets(
  asset_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  original_filename TEXT NOT NULL,
  media_type TEXT NOT NULL,
  visibility TEXT NOT NULL CHECK(visibility IN ('project','private')),
  sha256 TEXT NOT NULL,size INTEGER NOT NULL,
  created_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS asset_project ON assets(project_id,visibility);
"""

def _db(root):
    root=Path(root).resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Unsafe assets root")
    target=root/"catalog.sqlite"
    if target.is_symlink():raise ValueError("Unsafe asset catalog")
    return target

def _connect(root):
    db=_db(root)
    conn=sqlite3.connect(db,timeout=10)
    conn.row_factory=sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    return conn

def setup_assets(root,data_root):
    root=Path(root)
    data=Path(data_root).resolve(strict=True)
    if root.is_symlink() or not root.is_absolute() or \
        not root.parent.resolve().is_relative_to(data/"private"):
        raise ValueError("Assets must be inside local private data tree")
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    with closing(_connect(root)) as con:
        con.executescript(SCHEMA)
        con.commit()
    os.chmod(root,0o700)
    os.chmod(_db(root),0o600)

def _payload_valid(payload,mime):
    if mime=="image/webp":return payload.startswith(b"RIFF") and payload[8:12]==b"WEBP"
    if mime=="video/mp4":return len(payload)>12 and payload[4:8]==b"ftyp"
    if mime=="text/plain":
        try:payload.decode("utf8");return b"\x00" not in payload
        except UnicodeError:return False
    magic=KINDS[mime][0]
    return payload.startswith(magic)

def import_asset(root,identity_db,source,project_id,owner_user_id,filename,mime,visibility):
    if project_id!=PROJECT or visibility not in ("private","project") or mime not in KINDS:
        raise ValueError("Unsupported project, visibility or MIME")
    if type(owner_user_id) is not str or not re.fullmatch(r"user:[a-f0-9-]{36}",owner_user_id):
        raise ValueError("Invalid owner ID")
    if type(filename) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}",filename) \
        or filename.startswith("."):
        raise ValueError("Use a safe ASCII filename")
    if not filename.lower().endswith(KINDS[mime][1]):
        raise ValueError("MIME does not match declared filename extension")
    source=Path(source).resolve(strict=True)
    if not source.is_file() or source.is_symlink() or not source.is_relative_to(
         Path(root).resolve(strict=True).parent.parent/"staging/synthetic-assets"):
        raise ValueError("Only staged synthetic assets may be imported")
    if not 0<source.stat().st_size<=MAX_BYTES:
        raise ValueError("Invalid payload length")
    data=source.read_bytes()
    if len(data)>MAX_BYTES or not _payload_valid(data,mime):
        raise ValueError("Invalid file signature/payload")
    with sqlite3.connect(identity_db.resolve().as_uri()+"?mode=ro",uri=True) as con:
        member=con.execute("SELECT 1 FROM users u JOIN memberships m ON u.user_id=m.user_id "
                          "WHERE u.user_id=? AND u.active=1 AND m.project_id=? LIMIT 1",
                          (owner_user_id,project_id)).fetchone()
    if member is None:raise PermissionError("Owner must be an active project member")
    asset_id=str(uuid.uuid4())
    path=Path(root)/("asset-"+asset_id+".bin")
    digest=hashlib.sha256(data).hexdigest()
    fd,name=tempfile.mkstemp(prefix=".upload-",dir=root)
    temp=Path(name)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"wb") as file:
            file.write(data);file.flush();os.fsync(file.fileno())
        os.replace(temp,path)
        with closing(_connect(root)) as con:
            con.execute("INSERT INTO assets(asset_id,project_id,owner_user_id,original_filename,"
                        "media_type,visibility,sha256,size,created_utc) VALUES(?,?,?,?,?,?,?,?,?)",
                (asset_id,project_id,owner_user_id,filename,mime,visibility,digest,
                 len(data),datetime.now(timezone.utc).isoformat()))
            con.commit()
    finally:temp.unlink(missing_ok=True)
    return {"asset_id":asset_id,"sha256":digest,"size":len(data),"visibility":visibility}

def _authorized(record,identity,project_id):
    if record["project_id"]!=project_id:return False
    roles={x["role"] for x in identity["memberships"] if x["project_id"]==project_id}
    if not roles:return False
    if record["visibility"]=="private":
        return record["owner_user_id"]==identity["user_id"]
    return bool(roles.intersection({"viewer","editor","reviewer","publisher","owner"}))

def list_assets(root,identity,project_id):
    if project_id!=PROJECT:return []
    with closing(_connect(root)) as con:
        return [{
            k:r[k] for k in ("asset_id","original_filename","media_type","visibility",
                            "size","sha256","owner_user_id","created_utc")}
            for r in con.execute("SELECT * FROM assets WHERE project_id=? "
                                 "ORDER BY created_utc DESC,asset_id",(project_id,))
            if _authorized(r,identity,project_id)
            and ("asset_state" not in r.keys() or
                 (r["asset_state"]=="approved" and r["deleted_at"] is None))]

def download_asset(root,identity,project_id,asset_id):
    if project_id!=PROJECT or type(asset_id) is not str:
        return None
    try:
        if str(uuid.UUID(asset_id))!=asset_id:return None
    except (ValueError,AttributeError):return None
    with closing(_connect(root)) as con:
        row=con.execute("SELECT * FROM assets WHERE asset_id=?",(asset_id,)).fetchone()
        if row is None or not _authorized(row,identity,project_id):return None
        if "asset_state" in row.keys() and (row["asset_state"]!="approved" or
                                              row["deleted_at"] is not None):return None
        metadata=dict(row)
    ver=metadata.get("current_version",1)
    if type(ver) is not int or not 1<=ver<=100000:
        raise RuntimeError("Invalid active asset version")
    path=Path(root)/(("asset-"+asset_id+".bin") if ver==1 else
                      ("asset-"+asset_id+"-v"+str(ver)+".bin"))
    if path.is_symlink() or not path.is_file():raise RuntimeError("Missing asset")
    flags=os.O_RDONLY|os.O_NOFOLLOW
    fd=os.open(path,flags)
    try:
        info=os.fstat(fd)
        if info.st_size!=metadata["size"] or info.st_size>MAX_BYTES:
            raise RuntimeError("Asset size differs from catalog")
        with os.fdopen(fd,"rb",closefd=False) as stream:
            content=stream.read(MAX_BYTES+1)
        if len(content)!=metadata["size"] or hashlib.sha256(content).hexdigest()!=metadata["sha256"]:
            raise RuntimeError("Asset integrity mismatch")
    finally:os.close(fd)
    # Never serve a user-supplied filename in HTTP Content-Disposition.
    return {"content":content,"media_type":metadata["media_type"],
            "filename":"asset-"+asset_id+KINDS[metadata["media_type"]][1],
            "sha256":metadata["sha256"]}
