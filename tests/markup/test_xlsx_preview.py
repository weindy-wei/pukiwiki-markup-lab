"""Offline smoke tests for XLSX preview, no user workbook required."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("xlsx_preview", ROOT/"tools/table_import/xlsx_preview.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

WORKBOOK = """<?xml version="1.0" encoding="utf-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="故事大纲" sheetId="1" r:id="rId1"/></sheets></workbook>"""
REL = """<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
Target="worksheets/sheet1.xml"/></Relationships>"""
SHEET = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>
<row r="1"><c r="A1" t="inlineStr"><is><t>镜头</t></is></c><c r="B1" t="inlineStr"><is><t>状态</t></is></c></row>
<row r="2"><c r="A2" t="inlineStr"><is><t>SC02-001</t></is></c><c r="B2" t="inlineStr"><is><t>制作中</t></is></c></row>
</sheetData></worksheet>"""

class XlsxImportTests(unittest.TestCase):
    def test_read_only_import_preview(self):
        with tempfile.TemporaryDirectory() as d:
            filename = Path(d)/"input.xlsx"
            with zipfile.ZipFile(filename,"w") as z:
                z.writestr("xl/workbook.xml", WORKBOOK)
                z.writestr("xl/_rels/workbook.xml.rels", REL)
                z.writestr("xl/worksheets/sheet1.xml", SHEET)
            before = filename.read_bytes()
            result = mod.inspect(filename)
            self.assertFalse(result["wiki_write"])
            self.assertEqual(result["status"], "preview_only")
            self.assertEqual(len(result["sheets"]), 1)
            sheet = result["sheets"][0]
            self.assertEqual(sheet["name"], "故事大纲")
            self.assertEqual(sheet["source_rows"], 2)
            self.assertEqual(sheet["preview_rows"][1]["cells"]["1"], "SC02-001")
            self.assertEqual(sheet["preview_rows"][1]["cells"]["2"], "制作中")
            self.assertTrue(sheet["preview_rows"][1]["provisional_row_id"].startswith("imp_"))
            self.assertFalse(sheet["import_ready"])
            self.assertEqual(filename.read_bytes(), before)

    def test_archive_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            filename = Path(d)/"bad.xlsx"
            with zipfile.ZipFile(filename,"w") as z:
                z.writestr("../escape", "unsafe")
            with self.assertRaises(ValueError):
                mod.inspect(filename)

if __name__ == "__main__":
    unittest.main()
