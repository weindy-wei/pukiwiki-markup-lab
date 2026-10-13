#!/usr/bin/env python3
"""Read-only worksheet data preview for XLSX imports. Does not write Wiki pages."""
import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
D = "http://schemas.openxmlformats.org/drawingml/2006/main"
X = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
CELL = re.compile(r"^([A-Z]{1,4})([1-9]\d*)$")
LIMITS = {"source": 80*1024*1024, "entry": 24*1024*1024,
          "total": 256*1024*1024, "parts": 3000, "cells": 300000}

def parsed(z, name):
    return ET.fromstring(z.read(name))

def relations(z, part):
    folder, base = posixpath.split(part)
    rel_file = posixpath.join(folder, "_rels", base+".rels")
    if rel_file not in z.namelist():
        return {}
    result = {}
    for rel in parsed(z, rel_file):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target", "")
        if not target:
            continue
        resolved = posixpath.normpath(target[1:] if target.startswith("/")
                                      else posixpath.join(folder, target))
        if not resolved.startswith("../") and not resolved.startswith("/"):
            result[rel.get("Id")] = resolved
    return result

def colnum(code):
    value = 0
    for c in code:
        value = 26*value + ord(c)-64
    return value

def concatenated_text(node):
    return "".join(n.text or "" for n in node.iter() if n.tag == "{"+M+"}t")

def inspect(path, preview_rows=100):
    path = Path(path)
    if path.stat().st_size > LIMITS["source"]:
        raise ValueError("Workbook compressed size limit exceeded")
    report = {"status":"preview_only", "wiki_write":False, "filename":path.name,
              "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
              "sheets":[], "media":[], "warnings":[]}
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos)>LIMITS["parts"] or sum(x.file_size for x in infos)>LIMITS["total"]:
            raise ValueError("XLSX package exceeds safety limits")
        if any(x.file_size>LIMITS["entry"] or ".." in x.filename.split("/")
               or x.filename.startswith("/") or "\\" in x.filename for x in infos):
            raise ValueError("Unsafe XLSX entry")
        names = set(z.namelist())
        if "xl/workbook.xml" not in names:
            raise ValueError("Missing workbook.xml")
        strings = []
        if "xl/sharedStrings.xml" in names:
            for node in parsed(z,"xl/sharedStrings.xml").findall("{"+M+"}si"):
                if len(strings)>=100000:
                    raise ValueError("Too many shared strings")
                strings.append(concatenated_text(node))
        wb = parsed(z,"xl/workbook.xml")
        sheets_root = wb.find("{"+M+"}sheets")
        if sheets_root is None or len(sheets_root)>60:
            raise ValueError("Missing sheets or too many sheets")
        workbook_relations = relations(z,"xl/workbook.xml")
        for sheet_entry in sheets_root:
            name = sheet_entry.get("name","")
            part = workbook_relations.get(sheet_entry.get("{"+R+"}id"))
            if part not in names:
                report["warnings"].append(name+": missing worksheet")
                continue
            root = parsed(z,part)
            rows = []
            sheetdata = root.find("{"+M+"}sheetData")
            examined = 0
            if sheetdata is not None:
                for row in sheetdata.findall("{"+M+"}row"):
                    values = {}
                    for c in row.findall("{"+M+"}c"):
                        examined += 1
                        if examined>LIMITS["cells"]:
                            raise ValueError("Too many worksheet cells")
                        ref = c.get("r","")
                        match = CELL.fullmatch(ref)
                        if not match:
                            continue
                        typ = c.get("t")
                        v = c.find("{"+M+"}v")
                        val = v.text if v is not None and v.text else ""
                        if typ=="s":
                            try: val = strings[int(val)]
                            except (IndexError,ValueError):
                                report["warnings"].append(name+"!"+ref+": bad shared string")
                                val = ""
                        elif typ=="inlineStr":
                            node = c.find("{"+M+"}is")
                            val = concatenated_text(node) if node is not None else ""
                        elif typ=="b":
                            val = "TRUE" if val=="1" else "FALSE"
                        elif typ=="e":
                            report["warnings"].append(name+"!"+ref+": Excel cell error")
                        if c.find("{"+M+"}f") is not None and val=="":
                            report["warnings"].append(name+"!"+ref+": formula missing cached result")
                        if val:
                            values[str(colnum(match.group(1)))] = val
                    if values:
                        rows.append({"source_row":int(row.get("r","0")),"cells":values})
            preview = []
            unique_ids = set()
            for row in rows[:preview_rows]:
                stable_source = json.dumps([name, sorted(row["cells"].items())],ensure_ascii=False)
                rid = "imp_"+hashlib.sha256(stable_source.encode()).hexdigest()[:20]
                if rid in unique_ids:
                    rid += "_r"+str(row["source_row"])
                    report["warnings"].append(name+": duplicate-content row needs review")
                unique_ids.add(rid)
                preview.append({**row,"provisional_row_id":rid})
            draws = []
            rs = relations(z,part)
            for drref in root.findall("{"+M+"}drawing"):
                drawing = rs.get(drref.get("{"+R+"}id"))
                if drawing not in names:
                    continue
                media_refs = relations(z,drawing)
                for anchor in parsed(z,drawing):
                    start = anchor.find("{"+X+"}from")
                    position = None
                    if start is not None:
                        row = start.find("{"+X+"}row")
                        col = start.find("{"+X+"}col")
                        if row is not None and col is not None:
                            position = {"row":int(row.text)+1,"column":int(col.text)+1}
                    for node in anchor.iter():
                        if node.tag=="{"+D+"}blip":
                            draws.append({"candidate_cell":position,
                                          "media_part":media_refs.get(node.get("{"+R+"}embed")),
                                          "needs_review":True})
            report["sheets"].append({
                "name":name,"source_rows":len(rows),"preview_rows":preview,
                "truncated":len(rows)>preview_rows,"images":draws,
                "suggested_header": rows[0] if rows else None,
                "import_ready":False, "reason":"Field mapping and record ID review required"
            })
        for name in sorted(names):
            if name.startswith("xl/media/") and not name.endswith("/"):
                content=z.read(name)
                report["media"].append({"part":name,"sha256":hashlib.sha256(content).hexdigest(),
                                        "bytes":len(content)})
    return report

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx",type=Path)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--limit",type=int,default=100)
    args=parser.parse_args()
    if args.limit<1 or args.limit>1000:
        parser.error("limit must be 1..1000")
    output=json.dumps(inspect(args.xlsx,args.limit),ensure_ascii=False,indent=2)+"\n"
    if args.output:
        if args.output.exists():
            parser.error("Refusing to overwrite output")
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(output,encoding="utf-8")
    else:
        print(output)
if __name__=="__main__":
    main()
