"""FilmWiki v2 multi-project and keyset API acceptance tests (localhost only)."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/filmwiki_api"))
sys.path.insert(0,str(ROOT/"tools/table_import"))
from local_store import store
from catalog_v2 import read_registry,read_page,read_record,list_projects
from server_v2 import ProjectServer

TOKEN="p"*64
SHA1="a"*64
SHA2="b"*64

def fixture(sha,n,project):
    columns=["镜头号","镜头内容","时长（秒）","父记录","备注"]+["字段"+str(i) for i in range(6,18)]
    records=[]
    for idx in range(n):
        vals=[None]*17
        vals[0]="SC_06" if idx in (0,1) else "SC_"+str(idx).zfill(3)
        vals[1]=project+" 人物"+str(idx)+" 测试台词 "+("特殊%_" if idx==2 else "")
        vals[2]=str(idx+3)
        if idx==3:vals[3]="SC_06"
        vals[4]="偶数" if idx%2==0 else "奇数"
        records.append({
            "record_id":"src_"+project+"_"+str(idx).zfill(6),
            "source_row":idx+2,"shot_number":vals[0],"parent_shot":vals[3],
            "cells":[{"index":i+1,"field":name,"value":vals[i]} for i,name in enumerate(columns)]
        })
    return {"format":"filmwiki-import-preview",
            "source":{"sha256":sha,"filename":project+".xlsx"},
            "sheets":[{"name":"第1集","fields":[{"index":i+1,"name":h} for i,h in enumerate(columns)],
                      "records":records},
                     {"name":"道具设定","fields":[{"index":1,"name":"道具名称"}],"records":[]}],
            "asset_references":[{"ref_id":"assetref_"+project,"record_id":records[1]["record_id"],
                                 "filename":"image.png","column":2,"status":"unresolved"}]}

class V2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.tmp.name)
        for project,sha,n in [("demo-a",SHA1,253),("demo-b",SHA2,4)]:
            store(fixture(sha,n,project),cls.root/(project+".sqlite"))
        cls.registry=cls.root/"registry.json"
        cls.registry.write_text(json.dumps({"version":1,"projects":[
            {"id":p,"title":"演示 "+p,"db":p+".sqlite","source_sha":sha}
            for p,sha in [("demo-a",SHA1),("demo-b",SHA2)]
        ]}),encoding="utf-8")
        cls.projects=read_registry(cls.registry,cls.root)
        cls.server=ProjectServer(("127.0.0.1",0),cls.projects,TOKEN)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.url="http://127.0.0.1:"+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=3);cls.tmp.cleanup()

    def request(self,path,token=TOKEN,origin="http://127.0.0.1:8080",method="GET"):
        headers={"Origin":origin}
        if token is not None:headers["Authorization"]="Bearer "+token
        req=urllib.request.Request(self.url+path,headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=4) as res:return res.status,json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as err:return err.code,json.loads(err.read() or b"{}")

    def test_project_allowlist_and_manifest(self):
        code,p=self.request("/v2/projects")
        self.assertEqual(code,200)
        self.assertEqual([x["id"] for x in p["projects"]],["demo-a","demo-b"])
        self.assertNotIn("db",json.dumps(p))
        code,a=self.request("/v2/projects/demo-a/manifest")
        self.assertEqual(code,200)
        self.assertEqual(a["project"]["id"],"demo-a")
        self.assertEqual(a["sheets"][0]["record_count"],253)
        self.assertEqual(len(a["sheets"][0]["fields"]),17)
        self.assertEqual(a["sheets"][1]["record_count"],0)
        self.assertEqual(self.request("/v2/projects/unknown/manifest")[0],404)

    def test_cursor_avoids_duplicate_or_missing_rows(self):
        project=self.projects["demo-a"]
        cursor=None
        ids=[]
        pages=0
        while True:
            data=read_page(project,TOKEN,1,limit=41,cursor=cursor)
            pages+=1
            ids.extend(x["record_id"] for x in data["records"])
            cursor=data["next_cursor"]
            if cursor is None:break
            self.assertLess(pages,10)
        self.assertEqual(len(ids),253)
        self.assertEqual(len(set(ids)),253)
        self.assertEqual(pages,7)
        self.assertEqual(ids[0],"src_demo-a_000000")
        self.assertEqual(ids[-1],"src_demo-a_000252")

    def test_filter_and_cursor_binding(self):
        p=self.projects["demo-a"]
        first=read_page(p,TOKEN,1,limit=2,field=5,value="偶数")
        self.assertEqual([r["source_row"] for r in first["records"]],[2,4])
        self.assertTrue(first["has_more"])
        self.assertEqual(len(first["records"][0]["cells"]),17)
        second=read_page(p,TOKEN,1,limit=2,field=5,value="偶数",cursor=first["next_cursor"])
        self.assertEqual([r["source_row"] for r in second["records"]],[6,8])
        with self.assertRaises(ValueError):read_page(p,TOKEN,1,limit=2,field=5,value="奇数",cursor=first["next_cursor"])
        with self.assertRaises(ValueError):read_page(self.projects["demo-b"],TOKEN,1,limit=2,cursor=first["next_cursor"])
        altered=first["next_cursor"][:-1]+("A" if first["next_cursor"][-1]!="A" else "B")
        with self.assertRaises(ValueError):read_page(p,TOKEN,1,limit=2,field=5,value="偶数",cursor=altered)
        result=read_page(p,TOKEN,1,limit=5,q="特殊%_")
        self.assertEqual([x["source_row"] for x in result["records"]],[4])

    def test_single_record_and_project_boundary(self):
        item=read_record(self.projects["demo-a"],1,"src_demo-a_000003")
        self.assertEqual(item["record"]["parent_link"]["status"],"ambiguous")
        self.assertEqual(len(item["record"]["parent_link"]["candidate_record_ids"]),2)
        self.assertEqual(len(item["record"]["cells"]),17)
        with self.assertRaises(KeyError):
            read_record(self.projects["demo-b"],1,"src_demo-a_000003")

    def test_http_errors_and_legacy_v1(self):
        for p in ("/v2/projects","/v2/projects/demo-a/manifest","/v2/projects/demo-a/records?sheet=1"):
            self.assertEqual(self.request(p,token=None)[0],401)
            self.assertEqual(self.request(p,token="x")[0],401)
            self.assertEqual(self.request(p,origin="http://bad.test")[0],403)
        self.assertEqual(self.request("/v2/projects/demo-a/records?sheet=1&limit=101")[0],400)
        self.assertEqual(self.request("/v2/projects/demo-a/records?sheet=1&field=2")[0],400)
        self.assertEqual(self.request("/v2/projects/demo-a/records?sheet=1&q="+("z"*200))[0],400)
        self.assertEqual(self.request("/v2/projects/demo-a/records?sheet=1&db=../../etc/passwd")[0],400)
        self.assertEqual(self.request("/v2/projects/demo-a/records?sheet=1",method="POST")[0],405)
        self.assertEqual(self.request("/v1/manifest")[0],200)

    def test_registry_cannot_escape_or_duplicate(self):
        original=json.loads(self.registry.read_text())
        original["projects"][0]["db"]="../escape.sqlite"
        self.registry.write_text(json.dumps(original),encoding="utf-8")
        with self.assertRaises(ValueError):read_registry(self.registry,self.root)
        original["projects"][0]["db"]="demo-a.sqlite"
        original["projects"][1]["id"]="demo-a"
        self.registry.write_text(json.dumps(original),encoding="utf-8")
        with self.assertRaises(ValueError):read_registry(self.registry,self.root)

if __name__=="__main__":
    unittest.main()
