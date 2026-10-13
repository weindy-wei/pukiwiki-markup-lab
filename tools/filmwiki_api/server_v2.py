#!/usr/bin/env python3
"""FilmWiki v2 localhost multi-project read API; backward-compatible with v1."""
import argparse
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qs, urlsplit

from server import Handler as V1Handler, FilmWikiServer, HOST, validate_startup
from catalog_v2 import list_projects, describe_project, read_page, read_record, read_registry


class Handler(V1Handler):
    server_version="FilmWikiReadOnly/0.2"

    def log_message(self,format,*args):
        # Avoid retaining search terms, record identifiers, and credential material.
        try:
            code=str(args[1]) if len(args)>1 else "?"
        except Exception:
            code="?"
        print("FilmWiki v2 request status="+code,flush=True)

    def do_GET(self):
        uri=urlsplit(self.path)
        if not uri.path.startswith("/v2/"):
            return super().do_GET()
        origin=self.headers.get("Origin")
        if not self.allowed_origin():
            return self.respond(403,{"error":"origin_not_allowed"})
        if len(self.path)>1800 or uri.fragment:
            return self.respond(400,{"error":"invalid_request"},origin)
        if not self.check_auth():
            return self.respond(401,{"error":"unauthorized"},origin)
        try:
            if uri.path=="/v2/projects":
                if uri.query: raise ValueError("Unexpected query")
                data=list_projects(self.server.projects)
            else:
                match=re.fullmatch(
                    r"/v2/projects/([a-z][a-z0-9_-]{1,39})/(manifest|records|record)",uri.path
                )
                if not match:
                    return self.respond(404,{"error":"route_not_found"},origin)
                slug,action=match.groups()
                project=self.server.projects.get(slug)
                if project is None:
                    return self.respond(404,{"error":"project_not_found"},origin)
                if action=="manifest":
                    if uri.query:raise ValueError("Unexpected query")
                    data=describe_project(project)
                else:
                    params=parse_qs(uri.query,keep_blank_values=True)
                    required={"sheet"} if action=="records" else {"sheet","id"}
                    allowed=({"sheet","limit","cursor","q","field","value"}
                             if action=="records" else {"sheet","id"})
                    if (not required.issubset(params) or set(params)-allowed or
                        any(len(values)!=1 for values in params.values())):
                        raise ValueError("Invalid query")
                    sheet=int(params["sheet"][0])
                    if action=="records":
                        kwargs={"limit":int(params.get("limit",["50"])[0]),
                                "cursor":params.get("cursor",[None])[0],
                                "q":params.get("q",[""])[0]}
                        if "field" in params:kwargs["field"]=int(params["field"][0])
                        if "value" in params:kwargs["value"]=params["value"][0]
                        data=read_page(project,self.server.token,sheet,**kwargs)
                    else:
                        data=read_record(project,sheet,params["id"][0])
        except (ValueError,TypeError,OverflowError,IndexError):
            return self.respond(400,{"error":"invalid_query"},origin)
        except KeyError:
            return self.respond(404,{"error":"record_or_sheet_not_found"},origin)
        except (sqlite3.Error,OSError):
            return self.respond(503,{"error":"source_unavailable"},origin)
        self.respond(200,data,origin)


class ProjectServer(FilmWikiServer):
    def __init__(self,address,projects,token):
        # Preserve legacy v1 against the first allowlisted project.
        first=next(iter(projects.values()))
        super().__init__(address,Handler,first["db"],first["source_sha"],token)
        self.projects=projects


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-root",type=Path,required=True)
    p.add_argument("--projects-file",type=Path,required=True)
    p.add_argument("--token-file",type=Path,required=True)
    p.add_argument("--port",type=int,default=8091)
    a=p.parse_args()
    if a.port<1024 or a.port>65535:
        p.error("Port must be 1024..65535")
    projects=read_registry(a.projects_file,a.data_root)
    first=next(iter(projects.values()))
    _,token=validate_startup(first["db"],a.data_root,a.token_file,first["source_sha"])
    with ProjectServer((HOST,a.port),projects,token) as server:
        print("FilmWiki v2 private API 127.0.0.1:"+str(a.port)+
              " project_count="+str(len(projects)),flush=True)
        server.serve_forever(poll_interval=0.5)


if __name__=="__main__":
    main()
