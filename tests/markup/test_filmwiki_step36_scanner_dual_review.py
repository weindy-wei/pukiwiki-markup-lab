"""Step36: complete safe scan + independent reviewer acceptance checks."""
import hashlib,sqlite3,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/filmwiki_api"))
from collab_auth import create_store,create_user,grant,revoke
from asset_lifecycle_v8 import stage_new,stage_revision,review,audit
from asset_security_v9 import setup_security,scan_pending
from asset_catalog_v7 import list_assets,download_asset,import_asset
PROJECT="demo-editor"
class SecurityTests(unittest.TestCase):
 def setUp(self):
    self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
    self.root=Path(self.temp.name);(self.root/"private").mkdir()
    self.stage=self.root/"staging/synthetic-assets";self.stage.mkdir(parents=True)
    self.txt=self.stage/"safe.txt";self.txt.write_text("FilmWiki synthetic security test\n",encoding="utf8")
    self.db=self.root/"private/identities.sqlite"
    create_store(self.db,self.root)
    self.owner=create_user(self.db,"uploader","UploadSecure123!xyz")
    self.reviewer=create_user(self.db,"reviewer","ReviewSecure123!xyz")
    self.viewer=create_user(self.db,"viewer","ViewerSecure123!xyz")
    grant(self.db,PROJECT,"uploader","owner")
    grant(self.db,PROJECT,"reviewer","reviewer")
    grant(self.db,PROJECT,"viewer","viewer")
    self.assets=self.root/"private/assets"
    setup_security(self.assets,self.root)
    self.identity={"user_id":self.owner["user_id"],
                   "memberships":[{"project_id":PROJECT,"role":"owner"}]}
 def stage_file(self):
    return stage_new(self.assets,self.db,self.txt,PROJECT,self.owner["user_id"],
                     "safe.txt","text/plain","project")["asset_id"]
 def scan_mock(self,asset_id,status="clean"):
    state={"clean":("clean","test_clean","test-only"),
           "infected":("infected","test_detected","test-only"),
           "error":("error","test_engine_down","test-only")}[status]
    with patch("asset_security_v9._engine",return_value=state):
        return scan_pending(self.assets,self.db,asset_id,self.reviewer["user_id"])
 def approve(self,aid,actor=None):
    return review(self.assets,self.db,aid,actor or self.reviewer["user_id"],"approved")
 def test_01_unscanned_never_approved_or_downloaded(self):
    aid=self.stage_file()
    self.assertIsNone(download_asset(self.assets,self.identity,PROJECT,aid))
    self.assertEqual(list_assets(self.assets,self.identity,PROJECT),[])
    with self.assertRaises(PermissionError):self.approve(aid)
 def test_02_submitter_cannot_self_approve_even_if_owner(self):
    aid=self.stage_file();self.scan_mock(aid)
    with self.assertRaises(PermissionError):self.approve(aid,self.owner["user_id"])
    self.assertIsNone(download_asset(self.assets,self.identity,PROJECT,aid))
    self.approve(aid)
    self.assertEqual(download_asset(self.assets,self.identity,PROJECT,aid)["content"],
                     self.txt.read_bytes())
 def test_03_infected_denied(self):
    aid=self.stage_file()
    self.assertEqual(self.scan_mock(aid,"infected")["scan_state"],"infected")
    with self.assertRaises(PermissionError):self.approve(aid)
 def test_04_scanner_error_denied(self):
    aid=self.stage_file()
    self.assertEqual(self.scan_mock(aid,"error")["scan_state"],"error")
    with self.assertRaises(PermissionError):self.approve(aid)
 def test_05_mime_mismatch_without_antivirus_claim(self):
    aid=self.stage_file()
    with sqlite3.connect(self.assets/"catalog.sqlite") as c:
        c.execute("UPDATE asset_versions SET media_type='image/png' WHERE asset_id=?",(aid,))
    state=self.scan_mock(aid)
    self.assertEqual(state["scan_state"],"infected")
    self.assertEqual(state["engine"],"file")
    with self.assertRaises(PermissionError):self.approve(aid)
 def test_06_file_changed_after_clean_scan_denied(self):
    aid=self.stage_file();self.scan_mock(aid)
    f=self.assets/("asset-"+aid+".bin")
    original=f.read_bytes();f.write_bytes(b"altered malicious bytes")
    try:
        with self.assertRaises(RuntimeError):self.approve(aid)
    finally:f.write_bytes(original)
    self.assertIsNone(download_asset(self.assets,self.identity,PROJECT,aid))
 def test_07_reviewer_membership_revoked_before_approval(self):
    aid=self.stage_file();self.scan_mock(aid)
    revoke(self.db,PROJECT,"reviewer","reviewer")
    with self.assertRaises(PermissionError):self.approve(aid)
 def test_08_revision_must_be_scanned_independently(self):
    aid=self.stage_file();self.scan_mock(aid);self.approve(aid)
    first=download_asset(self.assets,self.identity,PROJECT,aid)["content"]
    self.txt.write_text("FilmWiki synthetic revised script\n")
    v2=stage_revision(self.assets,self.db,self.txt,aid,self.owner["user_id"],"text/plain")
    self.assertEqual(v2["version"],2)
    self.assertEqual(download_asset(self.assets,self.identity,PROJECT,aid)["content"],first)
    with self.assertRaises(PermissionError):self.approve(aid)
    self.scan_mock(aid);self.approve(aid)
    self.assertEqual(download_asset(self.assets,self.identity,PROJECT,aid)["content"],
                     self.txt.read_bytes())
    self.assertTrue(audit(self.assets,aid)["valid"])
 def test_09_legacy_import_blocked_after_migration(self):
    with self.assertRaises(PermissionError):
        import_asset(self.assets,self.db,self.txt,PROJECT,self.owner["user_id"],
                     "safe.txt","text/plain","project")
 def test_10_scan_schema_idempotent(self):
    aid=self.stage_file()
    setup_security(self.assets,self.root)
    setup_security(self.assets,self.root)
    self.assertIsNone(download_asset(self.assets,self.identity,PROJECT,aid))
    self.assertEqual(self.scan_mock(aid)["scan_state"],"clean")

if __name__=="__main__":unittest.main()
