"""Step29 local editor: authenticated tokenless Host/Origin and batch REST tests."""
import json
from pathlib import Path
import sys,tempfile,threading,unittest,uuid
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from urllib.parse import urlencode
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/filmwiki_api"))
sys.path.insert(0,str(ROOT/"tools/table_import"))
from local_store import store
from editor_store import initialize_copy,record,verify_history
from workflow_v5 import setup
from server_v4 import EditorHTTP
from test_filmwiki_editor_store import fixture,PROJECT,SOURCE_SHA

ORIGIN="http://127.0.0.1:8080"
class Step29HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.tmp.name)
        src=cls.root/"source.sqlite"
        store(fixture(),src)
        cls.db=cls.root/"editor.sqlite"
        initialize_copy(src,cls.db,cls.root,PROJECT,SOURCE_SHA,permissions={1:[2,3]})
        setup(cls.db,PROJECT)
        cls.server=EditorHTTP(("127.0.0.1",0),cls.db,None,PROJECT,local_no_token=True)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f"http://127.0.0.1:{cls.server.server_port}"
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=4)
        cls.tmp.cleanup()
    def call(self,route,payload=None,origin=ORIGIN):
        headers={}
        if origin:headers["Origin"]=origin
        if payload is not None:headers["Content-Type"]="application/json"
        req=Request(self.base+route,headers=headers,
            data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None,
            method="POST" if payload is not None else "GET")
        try:
            with urlopen(req,timeout=6) as res:return res.status,json.load(res)
        except HTTPError as error:
            with error:return error.code,json.loads(error.read() or b"{}")
    def payload(self):
        return {
          "project_id":PROJECT,"reason":"导演审核批量更新",
          "batch_id":str(uuid.uuid4()),
          "operations":[
            {"kind":"cell","sheet_index":1,"record_id":"src_synthetic_0001",
             "column_index":2,"value":"镜头计划修改一","expected_version":0},
            {"kind":"parent","sheet_index":1,"record_id":"src_synthetic_0008",
             "parent_record_id":"src_synthetic_0005","expected_version":0},
          ]
        }
    def test_01_batch_parent_fts_and_history(self):
        status,result=self.call("/v4/batch",self.payload())
        self.assertEqual(status,200,result)
        self.assertEqual(len(result["changes"]),2)
        code,link=self.call("/v4/relationship?"+urlencode(
            {"sheet":1,"id":"src_synthetic_0008"}))
        self.assertEqual(code,200)
        self.assertEqual(link["relation"]["parent_record_id"],"src_synthetic_0005")
        code,found=self.call("/v4/records?"+urlencode({"sheet":1,"q":"镜头计划修改一"}))
        self.assertEqual(code,200)
        self.assertEqual(found["count"],1)
        self.assertEqual(verify_history(self.db)["events"],2)
    def test_02_conflict_rolls_back_batch(self):
        payload=self.payload()
        payload["operations"][0]["expected_version"]=0
        payload["operations"][1]["expected_version"]=1
        payload["operations"][1]["parent_record_id"]="src_synthetic_0004"
        self.assertEqual(self.call("/v4/batch",payload)[0],409)
        self.assertEqual(record(self.db,1,"src_synthetic_0001")["version"],1)
        self.assertEqual(verify_history(self.db)["events"],2)
    def test_03_reject_origin_and_bad_relation(self):
        data=self.payload()
        self.assertEqual(self.call("/v4/batch",data,origin=None)[0],403)
        self.assertEqual(self.call("/v4/batch",data,origin="http://evil.invalid")[0],403)
        data["operations"]=[{"kind":"parent","sheet_index":1,"record_id":"src_synthetic_0008",
                            "parent_record_id":"SC_06","expected_version":1}]
        self.assertEqual(self.call("/v4/batch",data)[0],400)
        self.assertEqual(verify_history(self.db)["events"],2)
if __name__=="__main__":unittest.main()
