#!/usr/bin/env python3
"""FilmWiki Step31 private authenticated collaboration service.

Same trusted-PC loopback origin for this first pilot; no remote/LAN/TLS claim.
Unlike local 8094, all data access requires a real per-user logged-in session.
"""
import argparse,json,re,sqlite3
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from server import Handler as ResponseHandler
from editor_store import (manifest,list_records,record,history,change,
                          verify_history,get_meta,InvalidEdit,
                          VersionConflict,DuplicateRequest,MissingRecord)
from workflow_v5 import batch_change,relation
from collab_auth import login,session,logout,require_role

ORIGINS={"http://127.0.0.1:8080","http://localhost:8080"}
HOST="127.0.0.1"
MAX_BODY=16384

class CollabServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self,address,auth_db,editor_db,project_id):
        if address[0]!=HOST:raise ValueError("Collaboration pilot must be loopback-only")
        self.auth_db=Path(auth_db)
        self.editor_db=Path(editor_db)
        self.project_id=project_id
        super().__init__(address,Handler)

class Handler(ResponseHandler):
    server_version="FilmWikiCollab/0.1"
    def log_message(self,fmt,*args):
        # Never retain username, passwords, session tokens, record content or URLs.
        code=str(args[1]) if len(args)>1 else "?"
        print("FilmWiki collaboration HTTP "+code,flush=True)
    def _origin_host(self):
        hosts=self.headers.get_all("Host",[])
        if len(hosts)!=1 or hosts[0].lower() not in (
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}"):
            self.respond(403,{"error":"invalid_host"});return None
        origin=self.headers.get("Origin")
        if origin not in ORIGINS or len(self.headers.get_all("Origin",[]))!=1:
            self.respond(403,{"error":"invalid_origin"});return None
        if len(self.path)>1400 or urlsplit(self.path).fragment:
            self.respond(400,{"error":"invalid_path"},origin);return None
        return origin
    def do_OPTIONS(self):
        origin=self._origin_host()
        if origin is None:return
        if self.headers.get("Access-Control-Request-Method") not in ("GET","POST"):
            return self.respond(405,{"error":"invalid_method"},origin)
        headers={x.strip().lower() for x in self.headers.get(
             "Access-Control-Request-Headers","").split(",") if x.strip()}
        if not headers.issubset({"authorization","content-type"}):
            return self.respond(403,{"error":"invalid_headers"},origin)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin",origin)
        self.send_header("Access-Control-Allow-Headers","Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS")
        self.send_header("Cache-Control","no-store")
        self.send_header("Vary","Origin")
        self.end_headers()
    def _identity(self,origin):
        header=self.headers.get("Authorization","")
        if not header.startswith("Bearer ") or len(header)>150:
            self.respond(401,{"error":"login_required"},origin);return None
        user=session(self.server.auth_db,header[7:])
        if user is None:
            self.respond(401,{"error":"session_expired"},origin);return None
        return user
    def _body(self):
        if self.headers.get("Content-Type","").split(";")[0].lower()!="application/json":
            raise InvalidEdit("Only application/json accepted")
        rawlen=self.headers.get("Content-Length","")
        if not rawlen.isdigit() or not 0<int(rawlen)<=MAX_BODY:
            raise InvalidEdit("Invalid request size")
        val=json.loads(self.rfile.read(int(rawlen)))
        if type(val) is not dict:raise InvalidEdit("Object expected")
        return val
    def do_GET(self):
        origin=self._origin_host()
        if origin is None:return
        if self.path=="/v5/health":
            return self.respond(200,{"status":"ok","private_collaboration":True},origin)
        user=self._identity(origin)
        if user is None:return
        try:
            if urlsplit(self.path).path=="/v5/me":
                if urlsplit(self.path).query:raise InvalidEdit("No query expected")
                return self.respond(200,{"user_id":user["user_id"],
                   "username":user["username"],"memberships":user["memberships"]},origin)
            require_role(user,self.server.project_id,"read")
            u=urlsplit(self.path)
            q=parse_qs(u.query,keep_blank_values=True)
            if any(len(x)!=1 for x in q.values()):raise InvalidEdit("Duplicate query fields")
            if u.path=="/v5/manifest":
                if q:raise InvalidEdit("Unexpected query")
                result=manifest(self.server.editor_db)
                # Viewers may inspect field names but cannot change fields.
                if not any(x["project_id"]==self.server.project_id and
                  x["role"] in ("editor","owner") for x in user["memberships"]):
                    for sheet in result["sheets"]:
                        for col in sheet["fields"]:col["editable"]=False
            elif u.path=="/v5/records":
                if "sheet" not in q or set(q)-{"sheet","after","limit","q"}:
                    raise InvalidEdit("Invalid query")
                result=list_records(self.server.editor_db,int(q["sheet"][0]),
                                    int(q.get("after",["1"])[0]),
                                    int(q.get("limit",["25"])[0]),q.get("q",[""])[0])
            elif u.path in ("/v5/record","/v5/history","/v5/relationship"):
                if set(q)!={"sheet","id"}:raise InvalidEdit("Invalid query")
                si=int(q["sheet"][0]);rid=q["id"][0]
                if not re.fullmatch(r"src_[a-zA-Z0-9_-]{1,95}",rid):
                    raise InvalidEdit("Invalid record ID")
                result=(record(self.server.editor_db,si,rid) if u.path=="/v5/record"
                        else history(self.server.editor_db,si,rid) if u.path=="/v5/history"
                        else relation(self.server.editor_db,si,rid))
                if u.path=="/v5/record" and not any(
                   x["project_id"]==self.server.project_id and x["role"] in ("editor","owner")
                   for x in user["memberships"]):
                    for field in result["cells"]:field["editable"]=False
            else:return self.respond(404,{"error":"route_not_found"},origin)
            return self.respond(200,result,origin)
        except PermissionError:return self.respond(403,{"error":"not_a_project_member"},origin)
        except MissingRecord:return self.respond(404,{"error":"record_not_found"},origin)
        except (ValueError,TypeError,OverflowError,IndexError):
            return self.respond(400,{"error":"invalid_query"},origin)
        except (sqlite3.Error,OSError,RuntimeError):
            return self.respond(503,{"error":"storage_unavailable"},origin)
    def do_POST(self):
        origin=self._origin_host()
        if origin is None:return
        path=urlsplit(self.path)
        if path.query:return self.respond(400,{"error":"invalid_path"},origin)
        if path.path not in ("/v5/login","/v5/logout","/v5/edit","/v5/batch"):
            return self.respond(404,{"error":"route_not_found"},origin)
        try:body=self._body()
        except (ValueError,TypeError,json.JSONDecodeError):
            return self.respond(400,{"error":"invalid_body"},origin)
        if path.path=="/v5/login":
            if set(body)!={"username","password"}:
                return self.respond(400,{"error":"invalid_body"},origin)
            result=login(self.server.auth_db,body["username"],body["password"])
            if result is None:return self.respond(401,{"error":"invalid_credentials"},origin)
            return self.respond(200,{"format":"filmwiki-session","version":1,**result},origin)
        user=self._identity(origin)
        if user is None:return
        if path.path=="/v5/logout":
            if body:return self.respond(400,{"error":"invalid_body"},origin)
            logout(self.server.auth_db,self.headers.get("Authorization","")[7:])
            return self.respond(200,{"logged_out":True},origin)
        try:
            require_role(user,self.server.project_id,"edit")
            if path.path=="/v5/edit":
                if set(body)!={"project_id","sheet_index","record_id","column_index",
                       "value","expected_version","reason","request_id"}:
                    raise InvalidEdit("Invalid single-record payload")
                result=change(self.server.editor_db,body["project_id"],
                        body["sheet_index"],body["record_id"],body["column_index"],
                        body["value"],body["expected_version"],body["reason"],
                        body["request_id"],actor=user["user_id"])
            else:
                if set(body)!={"project_id","operations","reason","batch_id"}:
                    raise InvalidEdit("Invalid batch payload")
                result=batch_change(self.server.editor_db,body["project_id"],
                        body["operations"],body["reason"],body["batch_id"],
                        actor=user["user_id"])
        except PermissionError:return self.respond(403,{"error":"insufficient_edit_role"},origin)
        except (VersionConflict,DuplicateRequest):
            return self.respond(409,{"error":"version_conflict_or_replay"},origin)
        except MissingRecord:return self.respond(404,{"error":"record_not_found"},origin)
        except (ValueError,TypeError,OverflowError):
            return self.respond(400,{"error":"invalid_edit"},origin)
        except (sqlite3.Error,OSError,RuntimeError):
            return self.respond(503,{"error":"storage_unavailable"},origin)
        return self.respond(200,{"format":"filmwiki-collab-revision","version":1,**result},origin)
    def do_PUT(self):self.respond(405,{"error":"method_not_allowed"})
    def do_PATCH(self):self.respond(405,{"error":"method_not_allowed"})
    def do_DELETE(self):self.respond(405,{"error":"method_not_allowed"})

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-root",required=True,type=Path)
    p.add_argument("--auth-db",required=True,type=Path)
    p.add_argument("--editor-db",required=True,type=Path)
    p.add_argument("--project-id",required=True)
    p.add_argument("--port",default=8098,type=int)
    a=p.parse_args()
    root=a.data_root.resolve(strict=True)
    for x in (a.auth_db,a.editor_db):
        path=x.resolve(strict=True)
        if not path.is_file() or not path.is_relative_to(root):
            p.error("DB must be local-private")
    if get_meta(a.editor_db)["project_id"]!=a.project_id:
        p.error("Editor project ID mismatch")
    verify_history(a.editor_db)
    with CollabServer((HOST,a.port),a.auth_db,a.editor_db,a.project_id) as app:
        print("FilmWiki Step31 private collaboration on 127.0.0.1:"+str(a.port),flush=True)
        app.serve_forever(poll_interval=.4)

if __name__=="__main__":main()
