"""Step35 synthetic lifecycle, old HTTP gate, immutable versions, references and audit."""
import hashlib,sqlite3,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"tools/filmwiki_api"),str(ROOT/"tools/table_import")]
from local_store import store
from editor_store import initialize_copy
from workflow_v5 import setup
from test_filmwiki_editor_store import fixture,SOURCE_SHA
from collab_auth import create_store,create_user,grant
from asset_catalog_v7 import list_assets,download_asset
from asset_lifecycle_v8 import (setup_lifecycle,stage_new,stage_revision,review,
  trash_restore,link,audit)
PROJECT="demo-editor"
class Step35Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/"private").mkdir()
        self.stage=self.root/"staging/synthetic-assets"
        self.stage.mkdir(parents=True)
        self.source=self.stage/"script-v1.txt"
        self.source.write_text("FilmWiki Step35 synthetic scene one",encoding="utf8")
        self.source2=self.stage/"script-v2.txt"
        self.source2.write_text("FilmWiki Step35 synthetic scene revised",encoding="utf8")
        self.accounts=self.root/"private/accounts.sqlite"
        create_store(self.accounts,self.root)
        self.owner=create_user(self.accounts,"director","Director!2026Valid")
        self.reviewer=create_user(self.accounts,"reviewer","Reviewer!2026Valid")
        self.viewer=create_user(self.accounts,"reader01","Reader!2026Valid")
        self.outsider=create_user(self.accounts,"outsider","Outsider!2026Valid")
        for user,role in (("director","owner"),("reviewer","reviewer"),("reader01","viewer")):
            grant(self.accounts,PROJECT,user,role)
        src=self.root/"source.sqlite";store(fixture(),src)
        self.editor=self.root/"editor.sqlite"
        initialize_copy(src,self.editor,self.root,PROJECT,SOURCE_SHA,permissions={1:[2,3]})
        setup(self.editor,PROJECT)
        self.assets=self.root/"private/assets"
        setup_lifecycle(self.assets,self.root)
        self.director_identity={
          "user_id":self.owner["user_id"],"memberships":[{"project_id":PROJECT,"role":"owner"}]}
        self.reader_identity={
          "user_id":self.viewer["user_id"],"memberships":[{"project_id":PROJECT,"role":"viewer"}]}
    def new(self):
        return stage_new(self.assets,self.accounts,self.source,PROJECT,
            self.owner["user_id"],"script-v1.txt","text/plain","project")
    def accept(self,asset_id):
        return review(self.assets,self.accounts,asset_id,
            self.reviewer["user_id"],"approved")
    def test_01_pending_never_downloaded_by_legacy_api(self):
        first=self.new()
        self.assertEqual(first["state"],"pending")
        self.assertIsNone(download_asset(self.assets,self.director_identity,PROJECT,first["asset_id"]))
        self.assertIsNone(download_asset(self.assets,self.reader_identity,PROJECT,first["asset_id"]))
        self.assertEqual(list_assets(self.assets,self.director_identity,PROJECT),[])
        self.assertEqual(audit(self.assets)["events"],1)
    def test_02_approval_and_private_permission_inheritance(self):
        aid=self.new()["asset_id"]
        self.accept(aid)
        data=download_asset(self.assets,self.reader_identity,PROJECT,aid)
        self.assertEqual(data["content"],self.source.read_bytes())
        private=stage_new(self.assets,self.accounts,self.source,PROJECT,
                  self.owner["user_id"],"script-v1.txt","text/plain","private")
        self.accept(private["asset_id"])
        self.assertIsNone(download_asset(self.assets,self.reader_identity,PROJECT,private["asset_id"]))
        self.assertIsNotNone(download_asset(self.assets,self.director_identity,PROJECT,private["asset_id"]))
        self.assertEqual(len(list_assets(self.assets,self.reader_identity,PROJECT)),1)
    def test_03_review_unauthorized_and_rejection(self):
        aid=self.new()["asset_id"]
        with self.assertRaises(PermissionError):
            review(self.assets,self.accounts,aid,self.viewer["user_id"],"approved")
        review(self.assets,self.accounts,aid,self.reviewer["user_id"],"rejected")
        self.assertIsNone(download_asset(self.assets,self.director_identity,PROJECT,aid))
        with self.assertRaises(ValueError):self.accept(aid)
        self.assertEqual(audit(self.assets)["events"],2)
    def test_04_version_upgrade_without_removing_previous_bytes(self):
        aid=self.new()["asset_id"];self.accept(aid)
        v1=(self.assets/("asset-"+aid+".bin")).read_bytes()
        next_version=stage_revision(self.assets,self.accounts,self.source2,aid,
                                    self.owner["user_id"],"text/plain")
        self.assertEqual(next_version["version"],2)
        self.assertEqual(download_asset(self.assets,self.reader_identity,PROJECT,aid)["content"],v1)
        self.accept(aid)
        self.assertEqual(download_asset(self.assets,self.reader_identity,PROJECT,aid)["content"],
                         self.source2.read_bytes())
        self.assertEqual((self.assets/("asset-"+aid+".bin")).read_bytes(),v1)
        with sqlite3.connect(self.assets/"catalog.sqlite") as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM asset_versions WHERE asset_id=?",
                                         (aid,)).fetchone()[0],2)
    def test_05_rejected_revision_reserves_version(self):
        aid=self.new()["asset_id"];self.accept(aid)
        stage_revision(self.assets,self.accounts,self.source2,aid,
                       self.owner["user_id"],"text/plain")
        review(self.assets,self.accounts,aid,self.reviewer["user_id"],"rejected")
        next_=stage_revision(self.assets,self.accounts,self.source2,aid,
                             self.owner["user_id"],"text/plain")
        self.assertEqual(next_["version"],3)
        self.assertEqual(download_asset(self.assets,self.reader_identity,PROJECT,aid)["content"],
                         self.source.read_bytes())
        self.accept(aid)
        self.assertEqual(download_asset(self.assets,self.reader_identity,PROJECT,aid)["content"],
                         self.source2.read_bytes())
    def test_06_soft_delete_and_restore_rechecks_hash(self):
        aid=self.new()["asset_id"];self.accept(aid)
        trash_restore(self.assets,self.accounts,aid,self.owner["user_id"])
        self.assertIsNone(download_asset(self.assets,self.reader_identity,PROJECT,aid))
        f=self.assets/("asset-"+aid+".bin")
        old=f.read_bytes();f.write_bytes(b"BAD")
        try:
            with self.assertRaises(RuntimeError):
                trash_restore(self.assets,self.accounts,aid,self.owner["user_id"],restore=True)
        finally:f.write_bytes(old)
        trash_restore(self.assets,self.accounts,aid,self.owner["user_id"],restore=True)
        self.assertIsNotNone(download_asset(self.assets,self.reader_identity,PROJECT,aid))
    def test_07_reference_to_real_synthetic_record(self):
        aid=self.new()["asset_id"];self.accept(aid)
        target="src_synthetic_0001"
        result=link(self.assets,self.accounts,self.editor,aid,
                    self.owner["user_id"],target,"illustrates")
        self.assertTrue(result["linked"])
        with self.assertRaises(ValueError):
            link(self.assets,self.accounts,self.editor,aid,self.owner["user_id"],
                 "src_nonexistent","illustrates")
        link(self.assets,self.accounts,self.editor,aid,self.owner["user_id"],
             target,"illustrates",remove=True)
        self.assertTrue(audit(self.assets)["valid"])
    def test_08_audit_tampering_is_detected(self):
        aid=self.new()["asset_id"];self.accept(aid)
        self.assertEqual(audit(self.assets,aid)["events"],2)
        with sqlite3.connect(self.assets/"catalog.sqlite") as c:
            c.execute("UPDATE asset_events SET action='forged' WHERE asset_id=? AND action='stage-new'",
                      (aid,))
        with self.assertRaises(RuntimeError):audit(self.assets,aid)
    def test_09_staging_path_validation(self):
        outsider=self.root/"private/wrong.txt"
        outsider.write_text("payload")
        with self.assertRaises(ValueError):
            stage_new(self.assets,self.accounts,outsider,PROJECT,self.owner["user_id"],
                      "wrong.txt","text/plain","project")
    def test_10_legacy_existing_assets_remain_approved(self):
        from asset_catalog_v7 import import_asset
        # Old synthetic fixture inserted before lifecycle migration.
        fresh=self.root/"private/legacy"
        from asset_catalog_v7 import setup_assets
        setup_assets(fresh,self.root)
        legacy=import_asset(fresh,self.accounts,self.source,PROJECT,
            self.owner["user_id"],"script-v1.txt","text/plain","project")
        setup_lifecycle(fresh,self.root)
        self.assertEqual(download_asset(fresh,self.reader_identity,PROJECT,
                                        legacy["asset_id"])["content"],self.source.read_bytes())
        self.assertEqual(audit(fresh)["events"],0)

if __name__=="__main__":unittest.main()
