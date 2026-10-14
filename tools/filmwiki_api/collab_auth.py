#!/usr/bin/env python3
"""FilmWiki Step31 pilot identity store. Owner-only local users and sessions.

Not a production identity provider. This store is isolated from all wiki/PHP
credentials. No HTTP self-signup or arbitrary project grants; admin CLI only.
"""
from contextlib import closing
from datetime import datetime,timezone
import hashlib,hmac,os,re,secrets,sqlite3,time,uuid
from pathlib import Path

USER_RE=re.compile(r"^[a-z][a-z0-9_.-]{2,31}$")
PROJECT_RE=re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
ROLES={"viewer","editor","reviewer","publisher","owner"}
SESSION_SECONDS=8*3600
SCHEMA="""
PRAGMA foreign_keys=ON;
CREATE TABLE users (
  user_id TEXT PRIMARY KEY,username TEXT NOT NULL UNIQUE,
  salt BLOB NOT NULL,password_hash BLOB NOT NULL,
  active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  failures INTEGER NOT NULL DEFAULT 0,
  locked_until INTEGER NOT NULL DEFAULT 0,
  created_utc TEXT NOT NULL
);
CREATE TABLE memberships (
  project_id TEXT NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(user_id),
  role TEXT NOT NULL CHECK(role IN ('viewer','editor','reviewer','publisher','owner')),
  PRIMARY KEY(project_id,user_id,role)
);
CREATE TABLE sessions (
  session_sha256 TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(user_id),
  created_at INTEGER NOT NULL,expires_at INTEGER NOT NULL
);
CREATE INDEX session_expiry ON sessions(expires_at);
"""

def connect(path):
    path=Path(path).resolve(strict=True)
    con=sqlite3.connect(path.as_uri()+"?mode=rw",uri=True,isolation_level=None,timeout=10)
    con.row_factory=sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=10000")
    return con

def create_store(path,private_root):
    root=Path(private_root).resolve(strict=True)
    path=Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.parent.resolve().is_relative_to(root) or path.exists():
        raise ValueError("Use a fresh local-private identity DB")
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(path)) as con:
            con.executescript(SCHEMA)
        os.chmod(path,0o600)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return str(path)

def _hash(password,salt):
    return hashlib.pbkdf2_hmac("sha256",password.encode("utf-8"),salt,600000)

def create_user(db,username,password):
    if not isinstance(username,str) or not USER_RE.fullmatch(username) or \
       type(password) is not str or not 12<=len(password)<=128 or \
       len(password.encode())>300 or len(set(password))<6:
        raise ValueError("Username/password policy failed")
    salt=secrets.token_bytes(24)
    uid="user:"+str(uuid.uuid4())
    when=datetime.now(timezone.utc).isoformat()
    with closing(connect(db)) as con:
        with con:
            con.execute("INSERT INTO users(user_id,username,salt,password_hash,created_utc) "
                        "VALUES(?,?,?,?,?)",(uid,username,salt,_hash(password,salt),when))
    return {"user_id":uid,"username":username}

def grant(db,project_id,username,role):
    if not PROJECT_RE.fullmatch(project_id) or role not in ROLES:
        raise ValueError("Invalid project/role")
    with closing(connect(db)) as con:
        with con:
            row=con.execute("SELECT user_id FROM users WHERE username=? AND active=1",(username,)).fetchone()
            if row is None:raise ValueError("Unknown active user")
            con.execute("INSERT OR IGNORE INTO memberships VALUES(?,?,?)",
                        (project_id,row[0],role))
            return row[0]

def disable(db,username):
    with closing(connect(db)) as con:
        with con:
            row=con.execute("SELECT user_id FROM users WHERE username=?",(username,)).fetchone()
            if not row:raise ValueError("Unknown user")
            con.execute("UPDATE users SET active=0 WHERE user_id=?",(row[0],))
            con.execute("DELETE FROM sessions WHERE user_id=?",(row[0],))
            con.execute("DELETE FROM memberships WHERE user_id=?",(row[0],))
            return row[0]

def _token_hash(token):
    if type(token) is not str or not re.fullmatch(r"[0-9a-f]{96}",token):
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()

def login(db,username,password,now=None):
    now=int(time.time()) if now is None else int(now)
    # Same-cost dummy PBKDF to reduce user-enumeration timing differences.
    dummy_salt=b"\x00"*24
    if not isinstance(username,str) or not USER_RE.fullmatch(username) or \
       type(password) is not str or len(password)>128:
        _hash("invalid",dummy_salt)
        return None
    with closing(connect(db)) as con:
        try:
            con.execute("BEGIN IMMEDIATE")
            row=con.execute("SELECT * FROM users WHERE username=?",(username,)).fetchone()
            if not row:
                _hash(password,dummy_salt);con.execute("COMMIT");return None
            matched=hmac.compare_digest(_hash(password,row["salt"]),row["password_hash"])
            if not row["active"] or now<row["locked_until"] or not matched:
                if row["active"] and not matched:
                    failures=row["failures"]+1
                    locked_until=now+900 if failures>=5 else 0
                    con.execute("UPDATE users SET failures=?,locked_until=? WHERE user_id=?",
                                (0 if failures>=5 else failures,locked_until,row["user_id"]))
                con.execute("COMMIT");return None
            con.execute("UPDATE users SET failures=0,locked_until=0 WHERE user_id=?",(row["user_id"],))
            raw=secrets.token_hex(48)
            con.execute("INSERT INTO sessions VALUES(?,?,?,?)",
                        (_token_hash(raw),row["user_id"],now,now+SESSION_SECONDS))
            con.execute("COMMIT")
            return {"access_token":raw,"expires_in":SESSION_SECONDS,
                    "user_id":row["user_id"],"username":row["username"]}
        except Exception:
            if con.in_transaction:con.execute("ROLLBACK")
            raise

def session(db,token,now=None):
    now=int(time.time()) if now is None else int(now)
    digest=_token_hash(token)
    if digest is None:return None
    with closing(connect(db)) as con:
        user=con.execute("SELECT u.user_id,u.username,u.active,s.expires_at "
                         "FROM sessions s JOIN users u ON u.user_id=s.user_id "
                         "WHERE s.session_sha256=?",(digest,)).fetchone()
        if user is None or not user["active"] or now>=user["expires_at"]:return None
        roles=[dict(x) for x in con.execute(
            "SELECT project_id,role FROM memberships WHERE user_id=? ORDER BY project_id,role",
            (user["user_id"],))]
        return {"user_id":user["user_id"],"username":user["username"],
                "expires_at":user["expires_at"],"memberships":roles}

def logout(db,token):
    digest=_token_hash(token)
    if digest is None:return
    with closing(connect(db)) as con:
        with con:con.execute("DELETE FROM sessions WHERE session_sha256=?",(digest,))

def require_role(identity,project_id,kind):
    roles={x["role"] for x in identity["memberships"] if x["project_id"]==project_id}
    allowed=({"viewer","editor","reviewer","publisher","owner"}
             if kind=="read" else {"editor","owner"} if kind=="edit" else set())
    if not roles.intersection(allowed):
        raise PermissionError("Project membership insufficient")

def list_active_members(db,project_id):
    with closing(connect(db)) as con:
        return [dict(row) for row in con.execute(
            "SELECT u.user_id,u.username,m.role FROM memberships m "
            "JOIN users u ON u.user_id=m.user_id WHERE m.project_id=? "
            "AND u.active=1 ORDER BY u.username,m.role",(project_id,))]
