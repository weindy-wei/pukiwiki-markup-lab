#!/usr/bin/env python3
"""FilmWiki Step28 localhost-only revisioned editor API, separate credential/port.

Not an extension of production read-only v3 routes. Edits operate on an isolated
private working COPY of a synthetic source, never on canonical source SQLite.
"""
import argparse
import hmac
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import re
import secrets
import sqlite3
import stat
from urllib.parse import parse_qs,urlsplit

from server import Handler as BaseHandler,ORIGINS
from editor_store import (manifest,list_records,record,history,change,verify_history,
                          get_meta,InvalidEdit,VersionConflict,DuplicateRequest,MissingRecord)

HOST="127.0.0.1"
MAX_BODY=16384

def require_private_token(path,root):
    root=Path(root).resolve(strict=True)
    path=Path(path).resolve(strict=True)
    if not path.is_file() or not path.is_relative_to(root):
        raise ValueError("Editor token must be private to the owner")
    if (path.stat().st_mode & 0o777)!=0o600:raise ValueError("Editor token must be mode 0600")
    value=path.read_text(encoding="utf-8").strip()
    if not re.fullmatch("[a-f0-9]{64,128}",value):
        raise ValueError("Invalid token length/encoding")
    return value


def create_token(path):
    path=Path(path)
    if path.exists():raise FileExistsError("Existing token cannot be overwritten")
    import os
    descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(descriptor,"w",encoding="utf-8") as stream:
        stream.write(secrets.token_hex(48)+"\n")
    return path


class EditorHTTP(ThreadingHTTPServer):
    allow_reuse_address=True
    daemon_threads=True
    def __init__(self,address,db,token,project_id):
        self.db=Path(db);self.token=token;self.project_id=project_id
        super().__init__(address,Handler)


