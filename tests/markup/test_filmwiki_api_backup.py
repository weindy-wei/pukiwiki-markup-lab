"""SQLite snapshot to NAS-bytes archival tests, all in temp directories."""
from pathlib import Path
import importlib.util
import json
import sqlite3
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("backup_module",ROOT/"tools/filmwiki_api/backup_snapshot.py")
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class BackupTests(unittest.TestCase):
    def test_consistent_snapshot_verified_and_original_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            local=Path(folder)/"local"
            nas=Path(folder)/"nas"
            local.mkdir();nas.mkdir()
            db=local/"source.sqlite"
            with sqlite3.connect(db) as c:
                c.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
                c.execute("INSERT INTO sample(value) VALUES(?)",("SC_06",))
            before=mod.file_sha256(db)
            report=mod.backup_snapshot(db,local,nas)
            output=nas/report["snapshot_file"]
            self.assertTrue(output.is_file())
            self.assertEqual(mod.file_sha256(db),before)
            self.assertEqual(mod.file_sha256(output),report["sha256"])
            self.assertEqual(report["integrity_check"],"ok")
            manifest=json.loads((nas/(output.name+".json")).read_text())
            self.assertEqual(manifest["sha256"],report["sha256"])
            with sqlite3.connect(output) as c:
                self.assertEqual(c.execute("SELECT value FROM sample").fetchone()[0],"SC_06")

    def test_refuse_database_outside_private_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            local=root/"local";nas=root/"nas"
            local.mkdir();nas.mkdir()
            source=root/"outside.sqlite"
            with sqlite3.connect(source) as c:c.execute("CREATE TABLE sample(n INTEGER)")
            with self.assertRaises(ValueError):
                mod.backup_snapshot(source,local,nas)

if __name__=="__main__":
    unittest.main()
