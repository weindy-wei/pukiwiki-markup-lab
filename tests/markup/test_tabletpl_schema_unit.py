"""Schema checks for the repository's JSON table catalog."""
import json
from pathlib import Path
import re
import unittest

CATALOG = Path(__file__).resolve().parents[2] / "plugin" / "tabletpl_templates"
ID = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
PLACEHOLDER = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}\Z")


def no_duplicate_keys(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("Repeated JSON key: " + key)
        values[key] = value
    return values


class TableTemplateCatalogTests(unittest.TestCase):
    def test_template_schemas(self):
        files = sorted(CATALOG.glob("*.json"))
        self.assertEqual({f.stem for f in files}, {"project_overview", "shot_card"})
        for filename in files:
            with self.subTest(name=filename.name):
                schema = json.loads(filename.read_text(encoding="utf-8"),
                                    object_pairs_hook=no_duplicate_keys)
                self.assertEqual(schema["id"], filename.stem)
                self.assertEqual(schema["version"], 1)
                self.assertIs(type(schema["columns"]), int)
                self.assertTrue(2 <= schema["columns"] <= 8)
                fields = schema["fields"]
                self.assertTrue(0 < len(fields) <= 16)
                self.assertEqual(len(set(fields)), len(fields))
                self.assertTrue(all(ID.fullmatch(x) for x in fields))
                rows = schema["rows"]
                self.assertTrue(1 <= len(rows) <= 32)
                self.assertEqual(rows[0]["kind"], "header")
                self.assertTrue(all(r["kind"] in ("header", "body") for r in rows))
                self.assertTrue(all(len(r["cells"]) == schema["columns"] for r in rows))
                used = set()
                for row in rows:
                    for cell in row["cells"]:
                        self.assertIsInstance(cell, str)
                        m = PLACEHOLDER.fullmatch(cell)
                        if m:
                            used.add(m.group(1))
                        else:
                            self.assertNotIn("|", cell)
                            self.assertNotIn("<", cell)
                            self.assertNotIn(">", cell)
                            self.assertNotIn("{{", cell)
                self.assertEqual(used, set(fields))


if __name__ == "__main__":
    unittest.main()
