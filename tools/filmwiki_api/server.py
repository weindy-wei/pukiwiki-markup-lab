#!/usr/bin/env python3
"""FilmWiki private loopback read-only SQLite HTTP API v0.1.

The API never accepts a database path, SQL, file path, or user-controlled source
fingerprint over HTTP. Serving requires explicit --db, --data-root and --token-file.
"""
import argparse
import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import parse_qs, urlsplit

MAX_LIMIT = 200
MAX_OFFSET = 200000
HOST = "127.0.0.1"
ORIGINS = frozenset(("http://127.0.0.1:8080", "http://localhost:8080"))
SHA_HEX = frozenset("0123456789abcdef")


def public_json(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",",":"), allow_nan=False).encode("utf-8")


def open_readonly(db):
    resolved = Path(db).resolve(strict=True)
    con = sqlite3.connect(resolved.as_uri()+"?mode=ro", uri=True, timeout=3.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA trusted_schema=OFF")
    return con


def manifest(db, sha):
    with open_readonly(db) as con:
        row = con.execute(
            "SELECT source_filename, preview_sha256 FROM imports WHERE source_sha256=?", (sha,)
        ).fetchone()
        if row is None:
            raise ValueError("Configured source SHA-256 not present")
        sheets=[]
        for s in con.execute(
            "SELECT sheet_index,name FROM sheets WHERE source_sha256=? ORDER BY sheet_index", (sha,)
        ).fetchall():
            si=s["sheet_index"]
            fields=[
                {"index":f["column_index"],"name":f["name"]}
                for f in con.execute(
                    "SELECT column_index,name FROM fields WHERE source_sha256=? AND sheet_index=? ORDER BY column_index",
                    (sha,si)
                ).fetchall()
            ]
            count=con.execute(
                "SELECT COUNT(*) FROM records WHERE source_sha256=? AND sheet_index=?", (sha,si)
            ).fetchone()[0]
            sheets.append({"sheet_index":si,"name":s["name"],"fields":fields,"record_count":count})
        return {"format":"filmwiki-read-api-manifest","version":1,
                "source":{"sha256":sha,"filename":row["source_filename"],"preview_sha256":row["preview_sha256"]},
                "sheets":sheets,"read_only":True}


def page_records(db,sha,sheet_index,offset,limit):
    if not 1 <= sheet_index <= 100:
        raise ValueError("Invalid sheet index")
    if offset < 0 or offset > MAX_OFFSET:
        raise ValueError("Invalid page offset")
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError("Invalid page limit")
    with open_readonly(db) as con:
        sheet=con.execute(
            "SELECT name FROM sheets WHERE source_sha256=? AND sheet_index=?", (sha,sheet_index)
        ).fetchone()
        if sheet is None:
            raise KeyError("Sheet not found")
        fields=con.execute(
            "SELECT column_index,name FROM fields WHERE source_sha256=? AND sheet_index=? ORDER BY column_index",
            (sha,sheet_index)
        ).fetchall()
        total=con.execute(
            "SELECT COUNT(*) FROM records WHERE source_sha256=? AND sheet_index=?", (sha,sheet_index)
        ).fetchone()[0]
        selected=con.execute(
            "SELECT record_id,source_row,shot_number_json FROM records "
            "WHERE source_sha256=? AND sheet_index=? ORDER BY source_row LIMIT ? OFFSET ?",
            (sha,sheet_index,limit,offset)
        ).fetchall()
        records=[]
        assets=[]
        for row in selected:
            rid=row["record_id"]
            cells=dict(con.execute(
                "SELECT column_index,value_json FROM cells WHERE record_id=?", (rid,)
            ).fetchall())
            if len(cells)!=len(fields):
                raise ValueError("Incomplete record cells")
            relation=con.execute(
                "SELECT parent_label,status,parent_record_id,candidate_record_ids_json "
                "FROM shot_parent_links WHERE child_record_id=?", (rid,)
            ).fetchone()
            records.append({
                "record_id":rid,"source_row":row["source_row"],
                "shot_number":json.loads(row["shot_number_json"]),
                "cells":[{"index":f["column_index"],"field":f["name"],
                          "value":json.loads(cells[f["column_index"]])} for f in fields],
                "parent_link":None if relation is None else {
                    "source_label":relation["parent_label"],
                    "status":relation["status"],
                    "parent_record_id":relation["parent_record_id"],
                    "candidate_record_ids":json.loads(relation["candidate_record_ids_json"])
                }
            })
            assets.extend([
                {"ref_id":a["ref_id"],"record_id":rid,"filename":a["filename"],
                 "source_column":a["source_column"],"status":a["status"]}
                for a in con.execute(
                    "SELECT ref_id,filename,source_column,status FROM asset_references "
                    "WHERE source_sha256=? AND record_id=? ORDER BY ref_id", (sha,rid)
                ).fetchall()
            ])
        return {"sheet_index":sheet_index,"name":sheet["name"],
                "offset":offset,"limit":limit,"total":total,
                "next_offset":offset+len(records) if offset+len(records)<total else None,
                "records":records,"asset_references":assets}


class FilmWikiServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self, address, handler, db, sha, token):
        self.db=Path(db)
        self.sha=sha
        self.token=token
        super().__init__(address,handler)


