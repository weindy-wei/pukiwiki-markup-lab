"""Step28 editor tests: transactions, audit chain and FTS agree on one snapshot."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/filmwiki_api"))
sys.path.insert(0,str(ROOT/"tools/table_import"))
from local_store import store
from editor_store import (initialize_copy,change,record,history,list_records,
                          verify_history, VersionConflict, InvalidEdit,DuplicateRequest)

SOURCE_SHA="a"*64
PROJECT="demo-work"

def fixture():
    records=[]
    for i in range(12):
        cells=[
            "SC_06" if i in (4,5) else "SC_"+str(i).zfill(3),
            "镜头画面，路灯下散步" if i==5 else "画面原文 "+str(i),
            12+i,
            "SC_06" if i==8 else None
        ]
        records.append({"record_id":"src_synthetic_"+str(i).zfill(4),
                        "source_row":i+2,"shot_number":cells[0],"parent_shot":cells[3],
                        "cells":[{"index":j+1,"field":f,"value":v}
                          for j,(f,v) in enumerate(zip(
                            ("镜头号","镜头内容","预算","父记录"),cells))]})
    return {
       "format":"filmwiki-import-preview",
       "source":{"filename":"synthetic-copy.xlsx","sha256":SOURCE_SHA},
       "sheets":[{"name":"第1集",
          "fields":[{"index":j+1,"name":f} for j,f in enumerate(
               ("镜头号","镜头内容","预算","父记录"))],
          "records":records}],
       "asset_references":[]
    }

class EditorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.src=self.root/"source.sqlite"
        self.work=self.root/"work.sqlite"
        store(fixture(),self.src)
        self.original_hash=hashlib.sha256(self.src.read_bytes()).hexdigest()
        initialize_copy(self.src,self.work,self.root,PROJECT,SOURCE_SHA,
                        permissions={1:[2,3]})
    def tearDown(self):self.temp.cleanup()
    def patch(self,rid="src_synthetic_0005",field=2,value="更改后的镜头：雪中的灯塔",
              version=0,reason="修正镜头描述",request_id=None):
        return change(self.work,PROJECT,1,rid,field,value,version,reason,
                      request_id or str(uuid4()))
    def test_one_transaction_revision_and_fts(self):
        rid="src_synthetic_0005"
        before=record(self.work,1,rid)
        assert before["version"]==0
        assert len(list_records(self.work,1,q="雪中的灯塔")["records"])==0
        applied=self.patch()
        self.assertEqual(applied["version"],1)
        self.assertEqual(applied["audit_written"],True)
        after=record(self.work,1,rid)
        self.assertEqual(after["cells"][1]["value"],"更改后的镜头：雪中的灯塔")
        self.assertEqual(after["version"],1)
        self.assertEqual(len(list_records(self.work,1,q="雪中的灯塔")["records"]),1)
        self.assertEqual(len(list_records(self.work,1,q="路灯下散步")["records"]),0)
        self.assertEqual(verify_history(self.work,rid)["events"],1)
        self.assertEqual(history(self.work,1,rid)["events"][0]["old_value"],
                         "镜头画面，路灯下散步")
        self.assertEqual(hashlib.sha256(self.src.read_bytes()).hexdigest(),self.original_hash)

    def test_conflict_rejects_stale_patch_without_audit(self):
        self.patch()
        with self.assertRaises(VersionConflict):self.patch(value="冲突编辑",version=0)
        self.assertEqual(verify_history(self.work)["events"],1)
        self.assertEqual(record(self.work,1,"src_synthetic_0005")["version"],1)

    def test_audit_append_only_and_no_delete(self):
        self.patch()
        with sqlite3.connect(self.work) as conn:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("DELETE FROM revision_events")
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE revision_events SET reason='bad'")
        self.assertEqual(verify_history(self.work)["events"],1)

    def test_new_revision_reverts_value_but_preserves_history(self):
        value=record(self.work,1,"src_synthetic_0005")["cells"][1]["value"]
        self.patch()
        self.patch(value=value,version=1,reason="审查后恢复原稿")
        self.assertEqual(record(self.work,1,"src_synthetic_0005")["version"],2)
        self.assertEqual(len(list_records(self.work,1,q="路灯下散步")["records"]),1)
        self.assertEqual(len(list_records(self.work,1,q="雪中的灯塔")["records"]),0)
        self.assertEqual(verify_history(self.work)["events"],2)

    def test_forbidden_id_relation_and_invalid_json(self):
        with self.assertRaises(InvalidEdit):
            self.patch(field=1,value="NEW_ID")
        with self.assertRaises(InvalidEdit):
            self.patch(field=4,value="SC_01")
        with self.assertRaises(InvalidEdit):
            self.patch(value={"injected":1})
        with self.assertRaises(InvalidEdit):
            self.patch(value="x"*10001)
        self.assertEqual(verify_history(self.work)["events"],0)

    def test_idempotency_no_duplicate_request(self):
        request=str(uuid4())
        self.patch(request_id=request)
        with self.assertRaises(DuplicateRequest):
            self.patch(version=1,value="再次修改",request_id=request)
        self.assertEqual(verify_history(self.work)["events"],1)

    def test_worker_race_one_success_one_conflict(self):
        results=[]
        def writer(value):
            try:results.append(self.patch(value=value,version=0))
            except VersionConflict:results.append("conflict")
        threads=[threading.Thread(target=writer,args=(f"并行改写{i} 画面",)) for i in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertEqual(results.count("conflict"),1)
        self.assertEqual(verify_history(self.work)["events"],1)

    def test_working_copy_never_overwrites_original_or_existing(self):
        with self.assertRaises(InvalidEdit):
            initialize_copy(self.src,self.work,self.root,PROJECT,SOURCE_SHA)
        with self.assertRaises(InvalidEdit):
            initialize_copy(self.src,self.src,self.root,PROJECT,SOURCE_SHA)
        self.assertEqual(hashlib.sha256(self.src.read_bytes()).hexdigest(),self.original_hash)

if __name__=="__main__":unittest.main()
