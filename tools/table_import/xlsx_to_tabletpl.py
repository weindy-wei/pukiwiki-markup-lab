#!/usr/bin/env python3
"""Create a reviewed, local-only tabletpl v2 JSON candidate from a simple XLSX sheet."""
import argparse
import json
from pathlib import Path
import re
import sys
import unicodedata
from xlsx_preview import inspect

ALLOWED_PUNCT = set(".,!?:;/_+-（）【】《》，。！？：；、·%")
BAD_PREFIX = re.compile(r"^(LEFT|RIGHT|CENTER|BOLD|COLOR|BGCOLOR|SIZE):",re.I)
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

def acceptable(value):
    if not isinstance(value,str) or not value or len(value.encode("utf-8"))>240:
        return False
    if BAD_PREFIX.match(value): return False
    for char in value:
        cat = unicodedata.category(char)
        if cat[0] in ("L","M","N") or cat=="Zs" or char in ALLOWED_PUNCT:
            continue
        return False
    return True

def convert(preview, sheet_name, template_id):
    if not IDENTIFIER.fullmatch(template_id):
        raise ValueError("Invalid template ID")
    matching=[s for s in preview["sheets"] if s["name"]==sheet_name]
    if len(matching)!=1: raise ValueError("Unknown or ambiguous worksheet")
    sheet=matching[0]
    if sheet["truncated"]:
        raise ValueError("Worksheet preview truncated; cannot convert incomplete records")
    rows=sheet["preview_rows"]
    if len(rows)<2: raise ValueError("Worksheet has no record rows")
    header=rows[0]["cells"]
    columns=max(int(x) for row in rows for x in row["cells"])
    if not 2<=columns<=8: raise ValueError("Expected 2..8 populated columns")
    headers=[header.get(str(n),"") for n in range(1,columns+1)]
    if not all(acceptable(v) for v in headers):
        raise ValueError("Invalid or missing column heading")
    records=[]
    for row in rows[1:]:
        values=[row["cells"].get(str(n),"") for n in range(1,columns+1)]
        if not all(acceptable(v) for v in values):
            raise ValueError("Blank or unsafe data at source row "+str(row["source_row"]))
        record={"row_id":row["provisional_row_id"]}
        record.update({"c"+str(n):v for n,v in enumerate(values,1)})
        records.append(record)
    if len(records)>100: raise ValueError("More than 100 records")
    schema={"id":template_id,"version":2,"columns":columns,"headers":headers,
            "fields":[{"id":"c"+str(n),"type":"text"} for n in range(1,columns+1)],
            "records":records}
    return schema

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx",type=Path)
    parser.add_argument("--sheet",required=True)
    parser.add_argument("--template-id",required=True)
    parser.add_argument("--output",required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; refusing overwrite")
    manifest=inspect(args.xlsx,preview_rows=101)
    schema=convert(manifest,args.sheet,args.template_id)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(schema,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"candidate_only","wiki_write":False,
                      "records":len(schema["records"]),"output":str(args.output)},ensure_ascii=False))

if __name__=="__main__":
    main()