class Handler(BaseHTTPRequestHandler):
    server_version="FilmWikiReadOnly/0.1"
    sys_version=""
    def log_message(self,format,*args):
        # Never log Authorization header, credentials or full query strings.
        msg = format % args
        print("FilmWiki API:",msg[:280],flush=True)

    def respond(self,status,data=None,origin=None):
        body=public_json(data if data is not None else {})
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("Content-Security-Policy","default-src 'none'")
        self.send_header("Vary","Origin")
        if origin in ORIGINS:
            self.send_header("Access-Control-Allow-Origin",origin)
        self.end_headers()
        if self.command!="HEAD":
            self.wfile.write(body)

    def allowed_origin(self):
        origin=self.headers.get("Origin")
        return origin is None or origin in ORIGINS

    def check_auth(self):
        header=self.headers.get("Authorization","")
        if not header.startswith("Bearer ") or len(header)>300:
            return False
        token=header[len("Bearer "):]
        return hmac.compare_digest(token,self.server.token)

    def do_OPTIONS(self):
        origin=self.headers.get("Origin")
        if origin not in ORIGINS:
            self.respond(403,{"error":"origin_not_allowed"})
            return
        if self.headers.get("Access-Control-Request-Method")!="GET":
            self.respond(405,{"error":"read_only"},origin)
            return
        requested=self.headers.get("Access-Control-Request-Headers","").lower()
        if requested not in ("authorization",""):
            self.respond(403,{"error":"unsupported_headers"},origin)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin",origin)
        self.send_header("Access-Control-Allow-Methods","GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Authorization")
        self.send_header("Access-Control-Max-Age","300")
        self.send_header("Cache-Control","no-store")
        self.send_header("Vary","Origin")
        self.end_headers()

    def do_GET(self):
        origin=self.headers.get("Origin")
        if not self.allowed_origin():
            self.respond(403,{"error":"origin_not_allowed"})
            return
        path=urlsplit(self.path)
        if len(self.path)>2048 or path.fragment:
            self.respond(400,{"error":"invalid_request"},origin)
            return
        if path.path=="/v1/health" and not path.query:
            self.respond(200,{"status":"ok","read_only":True},origin)
            return
        if not self.check_auth():
            self.respond(401,{"error":"unauthorized"},origin)
            return
        try:
            if path.path=="/v1/manifest" and not path.query:
                data=manifest(self.server.db,self.server.sha)
            elif path.path=="/v1/records":
                qs=parse_qs(path.query,keep_blank_values=True)
                if set(qs)-{"sheet","offset","limit"} or len(qs.get("sheet",[]))!=1:
                    raise ValueError("Invalid pagination parameters")
                if any(len(v)!=1 for v in qs.values()):
                    raise ValueError("Duplicate pagination parameters")
                data=page_records(
                    self.server.db,self.server.sha,
                    int(qs["sheet"][0]),
                    int(qs.get("offset",["0"])[0]),
                    int(qs.get("limit",["100"])[0])
                )
            else:
                self.respond(404,{"error":"route_not_found"},origin)
                return
        except (ValueError,TypeError,OverflowError):
            self.respond(400,{"error":"invalid_parameters_or_source"},origin)
            return
        except KeyError:
            self.respond(404,{"error":"sheet_not_found"},origin)
            return
        except sqlite3.Error:
            self.respond(503,{"error":"source_unavailable"},origin)
            return
        self.respond(200,data,origin)

    def do_HEAD(self):
        self.respond(405,{"error":"read_only"})
    def do_POST(self):
        self.respond(405,{"error":"read_only"})
    def do_PUT(self):
        self.respond(405,{"error":"read_only"})
    def do_PATCH(self):
        self.respond(405,{"error":"read_only"})
    def do_DELETE(self):
        self.respond(405,{"error":"read_only"})


def validate_startup(db,root,token_file,source):
    if len(source)!=64 or any(x not in SHA_HEX for x in source):
        raise ValueError("Invalid configured source SHA-256")
    data_root=Path(root).resolve(strict=True)
    path=Path(db).resolve(strict=True)
    if not data_root.is_dir() or not path.is_file() or not path.is_relative_to(data_root):
        raise ValueError("SQLite database must be inside private data root")
    private=Path(token_file).resolve(strict=True)
    if not private.is_file() or private.is_relative_to(data_root/"web"):
        raise ValueError("Invalid token file")
    token=private.read_text(encoding="utf-8").strip()
    if len(token)<40 or len(token)>200 or not token.isascii():
        raise ValueError("Token file must contain strong token")
    manifest(path,source)  # fail closed before opening a listening socket
    return path,token


def initialize_token(path):
    path=Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("Token path must be absolute and not a symlink")
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("x",encoding="utf-8",opener=lambda p,f:__import__("os").open(p,f,0o600)) as out:
        out.write(secrets.token_urlsafe(48)+"\n")
    print("Token generated at specified private path (value not displayed).")


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db",type=Path)
    ap.add_argument("--data-root",type=Path)
    ap.add_argument("--source-sha")
    ap.add_argument("--token-file",type=Path,required=True)
    ap.add_argument("--init-token",action="store_true",help="Create token only; do not start service")
    ap.add_argument("--port",type=int,default=8091)
    a=ap.parse_args()
    if a.init_token:
        initialize_token(a.token_file)
        return
    if not a.db or not a.data_root or not a.source_sha:
        ap.error("Serving requires --db --data-root --source-sha and --token-file")
    if a.port<1024 or a.port>65535:
        ap.error("Invalid port")
    db,token=validate_startup(a.db,a.data_root,a.token_file,a.source_sha)
    with FilmWikiServer((HOST,a.port),Handler,db,a.source_sha,token) as server:
        print(f"FilmWiki read-only API listening at http://{HOST}:{a.port}",flush=True)
        server.serve_forever(poll_interval=0.5)


if __name__=="__main__":
    main()
