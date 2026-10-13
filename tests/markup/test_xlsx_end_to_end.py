"""End-to-end offline XLSX ZIP -> normalized preview -> SQLite -> open JSON."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from xml.sax.saxutils import escape

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/table_import"))
from xlsx_preview import inspect
from normalize_preview import transform
from local_store import store,projection
from export_store import export_snapshot

NS="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE="http://schemas.openxmlformats.org/package/2006/relationships"

def cell(ref,text):
    return '<c r="'+ref+'" t="inlineStr"><is><t>'+escape(text)+'</t></is></c>'

def excel_col(n):
    out=""
    while n:
        n,r=divmod(n-1,26)
        out=chr(65+r)+out
    return out

def make_workbook(path):
    headers=["镜头号"]+["字段"+str(n) for n in range(2,17)]+["父记录"]
    worksheet=['<worksheet xmlns="'+NS+'"><sheetData>',
               '<row r="1">'+''.join(cell(excel_col(n)+"1",h) for n,h in enumerate(headers,1))+'</row>']
    for i,shot,parent in [(2,"SC_06",None),(3,"SC_06",None),(4,"SC_06_01","SC_06")]:
        entries=[cell("A"+str(i),shot)]
        if i==2:
            entries.append(cell("B2","image.png"))
        if parent:
            entries.append(cell("Q"+str(i),parent))
        worksheet.append('<row r="'+str(i)+'">'+''.join(entries)+'</row>')
    worksheet.append("</sheetData></worksheet>")
    empty='<worksheet xmlns="'+NS+'"><sheetData><row r="1">'+cell("A1","道具名称")+cell("B1","道具资产")+'</row></sheetData></worksheet>'
    wb='<workbook xmlns="'+NS+'" xmlns:r="'+REL+'"><sheets><sheet name="第1集" sheetId="1" r:id="rId1"/><sheet name="道具设定" sheetId="2" r:id="rId2"/></sheets></workbook>'
    rels='<Relationships xmlns="'+PACKAGE+'">'+''.join('<Relationship Id="rId'+str(i)+'" Type="'+REL+'/worksheet" Target="worksheets/sheet'+str(i)+'.xml"/>' for i in (1,2))+'</Relationships>'
    with zipfile.ZipFile(path,"w") as z:
        z.writestr("xl/workbook.xml",wb)
        z.writestr("xl/_rels/workbook.xml.rels",rels)
        z.writestr("xl/worksheets/sheet1.xml","".join(worksheet))
        z.writestr("xl/worksheets/sheet2.xml",empty)

class EndToEndTests(unittest.TestCase):
    def test_full_width_and_ambiguous_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            xlsx=Path(folder)/"fixture.xlsx"
            db=Path(folder)/"private.sqlite"
            make_workbook(xlsx)
            before=xlsx.read_bytes()
            preview=transform(inspect(xlsx,preview_rows=100))
            self.assertEqual([s["name"] for s in preview["sheets"]],["第1集","道具设定"])
            self.assertEqual(preview["sheets"][0]["column_count"],17)
            self.assertEqual(len(preview["sheets"][0]["records"]),3)
            self.assertEqual(len(preview["sheets"][1]["records"]),0)
            self.assertEqual(preview["asset_references"][0]["filename"],"image.png")
            self.assertEqual(store(preview,db),"imported")
            snapshot=export_snapshot(db,preview["source"]["sha256"])
            self.assertEqual(len(snapshot["sheets"][0]["fields"]),17)
            self.assertEqual(len(snapshot["sheets"][1]["records"]),0)
            self.assertEqual(snapshot["sheets"][0]["records"][0]["cells"][1]["value"],"image.png")
            self.assertIsNone(snapshot["sheets"][0]["records"][1]["cells"][1]["value"])
            self.assertEqual(snapshot["sheets"][0]["records"][2]["parent_link"]["status"],"ambiguous")
            view=projection(db,preview["source"]["sha256"],"第1集",["镜头号","父记录"])
            self.assertEqual(view["headers"],["镜头号","父记录"])
            self.assertEqual(len(view["rows"]),3)
            self.assertEqual(xlsx.read_bytes(),before)
            con=sqlite3.connect(db)
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            con.close()

if __name__=="__main__":
    unittest.main()
