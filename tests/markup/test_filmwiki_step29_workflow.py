"""Step29 offline transactional multi-record, ID-based links, RBAC, release tests."""
from pathlib import Path
import hashlib,json,sqlite3,sys,tempfile,unittest,uuid
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/filmwiki_api"))
sys.path.insert(0,str(ROOT/"tools/table_import"))
from local_store import store
from editor_store import initialize_copy,record,history,list_records,verify_history,InvalidEdit,VersionConflict
from workflow_v5 import setup,batch_change,relation,ACTOR
from release_v5 import prepare_release,approve_release,publish_release,inspect_release
from test_filmwiki_editor_store import fixture,PROJECT,SOURCE_SHA

class Step29Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.source=self.root/"source.sqlite"
        self.working=self.root/"editor.sqlite"
        self.release_root=self.root/"releases"
        self.release_root.mkdir(mode=0o700)
        store(fixture(),self.source)
        self.source_sha=hashlib.sha256(self.source.read_bytes()).hexdigest()
        initialize_copy(self.source,self.working,self.root,PROJECT,SOURCE_SHA,permissions={1:[2,3]})
        setup(self.working,PROJECT)
    def tearDown(self):self.tmp.cleanup()
    def item(self,i,expected=0,value=None):
        return {"kind":"cell","sheet_index":1,"record_id":"src_synthetic_"+str(i).zfill(4),
                "column_index":2,"value":value or "新增动作画面"+str(i),
                "expected_version":expected}
    def batch(self,items):
        return batch_change(self.working,PROJECT,items,"批量镜头审核修订",str(uuid.uuid4()))
    def parent(self,parent,expected=0):
        return {"kind":"parent","sheet_index":1,"record_id":"src_synthetic_0008",
                "parent_record_id":parent,"expected_version":expected}

    def test_batch_atomic_success_index_audit(self):
        out=self.batch([self.item(1),self.item(2),self.item(3)])
        self.assertEqual(len(out["changes"]),3)
        for i in (1,2,3):
            r=record(self.working,1,f"src_synthetic_{i:04d}")
            self.assertEqual(r["version"],1)
            self.assertEqual(r["cells"][1]["value"],"新增动作画面"+str(i))
        self.assertEqual(len(list_records(self.working,1,q="新增动作画面")["records"]),3)
        self.assertEqual(verify_history(self.working)["events"],3)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),self.source_sha)

    def test_failed_mid_batch_rolls_back_all(self):
        items=[self.item(1),self.item(2)]
        items[1]["column_index"]=1
        with self.assertRaises(InvalidEdit):self.batch(items)
        self.assertEqual(record(self.working,1,"src_synthetic_0001")["version"],0)
        self.assertEqual(verify_history(self.working)["events"],0)
        with sqlite3.connect(self.working) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM workflow_batches").fetchone()[0],0)

    def test_stale_member_causes_total_conflict(self):
        self.batch([self.item(2)])
        with self.assertRaises(VersionConflict):self.batch([self.item(1),self.item(2)])
        self.assertEqual(record(self.working,1,"src_synthetic_0001")["version"],0)
        self.assertEqual(verify_history(self.working)["events"],1)

    def test_duplicate_record_batch_rejected(self):
        with self.assertRaises(InvalidEdit):self.batch([self.item(1),self.item(1)])
        self.assertEqual(verify_history(self.working)["events"],0)

    def test_parent_requires_explicit_candidate_id(self):
        before=relation(self.working,1,"src_synthetic_0008")
        self.assertEqual(before["relation"]["import_status"],"ambiguous")
        self.assertEqual(set(before["relation"]["candidate_record_ids"]),
                          {"src_synthetic_0004","src_synthetic_0005"})
        for bad in ("SC_06","src_synthetic_0003","src_synthetic_0008"):
            with self.assertRaises(InvalidEdit):self.batch([self.parent(bad)])
        updated=self.batch([self.parent("src_synthetic_0005")])
        self.assertEqual(updated["changes"][0]["kind"],"parent")
        after=relation(self.working,1,"src_synthetic_0008")
        self.assertEqual(after["relation"]["parent_record_id"],"src_synthetic_0005")
        self.assertEqual(after["relation"]["effective_status"],"resolved")
        self.assertEqual(verify_history(self.working)["events"],1)

    def test_atomic_cell_and_parent_in_same_batch(self):
        self.batch([self.item(2),self.parent("src_synthetic_0004")])
        self.assertEqual(record(self.working,1,"src_synthetic_0002")["version"],1)
        self.assertEqual(relation(self.working,1,"src_synthetic_0008")["relation"]["parent_record_id"],
                         "src_synthetic_0004")
        self.assertEqual(verify_history(self.working)["events"],2)

    def test_project_role_gate_denies_editor(self):
        with sqlite3.connect(self.working) as con:
            con.execute("DELETE FROM workflow_role_grants WHERE role='editor'")
        with self.assertRaises(PermissionError):self.batch([self.item(1)])
        self.assertEqual(verify_history(self.working)["events"],0)

    def test_prepare_approve_publish_immutable_and_no_live_deploy(self):
        self.batch([self.item(1),self.parent("src_synthetic_0005")])
        rid=str(uuid.uuid4())
        prepared=prepare_release(self.working,self.release_root,PROJECT,rid)
        self.assertEqual(prepared["evidence"]["audit_events"],2)
        with self.assertRaises(InvalidEdit):publish_release(self.release_root,rid)
        self.batch([self.item(2)]) # new edit stays only in working copy
        approved=approve_release(self.release_root,rid)
        self.assertEqual(approved["release_id"],rid)
        published=publish_release(self.release_root,rid)
        self.assertFalse(published["http_deployed"])
        self.assertEqual(inspect_release(self.release_root,rid)["evidence"]["audit_events"],2)
        published_snap=self.release_root/"published"/rid/"snapshot.sqlite"
        self.assertEqual(record(published_snap,1,"src_synthetic_0002")["version"],0)
        self.assertEqual(record(self.working,1,"src_synthetic_0002")["version"],1)
        with self.assertRaises(FileExistsError):prepare_release(self.working,self.release_root,PROJECT,rid)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),self.source_sha)

    def test_unauthorized_reviewer_cannot_approve(self):
        with sqlite3.connect(self.working) as c:
            c.execute("DELETE FROM workflow_role_grants WHERE role='reviewer'")
        rid=str(uuid.uuid4())
        prepare_release(self.working,self.release_root,PROJECT,rid)
        with self.assertRaises(PermissionError):
            approve_release(self.release_root,rid)
        self.assertFalse((self.release_root/"published"/rid).exists())

    def test_tampered_prepared_copy_rejected(self):
        rid=str(uuid.uuid4())
        prepare_release(self.working,self.release_root,PROJECT,rid)
        original=self.release_root/"prepared"/rid/"snapshot.sqlite"
        with original.open("ab") as f:f.write(b"tamper")
        with self.assertRaises(InvalidEdit):approve_release(self.release_root,rid)
        self.assertFalse((self.release_root/"published"/rid).exists())

if __name__=="__main__":unittest.main()
