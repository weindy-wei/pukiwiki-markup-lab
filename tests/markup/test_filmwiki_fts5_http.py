"""FTS5 v3 HTTP API tests using ephemeral owner-local synthetic SQLite fixtures."""
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/filmwiki_api"))
sys.path.insert(0,str(ROOT/"tools/table_import"))
from local_store import store
from search_index import build_index
from catalog_v3 import attach_validated_indices
from server_v3 import ProjectServer

SHA="c"*64
TOKEN="s"*64
ORIGIN="http://127.0.0.1:8080"

class IndexedHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.tmp.name)
        (cls.root/"indexes").mkdir()
        cols=["镜头号","文字","预算","状态"]
        rows=[]
        for j in range(310):
            cells=[f"SC_{j:05d}","角色靠近街角中文场景" if j%5==0 else "室内测试",
                   j if j%2==0 else None,"待审核" if j%3==0 else "制作中"]
            rows.append({"record_id":f"src_idx_{j:05d}","source_row":j+2,"shot_number":cells[0],
                         "cells":[{"index":i+1,"field":cols[i],"value":cells[i]} for i in range(4)]})
        data={"format":"filmwiki-import-preview","source":{"filename":"synthetic-index.xlsx","sha256":SHA},
              "sheets":[{"name":"合成分镜","fields":[{"index":i+1,"name":n} for i,n in enumerate(cols)],
                         "records":rows}],"asset_references":[]}
        cls.db=cls.root/"fixture.sqlite"
        store(data,cls.db)
        idx=cls.root/"indexes"/"demo.search.sqlite"
        build_index(cls.db,SHA,"demo",idx,cls.root)
        project={"id":"demo","title":"虚构测试","db":cls.db,"source_sha":SHA,"manifest":{
            "format":"filmwiki-read-api-manifest","version":1,
            "source":{"sha256":SHA,"filename":"synthetic-index.xlsx"},
            "sheets":[{"name":"合成分镜","fields":[{"index":i+1,"name":n} for i,n in enumerate(cols)],
                       "sheet_index":1,"record_count":310}]}}
        cls.projects=attach_validated_indices({"demo":project},cls.root/"indexes",cls.root)
        cls.server=ProjectServer(("127.0.0.1",0),cls.projects,TOKEN)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base="http://127.0.0.1:"+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=3);cls.tmp.cleanup()

    def get(self,route,token=TOKEN,origin=ORIGIN,method="GET"):
        headers={"Origin":origin}
        if token is not None:headers["Authorization"]="Bearer "+token
        req=urllib.request.Request(self.base+route,headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=5) as r:return r.status,json.loads(r.read() or "{}")
        except urllib.error.HTTPError as e:return e.code,json.loads(e.read() or "{}")

    def test_fts_search_over_http(self):
        params=urllib.parse.urlencode({"sheet":1,"q":"街角中文","limit":50})
        status,data=self.get("/v2/projects/demo/records?"+params)
        self.assertEqual(status,200)
        self.assertEqual(data["search_backend"],"fts5-trigram")
        self.assertEqual(len(data["records"]),50)
        self.assertTrue(data["next_cursor"])

    def test_adaptive_common_query(self):
        params=urllib.parse.urlencode({"sheet":1,"q":"制作中"})
        status,data=self.get("/v2/projects/demo/records?"+params)
        self.assertEqual(status,200)
        self.assertIn(data["search_backend"],("fts5-trigram","fts5-high-frequency-scan"))
        self.assertTrue(all(r["cells"][3]["value"]=="制作中" for r in data["records"]))

    def test_typed_numeric_comparisons(self):
        route="/v2/projects/demo/records?"
        params=urllib.parse.urlencode({"sheet":1,"field":3,"value":"300","kind":"number","op":"ge"})
        status,data=self.get(route+params)
        self.assertEqual(status,200)
        self.assertEqual([r["cells"][2]["value"] for r in data["records"]],[300,302,304,306,308])
        self.assertIn("+typed-field",data["search_backend"])
        params=urllib.parse.urlencode({"sheet":1,"field":3,"value":"300","kind":"number","op":"gt"})
        self.assertEqual(self.get(route+params)[1]["count"],4)
        params=urllib.parse.urlencode({"sheet":1,"field":3,"value":"not-number","kind":"number","op":"lt"})
        self.assertEqual(self.get(route+params)[0],400)

    def test_auth_untrusted_origin_mutation_and_legacy(self):
        prefix="/v2/projects/demo/records?sheet=1"
        self.assertEqual(self.get(prefix,token=None)[0],401)
        self.assertEqual(self.get(prefix,token="wrong")[0],401)
        self.assertEqual(self.get(prefix,origin="http://evil.example")[0],403)
        self.assertEqual(self.get(prefix,method="POST")[0],405)
        status,old=self.get("/v1/manifest")
        self.assertEqual(status,200)
        self.assertEqual(old["format"],"filmwiki-read-api-manifest")
        status,projects=self.get("/v2/projects")
        self.assertEqual(status,200)
        self.assertEqual(projects["projects"][0]["id"],"demo")
if __name__=="__main__":unittest.main()
