"""Validates that tabletpl converter emits a candidate, never installs it."""
import pathlib
import sys
import unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools/table_import"))
from xlsx_to_tabletpl import convert, acceptable

class ImportConverterTests(unittest.TestCase):
    def test_reject_wiki_control_tokens(self):
        for value in ("A|B","<script>","LEFT:abc","ok\ninclude","a#b",""):
            self.assertFalse(acceptable(value))
    def test_convert_simple_sheet(self):
        preview={"sheets":[{"name":"故事大纲","truncated":False,"preview_rows":[
            {"source_row":1,"cells":{"1":"镜头","2":"状态"},"provisional_row_id":"imp_header"},
            {"source_row":2,"cells":{"1":"SC02-001","2":"制作中"},"provisional_row_id":"imp_123"},
        ]}]}
        result=convert(preview,"故事大纲","shot_test_v2")
        self.assertEqual(result["version"],2)
        self.assertEqual(result["columns"],2)
        self.assertEqual(result["records"][0]["row_id"],"imp_123")
        self.assertEqual(result["records"][0]["c2"],"制作中")
    def test_reject_blank_cells(self):
        preview={"sheets":[{"name":"Sheet1","truncated":False,"preview_rows":[
            {"source_row":1,"cells":{"1":"Name","2":"Value"},"provisional_row_id":"imp_header"},
            {"source_row":2,"cells":{"1":"A"},"provisional_row_id":"imp_123"},
        ]}]}
        with self.assertRaises(ValueError):
            convert(preview,"Sheet1","invalid_test")
if __name__=="__main__":
    unittest.main()
