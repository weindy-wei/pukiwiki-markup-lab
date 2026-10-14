"""Offline reconciliation tests: no real FilmWiki data and no systemctl."""
from pathlib import Path
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/filmwiki_api"))
sys.path.insert(0,str(ROOT/"tools/table_import"))
from local_store import store
from index_reconciler import reconcile, locked_file
from search_index import validate_index

SHA="d"*64

class IndexReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/"staging").mkdir()
        (self.root/"indexes").mkdir()
        (self.root/"private").mkdir()
        (self.root/"reports").mkdir()
        self.db=self.root/"staging"/"one.sqlite"
        data={
          "format":"filmwiki-import-preview","source":{"sha256":SHA,"filename":"synthetic.xlsx"},
          "sheets":[{"name":"镜头",
                     "fields":[{"index":1,"name":"镜头号"},{"index":2,"name":"描述"}],
                     "records":[{"record_id":"src_demo_"+str(i),"source_row":i+2,
                                 "shot_number":"SC_"+str(i),
                                 "cells":[{"index":1,"field":"镜头号","value":"SC_"+str(i)},
                                          {"index":2,"field":"描述","value":"合成街景画面"+str(i)}]}
                                for i in range(9)]}],"asset_references":[]}
        store(data,self.db)
        self.canonical=self.db.read_bytes()
        self.registry=self.root/"private"/"projects.json"
        self.registry.write_text(json.dumps({"version":1,"projects":[{
          "id":"demo","title":"合成","db":"staging/one.sqlite","source_sha":SHA}]}))
        self.index=self.root/"indexes"/"demo.search.sqlite"
        self.report=self.root/"reports"/"status.json"

    def tearDown(self):self.tmp.cleanup()

    def check(self,apply=False):
        return reconcile(self.root,self.registry,self.root/"indexes",apply,self.report)

    def test_dry_run_does_not_touch_source_or_build_index(self):
        result=self.check()
        self.assertEqual(result["changed"][0]["status"],"needs_rebuild")
        self.assertFalse(result["restart_required"])
        self.assertFalse(self.index.exists())
        self.assertEqual(self.canonical,self.db.read_bytes())
        self.assertEqual(json.loads(self.report.read_text())["mode"],"dry_run")

    def test_missing_index_build_then_noop(self):
        r=self.check(apply=True)
        self.assertEqual(r["changed"][0]["status"],"rebuilt")
        self.assertTrue(r["restart_required"])
        self.assertTrue(validate_index(self.db,SHA,"demo",self.index))
        idx_data=self.index.read_bytes()
        r=self.check(apply=True)
        self.assertFalse(r["restart_required"])
        self.assertEqual(r["unchanged"],["demo"])
        self.assertEqual(self.index.read_bytes(),idx_data)
        self.assertEqual(self.db.read_bytes(),self.canonical)

    def test_changed_source_rebuilds_and_saves_prior_sidecar(self):
        self.check(apply=True)
        before=self.index.read_bytes()
        os.utime(self.db,ns=(self.db.stat().st_atime_ns,self.db.stat().st_mtime_ns+10_000_000))
        self.assertNotEqual(self.check()["changed"],[])
        r=self.check(apply=True)
        self.assertTrue(r["restart_required"])
        self.assertTrue(r["changed"][0]["prior_index_preserved"])
        backups=list((self.root/"indexes"/"history").glob("demo-*.sqlite"))
        self.assertEqual(len(backups),1)
        self.assertEqual(backups[0].read_bytes(),before)
        self.assertTrue(validate_index(self.db,SHA,"demo",self.index))
        self.assertEqual(self.db.read_bytes(),self.canonical)

    def test_failed_build_keeps_old_index_and_prevents_restart(self):
        self.check(apply=True)
        old=self.index.read_bytes()
        os.utime(self.db,ns=(self.db.stat().st_atime_ns,self.db.stat().st_mtime_ns+10_000_000))
        with mock.patch("index_reconciler.build_index",side_effect=RuntimeError("synthetic failure")):
            r=self.check(apply=True)
        self.assertEqual(len(r["failures"]),1)
        self.assertEqual(self.index.read_bytes(),old)
        self.assertFalse(r["restart_required"])
        self.assertEqual(self.db.read_bytes(),self.canonical)

    def test_locked_reconciler_prevents_double_run(self):
        with locked_file(self.root/"indexes"):
            with self.assertRaises(BlockingIOError):
                self.check(apply=True)
        self.assertFalse(self.index.exists())

    def test_registry_cannot_select_external_database(self):
        content=json.loads(self.registry.read_text())
        content["projects"][0]["db"]="../outside.sqlite"
        self.registry.write_text(json.dumps(content))
        with self.assertRaises((ValueError,FileNotFoundError)):
            self.check(apply=True)
        self.assertEqual(self.canonical,self.db.read_bytes())


if __name__=="__main__":unittest.main()
