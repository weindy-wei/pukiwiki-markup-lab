"""FilmWiki FTS5 trigram / typed-field index tests: source DB stays untouched."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"tools/filmwiki_api"))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"tools/table_import"))
from local_store import store
from catalog_v2 import read_page
from catalog_v3 import read_indexed_page,attach_validated_indices
from search_index import build_index,validate_index
from server_v2 import ProjectServer
from catalog_v2 import read_registry
import json

SHA="e"*64
TOKEN="h"*64

def fixture(count=250):
    fields=["镜头号","镜头内容","预算数值","状态","场景图片"]
    rows=[]
    for i in range(count):
        values=[
            "SC_06" if i<2 else "SC_"+str(i).zfill(5),
            "路灯下的人物抬头对白" if i%7==0 else
              ('他说："你好吗？" 特殊%_号' if i==15 else
              ("AlphaSC_06特写" if i==17 else "室内日常镜头"+str(i))),
            (i if i%2==0 else None),
            "制作中" if i%3==0 else "待审核",
            "image.png" if i==11 else None
        ]
        rows.append({
          "record_id":"src_ftstest_"+str(i).zfill(6),"source_row":i+2,
          "shot_number":values[0],"cells":[{"index":j+1,"field":fields[j],"value":v} for j,v in enumerate(values)]
        })
    return {"format":"filmwiki-import-preview",
            "source":{"filename":"artificial-fts.xlsx","sha256":SHA},
            "sheets":[{"name":"第1集","fields":[{"index":i+1,"name":n} for i,n in enumerate(fields)],"records":rows},
                      {"name":"道具设定","fields":[{"index":1,"name":"道具名称"}],"records":[]}],
            "asset_references":[{"ref_id":"assetref_one","record_id":rows[11]["record_id"],
                                  "filename":"image.png","column":5,"status":"unresolved"}]}

class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.db=self.root/"canon.sqlite"
        self.index_root=self.root/"indexes"
        self.index_root.mkdir()
        self.index=self.index_root/"demo.search.sqlite"
        store(fixture(),self.db)
        self.old=self.db.read_bytes()
        self.report=build_index(self.db,SHA,"demo",self.index,self.root)
        self.assertEqual(self.db.read_bytes(),self.old)
        self.assertTrue(validate_index(self.db,SHA,"demo",self.index))
        self.project={"id":"demo","title":"合成","db":self.db,"source_sha":SHA,"index_db":self.index}

    def tearDown(self):self.temp.cleanup()

    def test_sidecar_counts_and_fts_integrity(self):
        self.assertEqual(self.report["records"],250)
        self.assertEqual(self.report["indexed_cells"],1250)
        with sqlite3.connect(self.index) as con:
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            self.assertEqual(con.execute("SELECT COUNT(*) FROM cell_docs").fetchone()[0],1250)
            self.assertEqual(con.execute("SELECT count(*) FROM exact_fields WHERE value_type='number'").fetchone()[0],125)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM cell_fts WHERE cell_fts MATCH '\"路灯下\"'").fetchone()[0],36)

    def test_fts_long_chinese_and_punctuation_match_legacy(self):
        for term in ("路灯下","对白","你好吗","特殊%_","AlphaSC_06","\"你好吗","image.png","不存在"):
            old=read_page(self.project,TOKEN,1,q=term,limit=100)
            indexed=read_indexed_page(self.project,TOKEN,1,q=term,limit=100)
            self.assertEqual([x["record_id"] for x in indexed["records"]],
                             [x["record_id"] for x in old["records"]],term)
            if len(term)>=3:self.assertIn("fts5-trigram",indexed["search_backend"])
        for term in ("人物","路","SC"):
            indexed=read_indexed_page(self.project,TOKEN,1,q=term,limit=100)
            old=read_page(self.project,TOKEN,1,q=term,limit=100)
            self.assertEqual([x["record_id"] for x in indexed["records"]],
                             [x["record_id"] for x in old["records"]],term)
            if len(term)<3:self.assertIn("short-query-scan",indexed["search_backend"])

    def test_exact_field_and_numeric_range(self):
        exact=read_indexed_page(self.project,TOKEN,1,field=4,value="制作中",limit=100)
        self.assertTrue(exact["records"])
        self.assertTrue(all(row["cells"][3]["value"]=="制作中" for row in exact["records"]))
        numeric=read_indexed_page(self.project,TOKEN,1,field=3,value="200",kind="number",op="ge")
        self.assertEqual(numeric["count"],25)
        self.assertTrue(all(row["cells"][2]["value"]>=200 for row in numeric["records"]))
        self.assertEqual(read_indexed_page(self.project,TOKEN,1,field=3,value="200",kind="number",op="lt")["count"],100)
        self.assertEqual(read_indexed_page(self.project,TOKEN,1,field=3,value="200",kind="number",op="eq")["count"],1)
        with self.assertRaises(ValueError):
            read_indexed_page(self.project,TOKEN,1,field=4,value="abc",kind="number",op="ge")

    def test_cursor_query_binding_and_all_records_once(self):
        cursor=None;ids=[]
        while True:
            page=read_indexed_page(self.project,TOKEN,1,limit=31,cursor=cursor)
            ids += [r["record_id"] for r in page["records"]]
            cursor=page["next_cursor"]
            if not cursor:break
        self.assertEqual(len(ids),250)
        self.assertEqual(len(set(ids)),250)
        qpage=read_indexed_page(self.project,TOKEN,1,q="镜头",limit=3)
        with self.assertRaises(ValueError):
            read_indexed_page(self.project,TOKEN,1,q="灯下",limit=3,cursor=qpage["next_cursor"])

    def test_zero_record_sheet_and_index_stale_rejected(self):
        empty=read_indexed_page(self.project,TOKEN,2,q="灯下")
        self.assertEqual(empty["records"],[])
        with self.assertRaises(FileExistsError):
            build_index(self.db,SHA,"demo",self.index,self.root)
        with self.assertRaises(ValueError):
            build_index(self.db,SHA,"demo",self.root/"../escape.sqlite",self.root)
        os.utime(self.db,ns=(self.db.stat().st_atime_ns,self.db.stat().st_mtime_ns+1000000))
        with self.assertRaises(ValueError):
            validate_index(self.db,SHA,"demo",self.index)
        self.assertEqual(self.db.read_bytes(),self.old)

if __name__=="__main__":
    unittest.main()
