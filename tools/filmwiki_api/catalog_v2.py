"""FilmWiki v2: allowlisted projects and keyset pagination over owner-local SQLite.

No dynamic SQL identifier, path or project source SHA is ever accepted via HTTP.
Reads only. Search is a bounded substring scan until a separate FTS index is built.
"""
from contextlib import closing
import base64
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3

from server import open_readonly, public_json, manifest

PROJECT_ID = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
MAX_PAGE = 100
MAX_SEARCH = 80
MAX_EXACT_VALUE = 160
MAX_CURSOR = 380


def read_registry(registry_file, data_root):
    root = Path(data_root).resolve(strict=True)
    registry = Path(registry_file).resolve(strict=True)
    if not root.is_dir() or not registry.is_file() or not registry.is_relative_to(root):
        raise ValueError("Project registry must be a private file in local data root")
    raw = registry.read_bytes()
    if len(raw) > 32*1024:
        raise ValueError("Project registry too large")
    document = json.loads(raw)
    if (set(document) != {"version","projects"} or document["version"] != 1 or
            not isinstance(document["projects"],list) or len(document["projects"])>32):
        raise ValueError("Invalid registry structure")
    out = {}
    for p in document["projects"]:
        if not isinstance(p,dict) or set(p)!={"id","title","db","source_sha"}:
            raise ValueError("Invalid project entry")
        slug,title,relative,sha=p["id"],p["title"],p["db"],p["source_sha"]
        if not isinstance(slug,str) or not PROJECT_ID.fullmatch(slug) or slug in out:
            raise ValueError("Duplicate/invalid project id")
        if not isinstance(title,str) or not 1<=len(title)<=80:
            raise ValueError("Invalid project title")
        if not isinstance(relative,str) or len(relative)>240 or Path(relative).is_absolute():
            raise ValueError("Invalid project db relative path")
        if "\\" in relative or any(part in (".","..","") for part in relative.split("/")):
            raise ValueError("Unsafe database path")
        if not isinstance(sha,str) or not re.fullmatch(r"[0-9a-f]{64}",sha):
            raise ValueError("Invalid source fingerprint")
        path=(root/relative).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file() or path.suffix!=".sqlite":
            raise ValueError("Project SQLite outside local data root")
        project_manifest=manifest(path,sha) # validate every configured project before listen
        out[slug]={"id":slug,"title":title,"db":path,"source_sha":sha,
                   "manifest":project_manifest}
    if not out:
        raise ValueError("No projects registered")
    return out


def list_projects(projects):
    return {"format":"filmwiki-project-list","version":2,"read_only":True,
            "projects":[{"id":p["id"],"title":p["title"],
                         "sheet_count":len(p["manifest"]["sheets"])}
                        for p in projects.values()]}


def describe_project(project):
    return {"format":"filmwiki-project-manifest","version":2,
            "project":{"id":project["id"],"title":project["title"]},
            **project["manifest"]}


def _signature(secret,payload):
    return hmac.new(secret.encode("utf-8"),payload,hashlib.sha256).digest()[:20]


def _encode_cursor(secret,payload):
    data=json.dumps(payload,separators=(",",":"),ensure_ascii=True).encode("ascii")
    token=base64.urlsafe_b64encode(data+_signature(secret,data)).decode("ascii").rstrip("=")
    if len(token)>MAX_CURSOR:raise ValueError("Cursor too long")
    return token


def _decode_cursor(secret,cursor,context):
    if not isinstance(cursor,str) or not cursor or len(cursor)>MAX_CURSOR or not re.fullmatch("[A-Za-z0-9_-]+",cursor):
        raise ValueError("Invalid cursor")
    try:
        decoded=base64.urlsafe_b64decode(cursor+"="*((-len(cursor))%4))
        raw,sig=decoded[:-20],decoded[-20:]
        if len(decoded)<21 or not hmac.compare_digest(sig,_signature(secret,raw)):
            raise ValueError("Bad signature")
        payload=json.loads(raw)
        if payload.get("context")!=context or type(payload.get("last")) is not int or payload["last"]<1:
            raise ValueError("Wrong cursor context")
        return payload["last"]
    except (ValueError,TypeError,UnicodeDecodeError,OverflowError,KeyError) as exc:
        raise ValueError("Bad cursor") from exc


def _search_context(project_id,sheet,q,field,value):
    return hashlib.sha256(
        json.dumps([project_id,sheet,q,field,value],ensure_ascii=False,separators=(",",":")).encode("utf-8")
    ).hexdigest()[:20]


