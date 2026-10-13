# PukiWiki table markup integration tests

This Python standard-library test runner takes its source cases directly from
docs/markup/table-basics.md and checks real HTML produced by PukiWiki 1.5.4.
It tests ordinary tables, row and column spanning, headers, and cell alignment.

## Requirements and safety

- Python 3.10 or newer; no pip packages are required.
- Isolated PukiWiki test site listening only on 127.0.0.1.
- Never pass a Git checkout or production wiki as --site-dir.
- The runner creates wiki page fixtures named PukiWikiAutoTable01 to 05.
- Existing fixtures are reused only when their contents match exactly.
- Only local HTTP endpoints are accepted.
- HTML and JSON test results should be stored outside the Git repo.

## Running from WSL in a checked-out repository

First verify the five documented test definitions without writing any files:

    python3 tests/markup/test_table_rendering.py --dry-run

Then run the real PHP rendering tests against the isolated environment:

    python3 tests/markup/test_table_rendering.py \
      --site-dir /mnt/d/Administrator/Documents/DevEnvironments/PukiWiki/php81/site \
      --base-url http://127.0.0.1:8080 \
      --output-dir /mnt/d/Administrator/Documents/OperationArchive/PukiWiki/manual-table-test

Outputs: report.json and case-01.html through case-05.html in the output directory.
Subsequent runs are repeatable when test cases have not changed.

Exit status 0 means five passing render tests; 1 indicates at least one failed
case; 2 indicates invalid setup or documentation test specification.

This test does NOT change any PukiWiki PHP source or prove compatibility for all
PukiWiki functionality. PHP 8.1 is unsupported for public-facing production use.
The isolated Docker runtime is managed outside this repository, with
Manage-PukiWiki.ps1 in DevEnvironments/PukiWiki/php81.

## GitHub Actions checks

The lightweight offline GitHub Actions workflow validates Python syntax,
the Markdown baseline, and safety assertions on PRs. It does NOT run the
Nginx/PHP integration test. The live test requires a separately configured
localhost-only PHP 8.1 lab and is currently executed on WEINDY-PC.
