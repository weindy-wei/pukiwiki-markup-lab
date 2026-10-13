"""FilmWiki private API offline tests; no network beyond ephemeral localhost."""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
api_path=ROOT/"tools/filmwiki_api/server.py"
store_path=ROOT/"tools/table_import/local_store.py"
sp=importlib.util.spec_from_file_location("filmwiki_server",api_path)
server=importlib.util.module_from_spec(sp);sp.loader.exec_module(server)
st=importlib.util.spec_from_file_location("filmwiki_store",store_path)
local=importlib.util.module_from_spec(st);st.loader.exec_module(local)

SHA="f"*64
ORIGIN="http://127.0.0.1:8080"
TOKEN="test-"+("v"*60)

def fixture():
    fields=["镜头号"]+["字段"+str(i) for i in range(2,17)]+["父记录"]
    def rec(num,shot,parent=None):
        vals=[None]*17
        vals[0]=shot
        vals[3]="场景A"
        vals[16]=parent
        return {
            "record_id":"src_test_"+str(num),
            "source_row":num,"shot_number":shot,"parent_shot":parent,
            "cells":[{"index":i+1,"field":name,"value":vals[i]} for i,name in enumerate(fields)]
        }
    rows=[rec(2,"SC_06"),rec(3,"SC_06"),rec(4,"SC_06_01","SC_06")]
    return {
        "format":"filmwiki-import-preview",
        "source":{"sha256":SHA,"filename":"synthetic.xlsx"},
        "sheets":[
            {"name":"第1集","fields":[{"index":i+1,"name":n} for i,n in enumerate(fields)],"records":rows},
            {"name":"道具设定","fields":[{"index":1,"name":"道具名称"}],"records":[]}
        ],
        "asset_references":[{"ref_id":"assetref_test","record_id":rows[0]["record_id"],
                             "filename":"image.png","column":2,"status":"unresolved"}]
    }

class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        folder=Path(cls.temp.name)
        cls.db=folder/"owner.sqlite"
        cls.secret=folder/"token.txt"
        cls.secret.write_text(TOKEN+"\n",encoding="utf-8")
        local.store(fixture(),cls.db)
        assert server.validate_startup(cls.db,folder,cls.secret,SHA)[1]==TOKEN
        cls.server=server.FilmWikiServer(("127.0.0.1",0),server.Handler,cls.db,SHA,TOKEN)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.url="http://127.0.0.1:"+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=4)
        cls.temp.cleanup()

    def request(self,path,method="GET",token=TOKEN,origin=ORIGIN,extra=None):
        headers={}
        if token is not None:
            headers["Authorization"]="Bearer "+token
        if origin is not None:
            headers["Origin"]=origin
        if extra: headers.update(extra)
        req=urllib.request.Request(self.url+path,headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=4) as res:
                return res.status,json.loads(res.read() or b"{}"),res.headers
        except urllib.error.HTTPError as err:
            return err.code,json.loads(err.read() or b"{}"),err.headers

    def test_health_has_no_records(self):
        status,payload,_=self.request("/v1/health",token=None)
        self.assertEqual(status,200)
        self.assertEqual(payload,{"status":"ok","read_only":True})

    def test_manifest_and_paged_records(self):
        status,payload,_=self.request("/v1/manifest")
        self.assertEqual(status,200)
        self.assertEqual(len(payload["sheets"]),2)
        self.assertEqual(payload["sheets"][0]["record_count"],3)
        self.assertEqual(len(payload["sheets"][0]["fields"]),17)
        self.assertEqual(payload["sheets"][1]["record_count"],0)
        status,first,_=self.request("/v1/records?sheet=1&offset=0&limit=2")
        self.assertEqual(status,200)
        self.assertEqual(first["total"],3)
        self.assertEqual(first["next_offset"],2)
        self.assertEqual(len(first["records"][0]["cells"]),17)
        self.assertEqual(first["records"][0]["cells"][3]["value"],"场景A")
        self.assertIsNone(first["records"][0]["cells"][2]["value"])
        self.assertEqual(first["asset_references"][0]["filename"],"image.png")
        self.assertEqual(first["records"][0]["shot_number"],"SC_06")
        self.assertEqual(first["records"][1]["shot_number"],"SC_06")
        status,last,_=self.request("/v1/records?sheet=1&offset=2&limit=2")
        self.assertEqual(status,200)
        self.assertIsNone(last["next_offset"])
        self.assertEqual(last["records"][0]["parent_link"]["status"],"ambiguous")
        self.assertEqual(len(last["records"][0]["parent_link"]["candidate_record_ids"]),2)
        status,empty,_=self.request("/v1/records?sheet=2&offset=0&limit=2")
        self.assertEqual(status,200)
        self.assertEqual(empty["records"],[])

    def test_unauthorized_and_untrusted_origins_denied(self):
        self.assertEqual(self.request("/v1/manifest",token=None)[0],401)
        self.assertEqual(self.request("/v1/manifest",token="wrong")[0],401)
        self.assertEqual(self.request("/v1/manifest",origin="http://untrusted.example")[0],403)
        self.assertNotIn("Access-Control-Allow-Origin",
                          self.request("/v1/manifest",origin="http://untrusted.example")[2])

    def test_cors_preflight_exact(self):
        status,_,headers=self.request("/v1/records",method="OPTIONS",token=None,extra={
            "Access-Control-Request-Method":"GET",
            "Access-Control-Request-Headers":"authorization"
        })
        self.assertEqual(status,204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"),ORIGIN)
        status,_,_=self.request("/v1/records",method="OPTIONS",token=None,
            extra={"Access-Control-Request-Method":"POST"})
        self.assertEqual(status,405)
        status,_,_=self.request("/v1/records",method="OPTIONS",token=None,
            origin="http://bad.example",extra={"Access-Control-Request-Method":"GET"})
        self.assertEqual(status,403)

    def test_write_methods_and_bad_params_fail(self):
        original=self.db.read_bytes()
        for method in ("POST","PUT","DELETE","PATCH"):
            status,_,_=self.request("/v1/records",method=method)
            self.assertEqual(status,405)
        for path in ("/v1/records?sheet=1&limit=201",
                     "/v1/records?sheet=1&offset=-1",
                     "/v1/records?sheet=1&limit=2&limit=4",
                     "/v1/records?sheet=1&sql=DROP",
                     "/v1/records?sheet=foo"):
            self.assertEqual(self.request(path)[0],400)
        self.assertEqual(self.request("/v1/records?sheet=99")[0],404)
        self.assertEqual(self.request("/etc/passwd")[0],404)
        self.assertEqual(self.db.read_bytes(),original)
        self.assertEqual(server.manifest(self.db,SHA)["source"]["sha256"],SHA)

    def test_secure_token_creation_is_exclusive(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"credentials"/"token.txt"
            server.initialize_token(path)
            value=path.read_text(encoding="utf-8").strip()
            self.assertGreaterEqual(len(value),40)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                server.initialize_token(path)

    def test_startup_rejects_database_outside_data_root(self):
        with tempfile.TemporaryDirectory() as other:
            with self.assertRaises(ValueError):
                server.validate_startup(self.db,Path(other),self.secret,SHA)
        with self.assertRaises(ValueError):
            server.validate_startup(self.db,self.db.parent,self.secret,"bad")
        with self.assertRaises(FileNotFoundError):
            server.validate_startup(self.db,self.db.parent,self.db.parent/"missing",SHA)

if __name__=="__main__":
    unittest.main()
