"""SQLite staging tests: preserve 17 fields, nulls, empty sheets, duplicate labels."""
import copy
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("filmwiki_local_store",ROOT/"tools/table_import/local_store.py")
store=importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)

SOURCE="0"*64
FIELDS=["镜头号","分镜","镜头视频","场景","出场角色","景别","拍摄角度","运镜",
        "镜头内容","角色动作和表演","台词和声音","时长（秒）","转场方式",
        "生图提示词","视频提示词","备注","父记录"]

def row(number,label,parent=None):
    values=[None]*len(FIELDS)
    values[0]=label
    values[11]="12-18 秒" if number==2 else "6"
    values[-1]=parent
    return {"record_id":"src_"+str(number).zfill(24),
            "source_row":number,
            "shot_number":label,"parent_shot":parent,
            "cells":[{"index":i+1,"field":FIELDS[i],"value":val} for i,val in enumerate(values)]}

def make_data():
    a=[row(2,"SC_01"),row(3,"SC_06"),row(4,"SC_06"),row(5,"SC_01_01","SC_01"),
       row(6,"SC_06_01","SC_06"),row(7,"SC_99_01","SC_99")]
    return {
      "format":"filmwiki-import-preview","version":"0.1",
      "source":{"filename":"fictional.xlsx","sha256":SOURCE},
      "sheets":[
       {"name":"第1集","fields":[{"index":i+1,"name":name} for i,name in enumerate(FIELDS)],
        "records":a},
       {"name":"道具设定","fields":[{"index":1,"name":"道具名称"},{"index":2,"name":"道具资产"}],
        "records":[]},
      ],
      "asset_references":[
       {"ref_id":"assetref_001","record_id":a[1]["record_id"],
        "filename":"image.png","column":2,"status":"unresolved"}],
      "warnings":[{"issue":"duplicate_shot_numbers","locations":{"SC_06":[3,4]}}]
    }

class LocalStoreTests(unittest.TestCase):
    def test_realistic_full_width_roundtrip(self):
        source=make_data()
        status=store.summary(source)
        self.assertEqual(status["fields"],19)
        self.assertEqual(status["records"],6)
        self.assertEqual(status["empty_sheets"],["道具设定"])
        self.assertEqual(status["parent_links"],{"resolved":1,"ambiguous":1,"missing":1})
        self.assertEqual(status["asset_refs_unresolved"],1)
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/"filmwiki.sqlite"
            self.assertFalse(db.exists())
            self.assertEqual(store.store(source,db),"imported")
            self.assertEqual(store.store(source,db),"already_imported")
            con=sqlite3.connect(db)
            self.assertEqual(con.execute("SELECT count(*) FROM fields").fetchone()[0],19)
            self.assertEqual(con.execute("SELECT count(*) FROM records").fetchone()[0],6)
            self.assertEqual(con.execute("SELECT count(*) FROM cells").fetchone()[0],102)
            self.assertEqual(con.execute("SELECT count(*) FROM cells WHERE is_null=1").fetchone()[0]>60,True)
            self.assertEqual(con.execute("SELECT count(*) FROM asset_references WHERE status='unresolved'").fetchone()[0],1)
            self.assertEqual(dict(con.execute("SELECT status,count(*) FROM shot_parent_links GROUP BY status").fetchall()),
                             {"ambiguous":1,"missing":1,"resolved":1})
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0],"ok")
            con.close()
            view=store.projection(db,SOURCE,"第1集",["镜头号","场景","时长（秒）","父记录"])
            self.assertEqual(view["underlying_total_fields"],17)
            self.assertEqual(len(view["rows"]),6)
            self.assertIsNone(view["rows"][0]["values"][1])
            self.assertEqual(view["rows"][0]["values"][0],"SC_01")
            self.assertEqual(view["rows"][0]["values"][2],"12-18 秒")
            self.assertEqual(view["rows"][2]["values"][0],"SC_06")
            changed=copy.deepcopy(source)
            changed["sheets"][0]["records"][0]["cells"][1]["value"]="altered"
            with self.assertRaises(ValueError):
                store.store(changed,db)
            con=sqlite3.connect(db)
            self.assertEqual(con.execute("SELECT count(*) FROM records").fetchone()[0],6)
            con.close()

    def test_reject_inconsistent_schema(self):
        source=make_data()
        source["sheets"][0]["records"][0]["cells"].pop()
        with self.assertRaisesRegex(ValueError,"all fields"):
            store.summary(source)

    def test_duplicate_record_id_rejected(self):
        source=make_data()
        source["sheets"][0]["records"][1]["record_id"]=source["sheets"][0]["records"][0]["record_id"]
        with self.assertRaises(ValueError):
            store.summary(source)

    def test_projection_rejects_invalid_column_selection(self):
        source=make_data()
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/"view.sqlite"
            store.store(source,db)
            with self.assertRaises(ValueError):
                store.projection(db,SOURCE,"第1集",["不存在"])
            with self.assertRaises(ValueError):
                store.projection(db,SOURCE,"第1集",FIELDS[:9])

if __name__=="__main__":
    unittest.main()
