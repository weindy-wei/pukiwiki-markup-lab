"""Tokenless localhost-only editor acceptance: Origin, Host, method, and edit integrity."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from uuid import uuid4
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/filmwiki_api"))
sys.path.insert(0,str(ROOT/"tools/table_import"))
from local_store import store
from editor_store import initialize_copy,verify_history
from server_v4 import EditorHTTP
from test_filmwiki_editor_store import fixture,PROJECT,SOURCE_SHA

ORIGIN="http://127.0.0.1:8080"
class TokenlessEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.tmp.name)
        src=cls.root/"source.sqlite"
        store(fixture(),src)
        cls.work=cls.root/"working.sqlite"
        initialize_copy(src,cls.work,cls.root,PROJECT,SOURCE_SHA,permissions={1:[2,3]})
        cls.api=EditorHTTP(("127.0.0.1",0),cls.work,None,PROJECT,local_no_token=True)
        cls.worker=threading.Thread(target=cls.api.serve_forever,daemon=True)
        cls.worker.start()
        cls.address=f"http://127.0.0.1:{cls.api.server_port}"
    @classmethod
    def tearDownClass(cls):
        cls.api.shutdown();cls.api.server_close();cls.worker.join(timeout=5)
        cls.tmp.cleanup()
    def call(self,path,method="GET",body=None,origin=ORIGIN,headers=None):
        headers=dict(headers or {})
        if origin is not None:headers["Origin"]=origin
        if body is not None:headers["Content-Type"]="application/json"
        raw=None if body is None else json.dumps(body,ensure_ascii=False).encode()
        req=Request(self.address+path,headers=headers,data=raw,method=method)
        try:
            with urlopen(req,timeout=5) as response:
                return response.status,json.load(response) if response.status!=204 else {}
        except HTTPError as error:
            with error:return error.code,json.loads(error.read() or b"{}")
    def payload(self,value="不需要令牌的本地镜头修改",version=0):
        return {"project_id":PROJECT,"sheet_index":1,"record_id":"src_synthetic_0005",
                "column_index":2,"value":value,"expected_version":version,
                "reason":"验证无令牌本机受控编辑","request_id":str(uuid4())}
    def test_tokenless_local_auto_read_and_edit(self):
        code,metadata=self.call("/v4/manifest")
        self.assertEqual(code,200)
        self.assertEqual(metadata["project"]["id"],PROJECT)
        code,edited=self.call("/v4/edit","POST",self.payload())
        self.assertEqual(code,200,edited)
        self.assertEqual(edited["version"],1)
        code,history=self.call("/v4/history?sheet=1&id=src_synthetic_0005")
        self.assertEqual(code,200)
        self.assertEqual(len(history["events"]),1)
        self.assertEqual(verify_history(self.work)["events"],1)
    def test_reject_cross_site_origin_and_no_origin_data(self):
        self.assertEqual(self.call("/v4/manifest",origin=None)[0],403)
        self.assertEqual(self.call("/v4/manifest",origin="http://evil.example")[0],403)
        self.assertEqual(self.call("/v4/edit","POST",self.payload(),origin=None)[0],403)
        self.assertEqual(self.call("/v4/edit","POST",self.payload(),origin="http://evil.example")[0],403)
    def test_reject_dns_rebinding_host(self):
        self.assertEqual(self.call("/v4/manifest",headers={"Host":"evil.example"})[0],403)
        self.assertEqual(self.call("/v4/health",headers={"Host":"evil.example"})[0],403)
        self.assertEqual(self.call("/v4/edit","POST",self.payload(),headers={"Host":"evil.example"})[0],403)
    def test_preflight_allows_content_type_not_authorization(self):
        good={"Access-Control-Request-Method":"POST","Access-Control-Request-Headers":"content-type"}
        self.assertEqual(self.call("/v4/edit","OPTIONS",headers=good)[0],204)
        wrong={**good,"Access-Control-Request-Headers":"authorization,content-type"}
        self.assertEqual(self.call("/v4/edit","OPTIONS",headers=wrong)[0],403)
        self.assertEqual(self.call("/v4/edit","OPTIONS",headers=good,origin="http://evil.example")[0],403)
    def test_bad_methods_and_bad_content_type_still_rejected(self):
        self.assertEqual(self.call("/v4/edit","DELETE")[0],405)
        self.assertEqual(self.call("/v4/edit","POST",self.payload(),headers={"Host":"evil.example"})[0],403)
    def test_wide_binding_forbidden_in_tokenless_mode(self):
        with self.assertRaises(ValueError):
            EditorHTTP(("0.0.0.0",0),self.work,None,PROJECT,local_no_token=True)

if __name__=="__main__":unittest.main()
