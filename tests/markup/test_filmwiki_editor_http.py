"""API v4 end-to-end transaction, CORS, authorization and same-commit FTS tests."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from urllib.parse import urlencode
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/filmwiki_api"))
sys.path.insert(0,str(ROOT/"tools/table_import"))
from local_store import store
from editor_store import initialize_copy,verify_history
from server_v4 import EditorHTTP
from test_filmwiki_editor_store import fixture,PROJECT,SOURCE_SHA

TOKEN="a"*96
ORIGIN="http://127.0.0.1:8080"
class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.tmp.name)
        source=cls.root/"source.sqlite"
        store(fixture(),source)
        cls.work=cls.root/"editor.sqlite"
        initialize_copy(source,cls.work,cls.root,PROJECT,SOURCE_SHA,permissions={1:[2,3]})
        cls.server=EditorHTTP(("127.0.0.1",0),cls.work,TOKEN,PROJECT)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.url="http://127.0.0.1:"+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=5)
        cls.tmp.cleanup()
    def request(self,path,method="GET",body=None,token=TOKEN,origin=ORIGIN):
        headers={"Origin":origin}
        if token is not None:headers["Authorization"]="Bearer "+token
        if body is not None:headers["Content-Type"]="application/json"
        data=None if body is None else json.dumps(body,ensure_ascii=False).encode("utf-8")
        req=Request(self.url+path,headers=headers,data=data,method=method)
        try:
            with urlopen(req,timeout=6) as r:return r.status,json.load(r)
        except HTTPError as err:return err.code,json.loads(err.read() or b"{}")
    def edit(self,value="新版镜头：雪地追逐",expected=0,id=None):
        return {"project_id":PROJECT,"sheet_index":1,"record_id":"src_synthetic_0005",
                "column_index":2,"value":value,"expected_version":expected,
                "reason":"更正动作与镜头描述","request_id":id or str(uuid4())}

    def test_01_authorization_same_origin_and_readonly_baseline(self):
        self.assertEqual(self.request("/v4/manifest",token=None)[0],401)
        self.assertEqual(self.request("/v4/manifest",token="wrong")[0],401)
        self.assertEqual(self.request("/v4/manifest",origin="http://malicious.example")[0],403)
        status,doc=self.request("/v4/manifest")
        self.assertEqual(status,200)
        self.assertTrue(doc["working_copy"])
        self.assertEqual(doc["sheets"][0]["record_count"],12)
        self.assertFalse(doc["sheets"][0]["fields"][0]["editable"])
        self.assertTrue(doc["sheets"][0]["fields"][1]["editable"])
        self.assertEqual(self.request("/v4/records?sheet=1")[0],200)

    def test_02_edit_atomic_and_history_then_conflict(self):
        value="新版镜头：雪地追逐"
        status,result=self.request("/v4/edit","POST",self.edit(value))
        self.assertEqual(status,200,result)
        self.assertEqual(result["version"],1)
        status,row=self.request("/v4/record?"+urlencode({"sheet":1,"id":"src_synthetic_0005"}))
        self.assertEqual(row["version"],1)
        self.assertEqual(row["cells"][1]["value"],value)
        self.assertEqual(self.request("/v4/records?"+urlencode({"sheet":1,"q":"雪地追逐"}))[1]["count"],1)
        self.assertEqual(self.request("/v4/records?"+urlencode({"sheet":1,"q":"路灯下散步"}))[1]["count"],0)
        code,history=self.request("/v4/history?"+urlencode({"sheet":1,"id":"src_synthetic_0005"}))
        self.assertEqual(code,200)
        self.assertEqual(len(history["events"]),1)
        self.assertEqual(self.request("/v4/edit","POST",self.edit("覆盖别人修改",expected=0))[0],409)
        self.assertEqual(self.request("/v4/edit","POST",self.edit("突破权限",expected=1)|{"column_index":1})[0],400)
        self.assertEqual(verify_history(self.work)["events"],1)

    def test_03_unauthorized_writes(self):
        payload=self.edit(value="其他修改",expected=1)
        self.assertEqual(self.request("/v4/edit","POST",payload,token=None)[0],401)
        self.assertEqual(self.request("/v4/edit","POST",payload,origin="http://bad.invalid")[0],403)
        self.assertEqual(self.request("/v4/edit","DELETE",payload)[0],405)
        self.assertEqual(verify_history(self.work)["events"],1)

if __name__=="__main__":unittest.main()