def _materialize(con,rows,fields,sha):
    result,assets=[],[]
    if not rows:return result,assets
    ids=[r["record_id"] for r in rows]
    marks=",".join("?" for _ in ids)
    cells={}
    for c in con.execute(
        "SELECT record_id,column_index,value_json FROM cells WHERE record_id IN ("+marks+")",ids
    ):
        cells.setdefault(c["record_id"],{})[c["column_index"]]=json.loads(c["value_json"])
    parents={}
    for p in con.execute(
        "SELECT child_record_id,parent_label,status,parent_record_id,candidate_record_ids_json "
        "FROM shot_parent_links WHERE child_record_id IN ("+marks+")",ids
    ):
        parents[p["child_record_id"]]={
            "source_label":p["parent_label"],"status":p["status"],
            "parent_record_id":p["parent_record_id"],
            "candidate_record_ids":json.loads(p["candidate_record_ids_json"])
        }
    for a in con.execute(
        "SELECT ref_id,record_id,filename,source_column,status FROM asset_references "
        "WHERE source_sha256=? AND record_id IN ("+marks+") ORDER BY ref_id",[sha,*ids]
    ):
        assets.append(dict(a))
    for r in rows:
        rid=r["record_id"]
        if len(cells.get(rid,{}))!=len(fields):
            raise ValueError("Incomplete record")
        result.append({
            "record_id":rid,"source_row":r["source_row"],
            "shot_number":json.loads(r["shot_number_json"]),
            "cells":[{"index":f["column_index"],"field":f["name"],
                      "value":cells[rid][f["column_index"]]} for f in fields],
            "parent_link":parents.get(rid)
        })
    return result,assets


def read_page(project,secret,sheet,limit=50,cursor=None,q="",field=None,value=None):
    if type(sheet) is not int or not 1<=sheet<=100 or type(limit) is not int or not 1<=limit<=MAX_PAGE:
        raise ValueError("Invalid sheet/limit")
    if not isinstance(q,str) or len(q)>MAX_SEARCH or "\x00" in q:
        raise ValueError("Invalid search query")
    if field is None and value is not None or field is not None and value is None:
        raise ValueError("Field and value must be paired")
    if field is not None and (type(field) is not int or not 1<=field<=256 or
                              not isinstance(value,str) or len(value)>MAX_EXACT_VALUE or "\x00" in value):
        raise ValueError("Invalid field filter")
    context=_search_context(project["id"],sheet,q,field,value)
    after=1 if cursor is None else _decode_cursor(secret,cursor,context)
    with closing(open_readonly(project["db"])) as con:
        fields=con.execute(
            "SELECT column_index,name FROM fields WHERE source_sha256=? AND sheet_index=? ORDER BY column_index",
            (project["source_sha"],sheet)).fetchall()
        check=con.execute(
            "SELECT name FROM sheets WHERE source_sha256=? AND sheet_index=?",
            (project["source_sha"],sheet)).fetchone()
        if check is None:
            raise KeyError("Unknown sheet")
        if field is not None and field not in {x["column_index"] for x in fields}:
            raise ValueError("Unknown field")
        params=[project["source_sha"],sheet,after]
        sql=("SELECT r.record_id,r.source_row,r.shot_number_json FROM records r "
             "WHERE r.source_sha256=? AND r.sheet_index=? AND r.source_row>? ")
        # SQLite JSON is generated by Python and not externally evaluated.
        # User substring is a bound parameter, never interpolated into SQL.
        if q:
            sql+=("AND EXISTS (SELECT 1 FROM cells c WHERE c.record_id=r.record_id "
                  "AND instr(c.value_json,?)>0) ")
            params.append(q)
        if field is not None:
            sql+=("AND EXISTS (SELECT 1 FROM cells e WHERE e.record_id=r.record_id "
                  "AND e.column_index=? AND e.value_json=?) ")
            params.extend([field,json.dumps(value,ensure_ascii=False,separators=(",",":"))])
        sql+="ORDER BY r.source_row LIMIT ?"
        params.append(limit+1)
        found=con.execute(sql,params).fetchall()
        has_more=len(found)>limit
        selected=found[:limit]
        records,assets=_materialize(con,selected,fields,project["source_sha"])
        next_cursor=(_encode_cursor(secret,{"context":context,"last":selected[-1]["source_row"]})
                     if has_more and selected else None)
        return {"format":"filmwiki-record-page","version":2,
                "project_id":project["id"],"sheet_index":sheet,"name":check["name"],
                "query":q,"filter_field":field,"filter_value":value,
                "limit":limit,"records":records,"asset_references":assets,
                "next_cursor":next_cursor,"has_more":has_more,
                "count":len(records),
                "note":"Full-column data on this page only. Substring search is not yet FTS-indexed."}


def read_record(project,sheet,record_id):
    if type(sheet) is not int or not 1<=sheet<=100:
        raise ValueError("Invalid sheet")
    if not isinstance(record_id,str) or len(record_id)>100 or not re.fullmatch(r"src_[a-zA-Z0-9_-]+",record_id):
        raise ValueError("Invalid record id")
    with closing(open_readonly(project["db"])) as con:
        fields=con.execute(
            "SELECT column_index,name FROM fields WHERE source_sha256=? AND sheet_index=? ORDER BY column_index",
            (project["source_sha"],sheet)).fetchall()
        row=con.execute(
            "SELECT record_id,source_row,shot_number_json FROM records "
            "WHERE source_sha256=? AND sheet_index=? AND record_id=?",
            (project["source_sha"],sheet,record_id)).fetchone()
        if row is None:
            raise KeyError("Record not found")
        records,assets=_materialize(con,[row],fields,project["source_sha"])
        return {"format":"filmwiki-record-detail","version":2,
                "project_id":project["id"],"sheet_index":sheet,
                "record":records[0],"asset_references":assets}
