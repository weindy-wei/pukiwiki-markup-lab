#!/usr/bin/env python3
"""Export a local FilmWiki SQLite staging snapshot as portable, lossless JSON."""
import argparse
import datetime
import json
from pathlib import Path
import sqlite3

def export_snapshot(db_path,source_sha):
    con=sqlite3.connect("file:"+str(Path(db_path).resolve())+"?mode=ro",uri=True)
    try:
        con.row_factory=sqlite3.Row
        imp=con.execute("SELECT * FROM imports WHERE source_sha256=?",(source_sha,)).fetchone()
        if imp is None: raise ValueError("Unknown source SHA-256")
        sheets=[]
        for s in con.execute("SELECT sheet_index,name FROM sheets WHERE source_sha256=? ORDER BY sheet_index",(source_sha,)):
            si=s["sheet_index"]
            fields=[{"index":f["column_index"],"name":f["name"]} for f in con.execute(
                "SELECT column_index,name FROM fields WHERE source_sha256=? AND sheet_index=? ORDER BY column_index",
                (source_sha,si))]
            recs=[]
            for r in con.execute("SELECT record_id,source_row,shot_number_json FROM records WHERE source_sha256=? AND sheet_index=? ORDER BY source_row",
                                 (source_sha,si)).fetchall():
                values={c["column_index"]:json.loads(c["value_json"]) for c in con.execute(
                    "SELECT column_index,value_json FROM cells WHERE record_id=?",
                    (r["record_id"],)).fetchall()}
                if len(values)!=len(fields):
                    raise ValueError("Record field count mismatch")
                link=con.execute(
                    "SELECT parent_label,status,parent_record_id,candidate_record_ids_json FROM shot_parent_links WHERE child_record_id=?",
                    (r["record_id"],)).fetchone()
                recs.append({
                    "record_id":r["record_id"],"source_row":r["source_row"],
                    "shot_number":json.loads(r["shot_number_json"]),
                    "cells":[{"index":f["index"],"field":f["name"],"value":values[f["index"]]} for f in fields],
                    "parent_link":None if link is None else {
                        "source_label":link["parent_label"],"status":link["status"],
                        "parent_record_id":link["parent_record_id"],
                        "candidate_record_ids":json.loads(link["candidate_record_ids_json"])
                    }
                })
            sheets.append({"name":s["name"],"fields":fields,"records":recs})
        assets=[dict(x) for x in con.execute(
            "SELECT ref_id,record_id,filename,source_column,status FROM asset_references WHERE source_sha256=? ORDER BY ref_id",
            (source_sha,))]
        return {"format":"filmwiki-portable-export","version":1,
                "source":{"filename":imp["source_filename"],"sha256":source_sha,
                          "preview_sha256":imp["preview_sha256"]},
                "sheets":sheets,"asset_references":assets,
                "note":"Preserves original values including nulls, empty sheets, duplicate labels and unresolved asset names."}
    finally:
        con.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("database",type=Path)
    p.add_argument("--source-sha",required=True)
    p.add_argument("--output",required=True,type=Path)
    a=p.parse_args()
    if a.output.exists():
        p.error("Refusing to overwrite an existing export")
    snapshot=export_snapshot(a.database,a.source_sha)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(snapshot,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({"exported":str(a.output),"sheets":len(snapshot["sheets"]),
        "records":sum(len(s["records"]) for s in snapshot["sheets"]),
        "wiki_writes":0},ensure_ascii=False))

if __name__=="__main__":
    main()