class Handler(BaseHandler):
    server_version="FilmWikiEditor/0.1"
    def log_message(self,format,*args):
        # Do not retain query text, record data, Authorization, UUID, or edits.
        status=args[1] if len(args)>1 else "?"
        print("FilmWiki editor request HTTP "+str(status),flush=True)

    def check_auth(self):
        header=self.headers.get("Authorization","")
        if not header.startswith("Bearer ") or len(header)>300:return False
        return hmac.compare_digest(header[7:],self.server.token)

    def do_OPTIONS(self):
        origin=self.headers.get("Origin")
        if origin not in ORIGINS:
            return self.respond(403,{"error":"origin_not_allowed"})
        if self.headers.get("Access-Control-Request-Method") not in ("GET","POST"):
            return self.respond(405,{"error":"unsupported_method"},origin)
        headers={x.strip().lower() for x in self.headers.get(
            "Access-Control-Request-Headers","").split(",") if x.strip()}
        if not headers.issubset({"authorization","content-type"}):
            return self.respond(403,{"error":"unsupported_headers"},origin)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin",origin)
        self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age","300")
        self.send_header("Cache-Control","no-store")
        self.send_header("Vary","Origin")
        self.end_headers()

    def _request_valid(self,write=False):
        origin=self.headers.get("Origin")
        if origin not in ORIGINS and (write or origin is not None):
            self.respond(403,{"error":"origin_not_allowed"})
            return False
        if len(self.path)>1400 or urlsplit(self.path).fragment:
            self.respond(400,{"error":"invalid_path"},origin)
            return False
        if not self.check_auth():
            self.respond(401,{"error":"unauthorized"},origin)
            return False
        return True

    def do_GET(self):
        if urlsplit(self.path).path=="/v4/health" and not urlsplit(self.path).query:
            return self.respond(200,{"status":"ok","editor_working_copy":True})
        if not self._request_valid():return
        u=urlsplit(self.path)
        origin=self.headers.get("Origin")
        qs=parse_qs(u.query,keep_blank_values=True)
        try:
            if any(len(vals)!=1 for vals in qs.values()):
                raise InvalidEdit("Duplicate parameters")
            if u.path=="/v4/manifest":
                if qs:raise InvalidEdit("Unexpected query")
                response=manifest(self.server.db)
            elif u.path=="/v4/records":
                if "sheet" not in qs or set(qs)-{"sheet","after","limit","q"}:
                    raise InvalidEdit("Invalid query")
                response=list_records(
                    self.server.db,int(qs["sheet"][0]),int(qs.get("after",["1"])[0]),
                    int(qs.get("limit",["25"])[0]),qs.get("q",[""])[0]
                )
            elif u.path in ("/v4/record","/v4/history"):
                if set(qs)!={"sheet","id"}:raise InvalidEdit("Invalid query")
                sheet=int(qs["sheet"][0]);rid=qs["id"][0]
                if not re.fullmatch(r"src_[a-zA-Z0-9_-]{1,95}",rid):
                    raise InvalidEdit("Invalid record ID")
                response=(record(self.server.db,sheet,rid) if u.path=="/v4/record"
                          else history(self.server.db,sheet,rid))
            else:
                return self.respond(404,{"error":"route_not_found"},origin)
        except (InvalidEdit,ValueError,TypeError,OverflowError,IndexError):
            return self.respond(400,{"error":"invalid_query"},origin)
        except MissingRecord:
            return self.respond(404,{"error":"record_not_found"},origin)
        except (sqlite3.Error,OSError,RuntimeError):
            return self.respond(503,{"error":"storage_unavailable"},origin)
        return self.respond(200,response,origin)

    def do_POST(self):
        if not self._request_valid(write=True):return
        origin=self.headers.get("Origin")
        if urlsplit(self.path).path!="/v4/edit" or urlsplit(self.path).query:
            return self.respond(404,{"error":"route_not_found"},origin)
        if self.headers.get("Content-Type","").split(";")[0].lower()!="application/json":
            return self.respond(415,{"error":"json_only"},origin)
        try:
            size=int(self.headers.get("Content-Length","-1"))
            if not 0<size<=MAX_BODY:raise ValueError()
            raw=self.rfile.read(size)
            request=json.loads(raw)
            if not isinstance(request,dict) or set(request)!={
                "project_id","sheet_index","record_id","column_index","value",
                "expected_version","reason","request_id"
            }:raise InvalidEdit("Invalid patch schema")
            result=change(
                self.server.db,request["project_id"],request["sheet_index"],
                request["record_id"],request["column_index"],request["value"],
                request["expected_version"],request["reason"],request["request_id"]
            )
        except (VersionConflict,DuplicateRequest):
            return self.respond(409,{"error":"version_conflict_or_replay","action":"reload_record"},origin)
        except MissingRecord:
            return self.respond(404,{"error":"record_not_found"},origin)
        except (InvalidEdit,ValueError,TypeError,OverflowError,json.JSONDecodeError):
            return self.respond(400,{"error":"invalid_patch"},origin)
        except (sqlite3.Error,OSError):
            return self.respond(503,{"error":"storage_unavailable"},origin)
        return self.respond(200,{"format":"filmwiki-editor-edit","version":1,**result},origin)

    def do_PUT(self):self.respond(405,{"error":"not_supported"})
    def do_PATCH(self):self.respond(405,{"error":"not_supported"})
    def do_DELETE(self):self.respond(405,{"error":"not_supported"})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root",required=True,type=Path)
    parser.add_argument("--db",required=True,type=Path)
    parser.add_argument("--token-file",required=True,type=Path)
    parser.add_argument("--project-id",required=True)
    parser.add_argument("--port",type=int,default=8094)
    args=parser.parse_args()
    root=args.data_root.resolve(strict=True)
    db=args.db.resolve(strict=True)
    if not db.is_file() or not db.is_relative_to(root):
        parser.error("Editor database must be in local private root")
    meta=get_meta(db)
    if meta["project_id"]!=args.project_id:parser.error("Editor project identity mismatch")
    verify_history(db)
    token=require_private_token(args.token_file,root)
    with EditorHTTP((HOST,args.port),db,token,args.project_id) as server:
        print("FilmWiki isolated working-copy editor ready: 127.0.0.1:"+str(args.port),
              flush=True)
        server.serve_forever(poll_interval=0.5)

if __name__=="__main__":main()
