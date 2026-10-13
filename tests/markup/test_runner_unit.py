"""Offline unit tests for the PukiWiki table integration test runner."""
from pathlib import Path
import tempfile
import unittest

from test_table_rendering import (
    DEFAULT_SPEC, TableParser, assert_table, cases_from_document, localhost_url,
    run
)
from argparse import Namespace


class OfflineRunnerTests(unittest.TestCase):
    def test_document_contains_five_cases(self):
        cases = cases_from_document(DEFAULT_SPEC.read_text(encoding="utf-8"))
        self.assertEqual([c[0] for c in cases], [1, 2, 3, 4, 5])

    def test_bad_markdown_rejected(self):
        with self.assertRaises(ValueError):
            cases_from_document("## Test 1: Basic Table\n")

    def test_external_url_rejected(self):
        for url in ("https://127.0.0.1:8080", "http://example.com:8080",
                    "http://user:pass@localhost:8080"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    localhost_url(url)

    def test_local_url_accepted(self):
        self.assertEqual(localhost_url("http://127.0.0.1:8080/"),
                         "http://127.0.0.1:8080")

    def test_fixture_refuses_git_checkout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "index.php").write_text("", encoding="utf-8")
            (root / "wiki").mkdir()
            (root / ".git").mkdir()
            args = Namespace(spec=DEFAULT_SPEC, site_dir=root, base_url="http://localhost:8080",
                             output_dir=None, timeout=1, dry_run=False)
            with self.assertRaises(ValueError):
                run(args)

    def test_parser_and_rowspan_assertion(self):
        html = (
            '<table class="style_table">'
            '<tr><td>Weapon</td><td>Attack</td></tr>'
            '<tr><td rowspan="2">Great Sword</td><td>120</td></tr>'
            '<tr><td>135</td></tr>'
            '</table>'
        )
        parser = TableParser()
        parser.feed(html)
        self.assertEqual(len(parser.tables), 1)
        assert_table(2, parser.tables[0])
        parser.tables[0]["cells"][2]["attrs"]["rowspan"] = "3"
        with self.assertRaises(AssertionError):
            assert_table(2, parser.tables[0])

    def test_missing_alignment_detected(self):
        html = (
            '<table class="style_table">'
            '<tr><td style="text-align:left;">Weapon</td><td style="text-align:right;">Attack</td></tr>'
            '<tr><td style="text-align:left;">Great Sword</td><td style="text-align:left;">120</td></tr>'
            '</table>'
        )
        parser = TableParser()
        parser.feed(html)
        with self.assertRaises(AssertionError):
            assert_table(5, parser.tables[0])


if __name__ == "__main__":
    unittest.main()
