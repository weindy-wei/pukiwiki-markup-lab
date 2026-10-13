#!/usr/bin/env python3
"""Turn the repository's XLSX OOXML read-only inventory into canonical FilmWiki JSON.

Does not infer media matches or alter source workbook. This is a staged import:
the result must be reviewed before writing to owner-controlled SQLite.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
from xlsx_preview import inspect

EXTS=(".png",".jpg",".jpeg",".webp",".gif",".bmp",".svg",".tiff",".tif")
MAX_COLS=256
MAX_RECORDS=200000

def transform(inventory):
    if not isinstance(inventory,dict) or not isinstance(inventory.get("sha256"),str):
        raise ValueError("Invalid XLSX inventory")
    source_sha=inventory["sha256"]
    result={"format":"filmwiki-import-preview","version":"0.1",
            "source":{"filename":inventory["filename"],"sha256":source_sha},
            "write_mode":"none","sheets":[],"asset_references":[],"warnings":[],
            "embedded_media":inventory.get("media",[])}
    for sheet in inventory.get("sheets",[]):
        name=sheet["name"]
        if sheet.get("truncated"):
            raise ValueError("Refusing incomplete worksheet preview: "+name)
        rows=sheet.get("preview_rows",[])
        if sheet.get("source_rows",0)!=len(rows):
            raise ValueError("Preview row count mismatch: "+name)
        if len(rows)>MAX_RECORDS+1:
            raise ValueError("Too many source rows")
        # Headers are the first non-empty XML worksheet row, not necessarily row 1.
        if rows:
            raw_headers=rows[0]["cells"]
            max_col=max((int(k) for row in rows for k in row["cells"]),default=0)
        else:
            raw_headers={}
            max_col=0
        if max_col>MAX_COLS:
            raise ValueError("Too many columns: "+name)
        headings=[]
        for col in range(1,max_col+1):
            value=raw_headers.get(str(col))
            if not isinstance(value,str) or not value.strip():
                # Preserve missing original heading in source_heading; stable
                # placeholder prevents collision and retains column position.
                field_name="__column_"+str(col)
                result["warnings"].append({"sheet":name,"issue":"blank_header",
                                           "column":col,"source_heading":value})
            else:
                field_name=value
            headings.append(field_name)
        fields=[{"index":i+1,"name":h} for i,h in enumerate(headings)]
        records=[]
        for row in rows[1:]:
            row_number=row["source_row"]
            rid="src_"+hashlib.sha256((source_sha+"\x00"+name+"\x00"+str(row_number)).encode()).hexdigest()[:24]
            cells=[{"index":i+1,"field":headings[i],"value":row["cells"].get(str(i+1))}
                   for i in range(max_col)]
            record={"record_id":rid,"source_row":row_number,"cells":cells}
            if "镜头号" in headings:
                record["shot_number"]=cells[headings.index("镜头号")]["value"]
            if "父记录" in headings:
                record["parent_shot"]=cells[headings.index("父记录")]["value"]
            records.append(record)
            for idx,cell in enumerate(cells,1):
                filename=cell["value"]
                if isinstance(filename,str) and filename.strip().lower().endswith(EXTS):
                    ref_id="assetref_"+hashlib.sha256((rid+"-"+str(idx)).encode()).hexdigest()[:24]
                    result["asset_references"].append({
                        "ref_id":ref_id,"record_id":rid,"sheet":name,
                        "row":row_number,"column":idx,"field":headings[idx-1],
                        "filename":filename,"status":"unresolved",
                        "match_method":None,
                        "reason":"Filename is not a unique asset identifier"
                    })
        result["sheets"].append({"name":name,"column_count":max_col,
                                  "row_count":len(records),"fields":fields,
                                  "records":records,
                                  "source_header_row":rows[0]["source_row"] if rows else None,
                                  "display_column_limit":8})
        if max_col>8:
            result["warnings"].append({"sheet":name,"issue":"more_than_8_columns",
                                       "field_count":max_col})
        if not records:
            result["warnings"].append({"sheet":name,"issue":"empty_sheet_preserved"})
        if "镜头号" in headings:
            count=collections.Counter(r.get("shot_number") for r in records if r.get("shot_number") not in ("",None))
            dup={str(label):[r["source_row"] for r in records if r.get("shot_number")==label]
                 for label,n in count.items() if n>1}
            if dup:
                result["warnings"].append({"sheet":name,"issue":"duplicate_shot_numbers","locations":dup})
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("xlsx",type=Path)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--limit",type=int,default=100000,
                   help="Maximum preview rows per sheet, including header")
    a=p.parse_args()
    if a.limit<1 or a.limit>MAX_RECORDS+1:
        p.error("Invalid --limit")
    if a.output.exists():
        p.error("Refusing to overwrite output")
    raw=inspect(a.xlsx,preview_rows=a.limit)
    converted=transform(raw)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(converted,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"sheets":len(converted["sheets"]),"records":sum(len(s["records"]) for s in converted["sheets"]),
                      "unresolved_assets":len(converted["asset_references"]),"wiki_write":False,
                      "output":str(a.output)},ensure_ascii=False))
if __name__=="__main__":
    main()
