"""Step33 signature, expiry, replay, role, and authenticated session tests."""
import base64,hashlib,hmac,json,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/filmwiki_api"))
from collab_auth import (create_store,create_user,grant,disable,setup_sso,
                         redeem_sso,session)
KEY="abcdef0123456789"*4
def make(sub,now,**patch):
    d={"v":1,"iss":"filmwiki-native-local","aud":"filmwiki-collab-local",
       "sub":sub,"iat":now,"exp":now+45,"jti":hashlib.sha256(
        (sub+str(now)+str(patch)).encode()).hexdigest()[:48]}
    d.update(patch)
    wire=base64.urlsafe_b64encode(json.dumps(d,separators=(",",":")).encode()).decode().rstrip("=")
    sig=hmac.new(bytes.fromhex(KEY),wire.encode(),hashlib.sha256).hexdigest()
    return wire,sig

class Step33Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        self.db=root/"accounts.sqlite"
        create_store(self.db,root)
        self.uid=create_user(self.db,"alice","ExampleStrong123!xyz")["user_id"]
        self.other=create_user(self.db,"outsider","OtherStrong123!xyz")["user_id"]
        grant(self.db,"demo-editor","alice","viewer")
        setup_sso(self.db)
        self.now=int(time.time())
    def tearDown(self):self.tmp.cleanup()
    def test_once_returns_scoped_identity_and_session(self):
        wire,sig=make(self.uid,self.now)
        result=redeem_sso(self.db,wire,sig,KEY,now=self.now)
        self.assertEqual(result["username"],"alice")
        self.assertEqual(session(self.db,result["access_token"])["user_id"],self.uid)
        self.assertIsNone(redeem_sso(self.db,wire,sig,KEY,now=self.now))
    def test_expired_future_and_wrong_audience_rejected(self):
        for overrides in ({"exp":self.now-1},{"iat":self.now+10},
                          {"aud":"untrusted-service"},{"iss":"attacker"}):
            wire,sig=make(self.uid,self.now,**overrides)
            self.assertIsNone(redeem_sso(self.db,wire,sig,KEY,now=self.now))
    def test_signature_tampering_rejected(self):
        wire,sig=make(self.uid,self.now)
        self.assertIsNone(redeem_sso(self.db,wire,"0"*64,KEY,now=self.now))
        self.assertIsNone(redeem_sso(self.db,wire+"A",sig,KEY,now=self.now))
        self.assertIsNone(redeem_sso(self.db,wire,sig,"11"*32,now=self.now))
    def test_foreign_project_user_denied(self):
        wire,sig=make(self.other,self.now)
        self.assertIsNone(redeem_sso(self.db,wire,sig,KEY,now=self.now))
    def test_revoked_account_denied(self):
        disable(self.db,"alice")
        wire,sig=make(self.uid,self.now)
        self.assertIsNone(redeem_sso(self.db,wire,sig,KEY,now=self.now))
    def test_invalid_payload_fields_and_jti_rejected(self):
        for overrides in ({"jti":"bad"},{"v":2},{"sub":"somebody"},
                          {"iat":"notint"},{"exp":self.now+500}):
            wire,sig=make(self.uid,self.now,**overrides)
            self.assertIsNone(redeem_sso(self.db,wire,sig,KEY,now=self.now))
    def test_redeem_atomic_two_attempts(self):
        import concurrent.futures
        wire,sig=make(self.uid,self.now)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            outcomes=list(ex.map(lambda _:redeem_sso(self.db,wire,sig,KEY,now=self.now),[1,2]))
        self.assertEqual(sum(x is not None for x in outcomes),1)

if __name__=="__main__":unittest.main()
