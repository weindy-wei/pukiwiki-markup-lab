"""Step30 offline deploy/rollback tests. All SQLite data is synthetic."""
from pathlib import Path
import hashlib,json,sqlite3,sys,tempfile,unittest,uuid
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/filmwiki_api"))
sys.path.insert(0,str(ROOT/"tools/table_import"))
from local_store import store
from editor_store import initialize_copy
from workflow_v5 import setup,batch_change
from release_v5 import prepare_release,approve_release,publish_release
from search_index import build_index,validate_index
from catalog_v2 import read_registry,read_record
from catalog_v3 import attach_validated_indices
import deploy_v6 as d
from test_filmwiki_editor_store import fixture

A="a"*64
B="b"*64
RID="src_synthetic_0008"
class Step30DeployTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        for folder in ("staging","indexes","private","editing","releases","deployments"):
            (self.root/folder).mkdir()
        self.fingerprint={}
        for alias,sha in (("demo-shortfilm",A),("demo-longshots",B)):
            file=self.root/"staging"/(alias+".sqlite")
            data=fixture();data["source"]["sha256"]=sha
            store(data,file)
            self.fingerprint[alias]=hashlib.sha256(file.read_bytes()).hexdigest()
            build_index(file,sha,alias,self.root/"indexes"/(alias+".search.sqlite"),self.root)
        registry={"version":1,"projects":[
           {"id":name,"title":name,"db":"staging/"+name+".sqlite",
            "source_sha":A if name=="demo-shortfilm" else B}
           for name in ("demo-shortfilm","demo-longshots")
        ]}
        self.registry=self.root/"private/projects.json"
        self.registry.write_text(json.dumps(registry))
        self.base_sha=hashlib.sha256(self.registry.read_bytes()).hexdigest()
        self.w=self.root/"editing/editor.sqlite"
        initialize_copy(self.root/"staging/demo-shortfilm.sqlite",self.w,self.root,
                        "demo-editor",A,permissions={1:[2,3]})
        setup(self.w,"demo-editor")
        batch_change(self.w,"demo-editor",[{
            "kind":"parent","sheet_index":1,"record_id":RID,
            "parent_record_id":"src_synthetic_0005","expected_version":0
        }],"审核明确父镜头",str(uuid.uuid4()))
        self.release=str(uuid.uuid4())
        prepare_release(self.w,self.root/"releases","demo-editor",self.release)
        approve_release(self.root/"releases",self.release)
        publish_release(self.root/"releases",self.release)
        self.patcher=patch.object(d,"_base",side_effect=self.fake_base)
        self.patcher.start()
    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()
    def fake_base(self,root):
        root=Path(root)
        base=root/"deployments"
        for part in ("versions","plans","transactions"):
            (base/part).mkdir(exist_ok=True)
        return root,base
    def get_projects(self,expected):
        result=read_registry(self.registry,self.root)
        attach_validated_indices(result,self.root/"indexes",self.root)
        self.assertEqual(list(result),expected)
        if d.ALIAS in result:
            actual=read_record(result[d.ALIAS],1,RID)["record"]["parent_link"]
            self.assertEqual(actual["parent_record_id"],"src_synthetic_0005")
            self.assertEqual(actual["status"],"resolved")
        return expected

    def test_plan_checks_snapshot_and_does_not_mutate_registry(self):
        proposed=d.plan(self.root,self.release)
        self.assertEqual(proposed["project_count"],3)
        self.assertEqual(hashlib.sha256(self.registry.read_bytes()).hexdigest(),self.base_sha)
        self.assertEqual(self.get_projects(["demo-shortfilm","demo-longshots"]),
                         ["demo-shortfilm","demo-longshots"])
        indexed=self.root/"deployments/plans"/(self.release+"-demo-published.search.sqlite")
        assert indexed.is_file()

    def test_unapproved_tampered_manifest_fail_closed(self):
        folder=self.root/"releases/published"/self.release
        file=folder/"publication.json"
        file.chmod(0o600)
        payload=json.loads(file.read_text());payload["approval_hash"]="wrong"
        file.write_text(json.dumps(payload))
        with self.assertRaises(ValueError):
            d.plan(self.root,self.release)
        self.assertEqual(hashlib.sha256(self.registry.read_bytes()).hexdigest(),self.base_sha)

    def test_live_switch_and_rollback_then_reactivate(self):
        # Mock only systemctl and network I/O, not the SQLite/register/index
        # validation; the real root controller is exercised separately.
        def fake_maintenance(root,operation):return operation()
        def fake_live(root,ids):return self.get_projects(ids)
        with patch.object(d,"_with_maintenance",side_effect=fake_maintenance), \
             patch.object(d,"_command",return_value=""), \
             patch.object(d,"_verify_live",side_effect=fake_live):
            result=d.activate(self.root,self.release)
            self.assertEqual(result["state"],"active")
            self.assertEqual(len(self.get_projects(result["new_projects"])),3)
            rolled=d.rollback(self.root,result["transaction"])
            self.assertEqual(rolled["state"],"rolled_back")
            self.assertEqual(hashlib.sha256(self.registry.read_bytes()).hexdigest(),self.base_sha)
            self.assertFalse((self.root/"indexes/demo-published.search.sqlite").exists())
            second=d.activate(self.root,self.release)
            self.assertEqual(second["state"],"active")
            self.assertNotEqual(second["transaction"],result["transaction"])
        for key,digest in self.fingerprint.items():
            self.assertEqual(hashlib.sha256(
                (self.root/"staging"/(key+".sqlite")).read_bytes()).hexdigest(),digest)

    def test_interrupted_transaction_manual_recovery(self):
        with patch.object(d,"_with_maintenance",side_effect=lambda root,operation:operation()), \
             patch.object(d,"_command",return_value=""), \
             patch.object(d,"_verify_live",side_effect=lambda root,ids:self.get_projects(ids)):
            deployed=d.activate(self.root,self.release)
            transaction=deployed["transaction"]
            txn=self.root/"deployments/transactions"/transaction
            state=json.loads((txn/"transaction.json").read_text())
            state["state"]="prepared"
            (txn/"transaction.json").write_text(json.dumps(state))
            done=d.recover(self.root,transaction)
            self.assertEqual(done["state"],"recovered_after_interruption")
            self.assertEqual(hashlib.sha256(self.registry.read_bytes()).hexdigest(),self.base_sha)

    def test_forged_alias_and_project_set_rejected(self):
        with self.assertRaises(ValueError):d.plan(self.root,self.release,alias="production-project")
        registry=json.loads(self.registry.read_text())
        registry["projects"].append({"id":"user-private","db":"staging/x.sqlite",
                                    "source_sha":A,"title":"bad"})
        self.registry.write_text(json.dumps(registry))
        with self.assertRaises(ValueError):d.plan(self.root,self.release)
        self.assertFalse((self.root/"indexes/demo-published.search.sqlite").exists())

if __name__=="__main__":unittest.main()
