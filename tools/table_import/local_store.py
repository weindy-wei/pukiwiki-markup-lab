#!/usr/bin/env python3
"""FilmWiki portable JSON preview -> SQLite local staging store.

Data owner chooses the .sqlite location. Does not access network or write Wiki pages.
Use --apply explicitly; without it the input is validated and only counts are printed.
"""
import argparse
import collections
import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE imports (
    source_sha256 TEXT PRIMARY KEY,
    source_filename TEXT NOT NULL,
    preview_sha256 TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    format TEXT NOT NULL
);
CREATE TABLE sheets (
    source_sha256 TEXT NOT NULL REFERENCES imports(source_sha256),
    sheet_index INTEGER NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (source_sha256,sheet_index),
    UNIQUE (source_sha256,name)
);
CREATE TABLE fields (
    source_sha256 TEXT NOT NULL,
    sheet_index INTEGER NOT NULL,
    column_index INTEGER NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (source_sha256,sheet_index,column_index),
    FOREIGN KEY (source_sha256,sheet_index) REFERENCES sheets(source_sha256,sheet_index)
);
CREATE TABLE records (
    record_id TEXT PRIMARY KEY,
    source_sha256 TEXT NOT NULL,
    sheet_index INTEGER NOT NULL,
    source_row INTEGER NOT NULL,
    shot_number_json TEXT NOT NULL,
    UNIQUE(source_sha256,sheet_index,source_row),
    FOREIGN KEY (source_sha256,sheet_index) REFERENCES sheets(source_sha256,sheet_index)
);
CREATE TABLE cells (
    record_id TEXT NOT NULL REFERENCES records(record_id),
    column_index INTEGER NOT NULL,
    value_json TEXT NOT NULL,
    is_null INTEGER NOT NULL CHECK (is_null IN (0,1)),
    PRIMARY KEY(record_id,column_index)
);
CREATE TABLE asset_references (
    ref_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES records(record_id),
    source_sha256 TEXT NOT NULL,
    filename TEXT NOT NULL,
    source_column INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status='unresolved')
);
CREATE TABLE shot_parent_links (
    child_record_id TEXT PRIMARY KEY REFERENCES records(record_id),
    parent_label TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('resolved','ambiguous','missing')),
    parent_record_id TEXT REFERENCES records(record_id),
    candidate_record_ids_json TEXT NOT NULL
);
"""

def stable_json(value):
    return json.dumps(value,ensure_ascii=False,separators=(",",":"),sort_keys=True,allow_nan=False)

def canonical_bytes(obj):
    return stable_json(obj).encode("utf-8")

def validate_preview(data):
    if not isinstance(data,dict) or data.get("format")!="filmwiki-import-preview":
        raise ValueError("Expected FilmWiki XLSX preview")
    source=data.get("source")
    if not isinstance(source,dict) or not isinstance(source.get("sha256"),str):
        raise ValueError("Missing source fingerprint")
    sha=source["sha256"]
    if len(sha)!=64 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("Invalid source SHA-256")
    if not isinstance(data.get("sheets"),list) or len(data["sheets"])>100:
        raise ValueError("Invalid sheets")
    sheets=[]
    record_ids=set()
    for si,sheet in enumerate(data["sheets"],1):
        name=sheet.get("name")
        fields=sheet.get("fields")
        records=sheet.get("records")
        if not isinstance(name,str) or not name or not isinstance(fields,list) or not isinstance(records,list):
            raise ValueError("Invalid worksheet")
        if len(fields)>256 or len(records)>200000:
            raise ValueError("Worksheet exceeds limits")
        indices=[f.get("index") for f in fields]
        if indices!=list(range(1,len(fields)+1)):
            raise ValueError("Field indices not sequential: "+name)
        if not all(isinstance(f.get("name"),str) for f in fields):
            raise ValueError("Invalid field names")
        rows_seen=set()
        for record in records:
            rid=record.get("record_id")
            row=record.get("source_row")
            cells=record.get("cells")
            if not isinstance(rid,str) or not rid.startswith("src_") or rid in record_ids:
                raise ValueError("Invalid or duplicate record ID")
            if type(row) is not int or row<2 or row in rows_seen:
                raise ValueError("Invalid or duplicate source row")
            if not isinstance(cells,list) or len(cells)!=len(fields):
                raise ValueError("Record cells do not match all fields")
            for col,(cell,field) in enumerate(zip(cells,fields),1):
                if cell.get("index")!=col or cell.get("field")!=field["name"]:
                    raise ValueError("Cell index or field mismatch")
                canonical_bytes(cell.get("value"))
            rows_seen.add(row);record_ids.add(rid)
        sheets.append((si,sheet))
    for ref in data.get("asset_references",[]):
        if not isinstance(ref,dict) or ref.get("record_id") not in record_ids:
            raise ValueError("Invalid asset reference record")
        if not isinstance(ref.get("filename"),str) or not ref["filename"]:
            raise ValueError("Invalid asset filename")
        if ref.get("status")!="unresolved":
            raise ValueError("Only unresolved source references can be imported here")
    return sheets

def relation_plan(sheets):
    """Resolve parent ONLY if target shot label is unique inside the same sheet."""
    links=[]
    for _,sheet in sheets:
        index=collections.defaultdict(list)
        for rec in sheet["records"]:
            label=rec.get("shot_number")
            if label not in (None,""):
                index[stable_json(label)].append(rec["record_id"])
        for rec in sheet["records"]:
            parent=rec.get("parent_shot")
            if parent in (None,""):
                continue
            candidates=index.get(stable_json(parent),[])
            status="resolved" if len(candidates)==1 else ("ambiguous" if candidates else "missing")
            links.append((rec["record_id"],str(parent),status,candidates[0] if len(candidates)==1 else None,candidates))
    return links

def summary(data):
    sheets=validate_preview(data)
    links=relation_plan(sheets)
    status=collections.Counter(x[2] for x in links)
    return {"source_file":data["source"].get("filename"),
            "sheets":len(sheets),
            "fields":sum(len(x["fields"]) for _,x in sheets),
            "records":sum(len(x["records"]) for _,x in sheets),
            "empty_sheets":[x["name"] for _,x in sheets if not x["records"]],
            "duplicate_shot_labels":[w["locations"] for w in data.get("warnings",[]) if w.get("issue")=="duplicate_shot_numbers"],
            "parent_links":dict(status),
            "asset_refs_unresolved":len(data.get("asset_references",[])),
            "all_columns_preserved":True,"all_nulls_preserved":True}

def store(data,db_path):
    sheets=validate_preview(data)
    links=relation_plan(sheets)
    db_path=Path(db_path)
    if db_path.exists() and not db_path.is_file():
        raise ValueError("Database path is not a file")
    if not db_path.parent.is_dir():
        raise ValueError("Parent directory must exist")
    preview_hash=hashlib.sha256(canonical_bytes(data)).hexdigest()
    source_sha=data["source"]["sha256"]
    conn=sqlite3.connect(str(db_path),timeout=10)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        with conn:
            conn.executescript(SCHEMA.replace("CREATE TABLE ","CREATE TABLE IF NOT EXISTS "))
            existing=conn.execute("SELECT preview_sha256 FROM imports WHERE source_sha256=?",(source_sha,)).fetchone()
            if existing:
                if existing[0]!=preview_hash:
                    raise ValueError("Same source fingerprint with different preview. Refusing overwrite.")
                return "already_imported"
            now=datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute("INSERT INTO imports VALUES (?,?,?,?,?)",
                         (source_sha,data["source"].get("filename","unknown"),preview_hash,now,data["format"]))
            for si,sheet in sheets:
                conn.execute("INSERT INTO sheets VALUES (?,?,?)",(source_sha,si,sheet["name"]))
                for field in sheet["fields"]:
                    conn.execute("INSERT INTO fields VALUES (?,?,?,?)",
                                 (source_sha,si,field["index"],field["name"]))
                for rec in sheet["records"]:
                    rid=rec["record_id"]
                    conn.execute("INSERT INTO records VALUES (?,?,?,?,?)",
                                 (rid,source_sha,si,rec["source_row"],stable_json(rec.get("shot_number"))))
                    conn.executemany("INSERT INTO cells VALUES (?,?,?,?)",
                                     [(rid,c["index"],stable_json(c["value"]),int(c["value"] is None))
                                      for c in rec["cells"]])
            for asset in data.get("asset_references",[]):
                conn.execute("INSERT INTO asset_references VALUES (?,?,?,?,?,?)",
                             (asset["ref_id"],asset["record_id"],source_sha,asset["filename"],asset["column"],"unresolved"))
            for child,label,status,parent,candidates in links:
                conn.execute("INSERT INTO shot_parent_links VALUES (?,?,?,?,?)",
                             (child,label,status,parent,stable_json(candidates)))
        check=conn.execute("PRAGMA integrity_check").fetchone()[0]
        if check!="ok":
            raise RuntimeError("Database integrity_check failed")
        return "imported"
    finally:
        conn.close()

def projection(db_path,source_sha,sheet_name,selected_fields):
    """Non-destructive view for Tabler; never truncates storage to eight columns."""
    conn=sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        item=conn.execute("SELECT sheet_index FROM sheets WHERE source_sha256=? AND name=?",
                          (source_sha,sheet_name)).fetchone()
        if not item: raise ValueError("Unknown sheet")
        si=item[0]
        fields=conn.execute("SELECT column_index,name FROM fields WHERE source_sha256=? AND sheet_index=? ORDER BY column_index",
                            (source_sha,si)).fetchall()
        name_to_indexes=collections.defaultdict(list)
        for n,label in fields:name_to_indexes[label].append(n)
        if len(selected_fields)>8 or len(selected_fields)<1:
            raise ValueError("Projection must select 1 to 8 columns")
        indices=[]
        for field in selected_fields:
            positions=name_to_indexes.get(field,[])
            if len(positions)!=1: raise ValueError("Missing or non-unique field "+field)
            indices.append(positions[0])
        rows=conn.execute("SELECT record_id,source_row FROM records WHERE source_sha256=? AND sheet_index=? ORDER BY source_row",
                          (source_sha,si)).fetchall()
        result=[]
        for rid,row_num in rows:
            cells=dict(conn.execute("SELECT column_index,value_json FROM cells WHERE record_id=?",(rid,)).fetchall())
            result.append({"record_id":rid,"source_row":row_num,
                           "values":[json.loads(cells[i]) for i in indices]})
        return {"sheet":sheet_name,"headers":selected_fields,"rows":result,
                "underlying_total_fields":len(fields),"projection_only":True}
    finally:
        conn.close()

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("preview",type=Path,help="Read-only XLSX preview JSON")
    ap.add_argument("--db",type=Path,help="User-owned SQLite file; created only with --apply")
    ap.add_argument("--apply",action="store_true",help="Opt in to local SQLite staging write")
    ap.add_argument("--project-sheet",help="Print non-destructive selected column view")
    ap.add_argument("--columns",help="Comma separated field names (up to 8)")
    args=ap.parse_args()
    data=json.loads(args.preview.read_text(encoding="utf-8"))
    report=summary(data)
    report["action"]="validated_only"
    if args.apply:
        if not args.db: ap.error("--apply requires --db")
        report["action"]=store(data,args.db)
        report["db"]=str(args.db)
    if args.project_sheet:
        if not args.db or not args.columns: ap.error("Projection requires --db and --columns")
        report["view"]=projection(args.db,data["source"]["sha256"],
                                  args.project_sheet,[x.strip() for x in args.columns.split(",")])
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
