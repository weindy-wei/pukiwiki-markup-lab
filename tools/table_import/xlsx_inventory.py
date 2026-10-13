#!/usr/bin/env python3
"""Read-only XLSX inventory and image-reference discovery. Standard library only."""
import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

NS={"m":"http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r":"http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "p":"http://schemas.openxmlformats.org/package/2006/relationships",
    "xdr":"http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"}
def xml(z,name):
    return ET.fromstring(z.read(name))
def rels(z,path):
    if path.startswith("/"): path=path.lstrip("/")
    folder,name=posixpath.split(path)
    relpath=posixpath.join(folder,"_rels",name+".rels")
    if relpath not in z.namelist(): return {}
    out={}
    for r in xml(z,relpath):
        target=r.attrib.get("Target","")
        if r.attrib.get("TargetMode") == "External": continue
        out[r.attrib.get("Id")]=posixpath.normpath(
            target.lstrip("/") if target.startswith("/") else posixpath.join(folder,target))
    return out
def inspect_xlsx(path):
    report={"file":path.name,"sheets":[],"media":[],"warnings":[]}
    with zipfile.ZipFile(path) as z:
        names=set(z.namelist())
        if "xl/workbook.xml" not in names: raise ValueError("Not an XLSX workbook")
        wb=xml(z,"xl/workbook.xml"); lookup=rels(z,"xl/workbook.xml")
        sheets=wb.find("m:sheets",NS)
        for sh in sheets:
            rid=sh.attrib.get("{"+NS["r"]+"}id")
            part=lookup.get(rid)
            if not part or part not in names:
                report["warnings"].append("Missing worksheet reference: "+sh.attrib.get("name",""))
                continue
            root=xml(z,part)
            drawing_ids=[x.attrib.get("{"+NS["r"]+"}id") for x in root.findall("m:drawing",NS)]
            sheet={"name":sh.attrib["name"],"part":part,"row_count":len(root.findall(".//m:sheetData/m:row",NS)),"drawings":[]}
            links=rels(z,part)
            for did in drawing_ids:
                drawing=links.get(did)
                if not drawing or drawing not in names:
                    report["warnings"].append("Missing drawing for "+sheet["name"])
                    continue
                dr=xml(z,drawing); images=rels(z,drawing)
                for anchor in dr:
                    frm=anchor.find("xdr:from",NS)
                    loc=None
                    if frm is not None:
                        col=frm.find("xdr:col",NS);row=frm.find("xdr:row",NS)
                        if col is not None and row is not None:
                            loc={"row_1based":int(row.text)+1,"col_1based":int(col.text)+1}
                    for node in anchor.iter():
                        if node.tag.endswith("}blip"):
                            embed=node.attrib.get("{"+NS["r"]+"}embed")
                            media=images.get(embed)
                            sheet["drawings"].append({"anchor":loc,"media_part":media,"resolved":bool(media and media in names)})
            report["sheets"].append(sheet)
        for item in sorted(names):
            if item.startswith("xl/media/") and not item.endswith("/"):
                data=z.read(item)
                report["media"].append({"part":item,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest()})
        anchored={x["media_part"] for s in report["sheets"] for x in s["drawings"] if x["resolved"]}
        report["unlinked_media"]=[x["part"] for x in report["media"] if x["part"] not in anchored]
    return report
def main():
    p=argparse.ArgumentParser()
    p.add_argument("workbook",type=Path)
    p.add_argument("--output",type=Path,help="Optional inventory JSON path (never writes Wiki data)")
    a=p.parse_args()
    result=inspect_xlsx(a.workbook)
    data=json.dumps(result,ensure_ascii=False,indent=2)
    if a.output:
        if a.output.exists(): p.error("Output already exists; refusing overwrite")
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(data+"\n",encoding="utf-8")
    else: print(data)
if __name__=="__main__":main()
