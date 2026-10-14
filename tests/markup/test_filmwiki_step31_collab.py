"""Step31 real local sessions/ACL + concurrent edits + audit attribution tests."""
import json
from pathlib import Path
import sqlite3,sys,tempfile,threading,unittest,uuid
from urllib.request import Request,urlopen
from urllib.error import HTTPError
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"tools/filmwiki_api"),str(ROOT/"tools/table_import")]
from local_store import store
from editor_store import initialize_copy,record,history,verify_history
from workflow_v5 import setup
from collab_auth import (create_store,create_user,grant,revoke,reset_password,login,session,logout,disable,
                         require_role,SESSION_SECONDS)
from server_v5_collab import CollabServer
from test_filmwiki_editor_store import fixture,PROJECT,SOURCE_SHA
PASS1="BlueDragon!Q7n-27"
PASS2="BlueDragon!Q8n-28"
ORIGIN="http://127.0.0.1:8080"

class Step31Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.tmp.name)
        src=cls.root/"source.sqlite"
        store(fixture(),src)
        cls.work=cls.root/"collab.sqlite"
        initialize_copy(src,cls.work,cls.root,PROJECT,SOURCE_SHA,permissions={1:[2,3]})
        setup(cls.work,PROJECT)
        cls.accounts=cls.root/"identities.sqlite"
        create_store(cls.accounts,cls.root)
        cls.alice=create_user(cls.accounts,"alice",PASS1)
        cls.bob=create_user(cls.accounts,"bob",PASS2)
        cls.cara=create_user(cls.accounts,"cara","ViewerOnly!Top8")
        cls.mallory=create_user(cls.accounts,"mallory","NoMember!Top8")
        cls.uid_a=grant(cls.accounts,PROJECT,"alice","editor")
        cls.uid_b=grant(cls.accounts,PROJECT,"bob","editor")
        grant(cls.accounts,PROJECT,"cara","viewer")
        with sqlite3.connect(cls.work) as conn:
            conn.executemany("INSERT INTO workflow_role_grants VALUES (?,?,?)",[
                (PROJECT,cls.uid_a,"editor"),(PROJECT,cls.uid_b,"editor")
            ])
        cls.srv=CollabServer(("127.0.0.1",0),cls.accounts,cls.work,PROJECT)
        cls.thread=threading.Thread(target=cls.srv.serve_forever,daemon=True)
        cls.thread.start()
        cls.url="http://127.0.0.1:"+str(cls.srv.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown();cls.srv.server_close();cls.thread.join(timeout=4)
        cls.tmp.cleanup()
    def api(self,path,payload=None,token=None,origin=ORIGIN,host=None):
        headers={}
        if origin is not None:headers["Origin"]=origin
        if token is not None:headers["Authorization"]="Bearer "+token
        if payload is not None:headers["Content-Type"]="application/json"
        if host:headers["Host"]=host
        req=Request(self.url+path,headers=headers,
          data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None,
          method="POST" if payload is not None else "GET")
        try:
            with urlopen(req,timeout=6) as res:return res.status,json.load(res)
        except HTTPError as exc:
            with exc:return exc.code,json.loads(exc.read() or b"{}")
    def auth(self,user,password):
        status,obj=self.api("/v5/login",{"username":user,"password":password})
        self.assertEqual(status,200,obj)
        return obj["access_token"]
    def item(self,rid,version,value):
        return {"project_id":PROJECT,"sheet_index":1,"record_id":rid,"column_index":2,
                "value":value,"expected_version":version,"reason":"协作审核镜头措辞",
                "request_id":str(uuid.uuid4())}
    def test_01_auth_required_and_project_isolation(self):
        self.assertEqual(self.api("/v5/manifest")[0],401)
        token=self.auth("mallory","NoMember!Top8")
        self.assertEqual(self.api("/v5/me",token=token)[0],200)
        self.assertEqual(self.api("/v5/manifest",token=token)[0],403)
        self.assertEqual(self.api("/v5/records?sheet=1",token=token)[0],403)
    def test_02_wrong_password_and_origin_dns_rebind(self):
        self.assertEqual(self.api("/v5/login",{"username":"alice","password":"fake"})[0],401)
        self.assertEqual(self.api("/v5/login",{"username":"alice","password":PASS1},origin="http://evil.invalid")[0],403)
        self.assertEqual(self.api("/v5/login",{"username":"alice","password":PASS1},host="evil.invalid")[0],403)
        self.assertEqual(self.api("/v5/login",{"username":"alice","password":PASS1},origin=None)[0],403)
    def test_03_viewer_can_read_cannot_write(self):
        token=self.auth("cara","ViewerOnly!Top8")
        status,meta=self.api("/v5/manifest",token=token)
        self.assertEqual(status,200)
        self.assertFalse(any(x["editable"] for s in meta["sheets"] for x in s["fields"]))
        self.assertEqual(self.api("/v5/edit",self.item("src_synthetic_0004",0,"非法修改"),
                                  token=token)[0],403)
    def test_04_two_accounts_versions_and_actor_audit(self):
        alice=self.auth("alice",PASS1);bob=self.auth("bob",PASS2)
        rid="src_synthetic_0005"
        status,obj=self.api("/v5/edit",self.item(rid,0,"Alice 的镜头修订 A"),token=alice)
        self.assertEqual(status,200,obj)
        self.assertEqual(obj["version"],1)
        self.assertEqual(self.api("/v5/edit",self.item(rid,0,"Bob 旧版本覆盖"),token=bob)[0],409)
        self.assertEqual(self.api("/v5/edit",self.item(rid,1,"Bob 的镜头修订 B"),token=bob)[0],200)
        status,events=self.api("/v5/history?sheet=1&id="+rid,token=alice)
        self.assertEqual(status,200)
        self.assertEqual([e["actor"] for e in events["events"]],
                         [self.uid_a,self.uid_b])
        self.assertEqual(record(self.work,1,rid)["version"],2)
        self.assertTrue(verify_history(self.work,rid)["valid"])
    def test_05_atomic_batch_user_identity(self):
        token=self.auth("bob",PASS2)
        payload={"project_id":PROJECT,"reason":"Bob 批量镜头更正",
             "batch_id":str(uuid.uuid4()),
             "operations":[{"kind":"cell","sheet_index":1,"record_id":"src_synthetic_0001",
               "column_index":2,"value":"Bob 批量镜头一","expected_version":0},
               {"kind":"cell","sheet_index":1,"record_id":"src_synthetic_0002",
               "column_index":2,"value":"Bob 批量镜头二","expected_version":0}]}
        status,result=self.api("/v5/batch",payload,token=token)
        self.assertEqual(status,200,result)
        self.assertEqual(result["audit_events"],2)
        for i in (1,2):
            events=history(self.work,1,f"src_synthetic_{i:04d}")["events"]
            self.assertEqual(events[-1]["actor"],self.uid_b)
        self.assertTrue(verify_history(self.work)["valid"])
    def test_06_logout_revocation(self):
        token=self.auth("alice",PASS1)
        self.assertEqual(self.api("/v5/me",token=token)[0],200)
        self.assertEqual(self.api("/v5/logout",{},token=token)[0],200)
        self.assertEqual(self.api("/v5/me",token=token)[0],401)
    def test_07_expiry_and_user_disable(self):
        token=self.auth("cara","ViewerOnly!Top8")
        self.assertIsNotNone(session(self.accounts,token))
        self.assertIsNone(session(self.accounts,token,now=10**11))
        disable(self.accounts,"cara")
        self.assertEqual(self.api("/v5/manifest",token=token)[0],401)
    def test_08_durable_account_lockout(self):
        for _ in range(5):
            self.assertIsNone(login(self.accounts,"alice","bad-password"))
        self.assertIsNone(login(self.accounts,"alice",PASS1))
        with sqlite3.connect(self.accounts) as conn:
            conn.execute("UPDATE users SET locked_until=0 WHERE username='alice'")
        self.assertIsNotNone(login(self.accounts,"alice",PASS1))
    def test_09_no_role_grant_means_sqlite_transaction_denied(self):
        actor=self.uid_a
        with sqlite3.connect(self.work) as con:
            con.execute("DELETE FROM workflow_role_grants WHERE actor=?",(actor,))
        token=self.auth("alice",PASS1)
        status,_=self.api("/v5/edit",self.item("src_synthetic_0006",0,"不能绕过数据库权限"),token=token)
        self.assertEqual(status,403)
        self.assertEqual(record(self.work,1,"src_synthetic_0006")["version"],0)
    def test_10_only_loopback_listening(self):
        with self.assertRaises(ValueError):
            CollabServer(("0.0.0.0",0),self.accounts,self.work,PROJECT)

    def test_11_role_revocation_expires_sessions(self):
        token=self.auth("mallory","NoMember!Top8")
        self.assertEqual(self.api("/v5/manifest",token=token)[0],403)
        grant(self.accounts,PROJECT,"mallory","viewer")
        token=self.auth("mallory","NoMember!Top8")
        self.assertEqual(self.api("/v5/manifest",token=token)[0],200)
        revoke(self.accounts,PROJECT,"mallory","viewer")
        self.assertEqual(self.api("/v5/manifest",token=token)[0],401)
        new_token=self.auth("mallory","NoMember!Top8")
        self.assertEqual(self.api("/v5/manifest",token=new_token)[0],403)

    def test_12_password_reset_invalidates_existing_sessions(self):
        token=self.auth("bob",PASS2)
        reset_password(self.accounts,"bob","Replaced!Password2211")
        self.assertEqual(self.api("/v5/me",token=token)[0],401)
        self.assertEqual(self.api("/v5/login",{"username":"bob","password":PASS2})[0],401)
        self.assertEqual(self.api("/v5/login",{
            "username":"bob","password":"Replaced!Password2211"})[0],200)

if __name__=="__main__":unittest.main()
