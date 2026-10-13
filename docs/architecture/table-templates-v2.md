# Dynamic table datasets v0.2

This is an additive extension of `#tabletpl`; v1 templates and the original PukiWiki parser remain unchanged.

## Usage

```text
#tabletpl(shot_register_v2)
```

The reviewed JSON catalog entry defines `version: 2`, column headers, typed fields, and 1-100 records. Each record has a stable `row_id` and exactly one value for each declared field. Column types are text, number, date, enum, and asset_ref. The last type is a **reference token only** (`asset_` followed by 64 lowercase hex digits), not an HTML image renderer. All output is restricted to plain text and compiled into native PukiWiki table markup. Dataset templates refuse inline field overrides.

A `row_id` preserves the identity of a record across reordering but is not emitted as an extra visible table column; downstream importers must retain it in the dataset. Catalog JSON is reviewed source code and not a user-upload mechanism. Every released schema should have its own immutable ID.

## Scope and security

v0.2 supports variable-length JSON-defined data, typed validation, duplicate row rejection and safe encoding boundaries. It is **not yet** a browser editor, live database backend, uploaded Excel importer or interactive image renderer. Existing v1 templates retain their explicit named parameters.

Keep the plugin catalog blocked from direct web access. Test PHP syntax and both compiler suites, Python catalog tests, and local loopback HTTP rendering before merge.

```sh
php -l plugin/tabletpl.inc.php
php tests/markup/check_tabletpl_compiler.php
php tests/markup/check_tabletpl_v2.php
python3 -m unittest discover -s tests/markup -p 'test_*.py' -v
```
