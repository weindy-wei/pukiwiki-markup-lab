#!/usr/bin/env python3
"""PukiWiki table integration tests, standard library only, isolated localhost site."""
from __future__ import annotations
import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlparse
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC = REPO_ROOT / "docs" / "markup" / "table-basics.md"
FIXTURE_PREFIX = "PukiWikiAutoTable"


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[dict] = []
        self.active: dict | None = None
        self.cell: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "table":
            self.active = {"attrs": values, "rows": 0, "thead": False, "cells": []}
        elif self.active is not None and tag == "thead":
            self.active["thead"] = True
        elif self.active is not None and tag == "tr":
            self.active["rows"] += 1
        elif self.active is not None and tag in ("th", "td"):
            self.cell = {"tag": tag, "attrs": values, "text": ""}

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag in ("th", "td") and self.cell is not None and self.active is not None:
            self.cell["text"] = self.cell["text"].strip()
            self.active["cells"].append(self.cell)
            self.cell = None
        if tag == "table" and self.active is not None:
            self.tables.append(self.active)
            self.active = None


def cases_from_document(source: str) -> list[tuple[int, str, str]]:
    pattern = r"(?ms)^## Test ([1-5]): ([^\n]+)\s*\n\x60{3}text\n(.*?)\n\x60{3}"
    cases = [(int(x.group(1)), x.group(2).strip(), x.group(3).strip())
             for x in re.finditer(pattern, source)]
    if [x[0] for x in cases] != [1, 2, 3, 4, 5]:
        raise ValueError("Expected exactly five numbered Markdown code cases (1-5)")
    return cases


def assert_table(case: int, table: dict) -> None:
    cells = table["cells"]

    def has(text: str, tag: str | None = None, attr: str | None = None,
            val: str | None = None) -> bool:
        return any(c["text"] == text and (tag is None or c["tag"] == tag)
                   and (attr is None or c["attrs"].get(attr) == val) for c in cells)

    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise AssertionError(reason)

    if case == 1:
        require(table["rows"] == 3 and len(cells) == 6, "Basic table geometry mismatch")
        require(all(has(x) for x in ("Weapon", "Attack", "Great Sword", "120", "Long Sword", "100")),
                "Basic table cell content mismatch")
    elif case == 2:
        require(table["rows"] == 3 and len(cells) == 5, "Rowspan table geometry mismatch")
        require(has("Great Sword", attr="rowspan", val="2") and not has("~"),
                "Expected rowspan=2 for Great Sword")
    elif case == 3:
        require(table["rows"] == 2 and len(cells) == 3, "Colspan table geometry mismatch")
        require(has("Great Sword", attr="colspan", val="2") and not has(">"),
                "Expected colspan=2 for Great Sword")
    elif case == 4:
        require(table["thead"] and has("Weapon", tag="th") and has("Attack", tag="th"),
                "Header section or header cells missing")
    elif case == 5:
        for text, direction in (("Weapon", "left"), ("Attack", "right"),
                                ("Great Sword", "left"), ("120", "right")):
            require(has(text, attr="style", val=f"text-align:{direction};"),
                    f"Cell {text} is not aligned {direction}")
    else:
        raise ValueError(f"Unrecognized test case: {case}")


def localhost_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("Refusing non-local HTTP URL; use localhost or 127.0.0.1")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL cannot include authentication, query or fragment")
    return value.rstrip("/")


def run(args: argparse.Namespace) -> int:
    spec = args.spec.resolve(strict=True)
    content = spec.read_text(encoding="utf-8")
    cases = cases_from_document(content)
    digest = hashlib.sha256(spec.read_bytes()).hexdigest()
    print(f"SPEC={spec}")
    print(f"SPEC_SHA256={digest}")
    print(f"DOCUMENTED_CASES={len(cases)}")
    if args.dry_run:
        for num, label, _ in cases:
            print(f"CASE_{num}: READY {label}")
        print("DRY_RUN=PASS; zero writes, zero network requests")
        return 0
    if args.site_dir is None:
        raise ValueError("--site-dir is required for live integration tests")
    site = args.site_dir.resolve(strict=True)
    if not (site / "index.php").is_file() or not (site / "wiki").is_dir():
        raise ValueError("Site directory must contain index.php and wiki/")
    if (site / ".git").exists() or site == REPO_ROOT:
        raise ValueError("Refusing to create fixtures inside a Git checkout")
    base_url = localhost_url(args.base_url)
    output = args.output_dir.resolve() if args.output_dir else None
    if output is not None:
        if output == REPO_ROOT or REPO_ROOT in output.parents:
            raise ValueError("Do not store reports in the source repository")
        output.mkdir(parents=True, exist_ok=True)

    results = []
    for num, label, wiki_markup in cases:
        page = f"{FIXTURE_PREFIX}{num:02d}"
        fixture = site / "wiki" / (page.encode("utf-8").hex().upper() + ".txt")
        expected = f"* Test {num}: {label}\n\n{wiki_markup}\n"
        record = {"case": num, "label": label, "page": page, "passed": False}
        try:
            if fixture.exists():
                if fixture.read_text(encoding="utf-8") != expected:
                    raise ValueError(f"Fixture content differs, refusing overwrite: {fixture.name}")
            else:
                fixture.write_text(expected, encoding="utf-8")
            url = f"{base_url}/index.php?{quote(page)}"
            with urlopen(url, timeout=args.timeout) as response:
                record["http"] = response.status
                html = response.read().decode("utf-8", errors="replace")
            if output:
                (output / f"case-{num:02d}.html").write_text(html, encoding="utf-8")
            parsed = TableParser()
            parsed.feed(html)
            matches = [t for t in parsed.tables
                       if "style_table" in (t["attrs"].get("class") or "").split()]
            if len(matches) != 1:
                raise AssertionError(f"Expected one PukiWiki table, got {len(matches)}")
            assert_table(num, matches[0])
            record["rows"] = matches[0]["rows"]
            record["cells"] = matches[0]["cells"]
            record["passed"] = True
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        results.append(record)
        print(f"CASE_{num}: {'PASS' if record['passed'] else 'FAIL'} {label}" +
              (f" | {record['error']}" if not record["passed"] else ""))
    passed = sum(int(r["passed"]) for r in results)
    if output:
        report = {"source_spec": str(spec), "source_sha256": digest,
                  "base_url": base_url, "passed": passed,
                  "total": len(results), "results": results}
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"REPORT={output / 'report.json'}")
    print(f"TOTAL_PASSED={passed}/{len(results)}")
    return 0 if passed == len(results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--site-dir", type=Path, help="Isolated runtime, NEVER the Git checkout")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--output-dir", type=Path, help="Optional output directory outside Git")
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--dry-run", action="store_true", help="Validate docs without I/O")
    args = parser.parse_args()
    try:
        return run(args)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
