"""Step34 session generations, multiple devices, and private asset-bytes ACL."""
import hashlib,json,os,sqlite3,sys,tempfile,threading,unittest,uuid
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"tools/filmwiki_api"),str(ROOT/"tools/table_import")]
from local_store import store
from editor_store import initialize_copy
from workflow_v5 import setup
from test_filmwiki_editor_store import fixture,SOURCE_SHA
PROJECT='demo-editor'
from collab_auth import (create_store,create_user,grant,login,session,
    active_sessions,revoke_session,revoke_all_sessions,setup_session_lifecycle)
from asset_catalog_v7 import (setup_assets,import_asset,list_assets,download_asset)
from server_v5_collab import CollabServer

class Step34Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        root=Path(cls.tmp.name)
        cls.root=root
        for suffix in ("private","staging/synthetic-assets"):
            (root/suffix).mkdir(parents=True,exist_ok=True)
        cls.db=root/"private/identities.sqlite"
        create_store(cls.db,root)
        cls.owner=create_user(cls.db,"director","Director2026!Secure")
        cls.viewer=create_user(cls.db,"reader01","Reader2026!Secure")
        cls.outsider=create_user(cls.db,"outsider","Outsider2026!Secure")
        grant(cls.db,PROJECT,"director","owner")
        grant(cls.db,PROJECT,"reader01","viewer")
        src=root/"source.sqlite"
        store(fixture(),src)
        cls.editor=root/"editor.sqlite"
        initialize_copy(src,cls.editor,root,PROJECT,SOURCE_SHA,permissions={1:[2,3]})
        setup(cls.editor,PROJECT)
        cls.assets=root/"private/assets"
        setup_assets(cls.assets,root)
        sample=root/"staging/synthetic-assets/sample.png"
        # Test-only PNG header. The private repository deliberately does not
        # claim full image decode validation; magic bytes are the v0.1 gate.
        sample.write_bytes(b"\x89PNG\r\n\x1a\n"+b"synthetic Step34 only")
        cls.project_asset=import_asset(cls.assets,cls.db,sample,PROJECT,
           cls.owner["user_id"],"sample.png","image/png","project")
        cls.private_asset=import_asset(cls.assets,cls.db,sample,PROJECT,
           cls.owner["user_id"],"private.png","image/png","private")
        cls.srv=CollabServer(("127.0.0.1",0),cls.db,cls.editor,PROJECT,
                            asset_root=cls.assets)
        cls.thread=threading.Thread(target=cls.srv.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f"http://127.0.0.1:{cls.srv.server_port}"
    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown();cls.srv.server_close();cls.thread.join(timeout=4)
        cls.tmp.cleanup()
    def request(self,path,token=None,payload=None):
        hdr={"Origin":"http://127.0.0.1:8080"}
        if token:hdr["Authorization"]="Bearer "+token
        if payload is not None:hdr["Content-Type"]="application/json"
        req=Request(self.base+path,headers=hdr,
                    data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urlopen(req,timeout=5) as response:
                content=response.read()
                return response.status,content,response.headers
        except HTTPError as e:
            with e:return e.code,e.read(),e.headers
    def signin(self,name,password):
        status,response,_=self.request("/v5/login",payload={"username":name,"password":password})
        self.assertEqual(status,200)
        return json.loads(response)["access_token"]
    def test_01_device_list_and_single_revoke(self):
        t1=self.signin("director","Director2026!Secure")
        t2=self.signin("director","Director2026!Secure")
        st,data,_=self.request("/v6/sessions",t1)
        self.assertEqual(st,200)
        info=json.loads(data)
        own=[s for s in info["sessions"] if s["session_id"]==info["current_session_id"]]
        self.assertEqual(len(own),1)
        other=session(self.db,t2)["session_id"]
        st,data,_=self.request("/v6/sessions/revoke",t1,{"session_id":other})
        self.assertEqual(st,200)
        self.assertTrue(json.loads(data)["revoked"])
        self.assertEqual(self.request("/v5/me",t2)[0],401)
        self.assertEqual(self.request("/v5/me",t1)[0],200)
    def test_02_central_logout_all_invalidates_bearers_and_epoch(self):
        before=self.signin("director","Director2026!Secure")
        token2=self.signin("director","Director2026!Secure")
        with sqlite3.connect(self.db) as con:
            epoch=con.execute("SELECT session_epoch FROM users WHERE username='director'").fetchone()[0]
        st,data,_=self.request("/v6/sessions/revoke-all",before,{})
        self.assertEqual(st,200)
        self.assertTrue(json.loads(data)["wiki_sessions_invalidated_on_next_request"])
        self.assertEqual(self.request("/v5/me",before)[0],401)
        self.assertEqual(self.request("/v5/me",token2)[0],401)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("SELECT session_epoch FROM users WHERE username='director'").fetchone()[0],epoch+1)
        self.assertEqual(self.request("/v6/sessions",before)[0],401)
    def test_03_authenticated_project_asset_and_private_owner(self):
        owner=self.signin("director","Director2026!Secure")
        viewer=self.signin("reader01","Reader2026!Secure")
        outsider=self.signin("outsider","Outsider2026!Secure")
        public_id=self.project_asset["asset_id"]
        private_id=self.private_asset["asset_id"]
        for token,expected_count in ((owner,2),(viewer,1)):
            st,data,_=self.request("/v6/assets",token)
            self.assertEqual(st,200)
            self.assertEqual(len(json.loads(data)["assets"]),expected_count)
        self.assertEqual(self.request("/v6/assets",outsider)[0],403)
        st,data,hdr=self.request(f"/v6/assets/{public_id}/content",viewer)
        self.assertEqual(st,200)
        self.assertEqual(hashlib.sha256(data).hexdigest(),self.project_asset["sha256"])
        self.assertEqual(hdr.get("Content-Type"),"application/octet-stream")
        self.assertEqual(hdr.get("X-Content-Type-Options"),"nosniff")
        self.assertEqual(hdr.get("Cache-Control"),"no-store, private")
        self.assertEqual(self.request(f"/v6/assets/{private_id}/content",viewer)[0],404)
        self.assertEqual(self.request(f"/v6/assets/{private_id}/content",owner)[0],200)
        self.assertEqual(self.request(f"/v6/assets/{public_id}/content",outsider)[0],403)
        self.assertEqual(self.request(f"/v6/assets/{public_id}/content")[0],401)
    def test_04_revoke_membership_blocks_immediately_at_read(self):
        viewer=self.signin("reader01","Reader2026!Secure")
        uid=self.viewer["user_id"]
        with sqlite3.connect(self.db) as con:
            con.execute("DELETE FROM memberships WHERE user_id=?",(uid,))
        self.assertEqual(self.request("/v6/assets",viewer)[0],403)
        self.assertEqual(self.request(f'/v6/assets/{self.project_asset["asset_id"]}/content',viewer)[0],403)
    def test_05_integrity_check_fail_closed(self):
        owner=self.signin("director","Director2026!Secure")
        aid=self.private_asset["asset_id"]
        file=self.assets/("asset-"+aid+".bin")
        original=file.read_bytes()
        try:
            file.write_bytes(b"TAMPER")
            self.assertEqual(self.request(f"/v6/assets/{aid}/content",owner)[0],503)
        finally:file.write_bytes(original)
    def test_06_traversal_unknown_and_malformed_inputs_denied(self):
        token=self.signin("director","Director2026!Secure")
        self.assertEqual(self.request("/v6/assets/../../etc/passwd",token)[0],404)
        self.assertEqual(self.request("/v6/assets/"+str(uuid.uuid4())+"/content",token)[0],404)
        self.assertEqual(self.request("/v6/assets",token,payload={})[0],404)
    def test_07_atomic_migration_preserves_existing_sessions(self):
        before=self.signin("director","Director2026!Secure")
        setup_session_lifecycle(self.db)
        setup_session_lifecycle(self.db)
        self.assertEqual(self.request("/v5/me",before)[0],200)

if __name__=="__main__":unittest.main()
