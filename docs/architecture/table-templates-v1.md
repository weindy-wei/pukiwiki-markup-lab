# PukiWiki table templates and plugin architecture, v1

## Goal

Provide reusable, version-controlled, data-only table layouts without changing
PukiWiki 1.5.4's table parser or its existing page-copying template plugin.
The separate block plugin is named tabletpl.

## Data flow

1. An editor adds a tabletpl call to the source of a PukiWiki page.
2. The tabletpl plugin resolves a short ID to a reviewed JSON file.
3. It validates named arguments against the declared required fields.
4. It builds native pipe-delimited PukiWiki table markup.
5. The original convert_html parser renders the table to HTML.

The plugin cannot write or edit the Wiki pages, and does not interpret
user-provided PHP, HTML or arbitrary Wiki markup. It returns a safe error
message rather than expanding unexpected template references.

## Usage examples

Project overview block:

    #tabletpl(project_overview,title=欧罗巴黑晶纪,genre=科幻电影,stage=筹备中,owner=周星宇)

Shot record block:

    #tabletpl(shot_card,shot=SC017,setting=欧罗巴基地,action=黑晶生长,duration=12秒)

These result in real semantic table headers and native HTML table output.
The project overview is a two-column key/value table. Shot card is a
four-column header plus exactly one data row.

## v1 JSON schema

Each file under plugin/tabletpl_templates is an independently maintained
definition, with these required properties:

- id: file basename and short lower-case slug.
- version: integer 1.
- columns: integer 2 to 8.
- fields: list of unique required argument names, at most 16.
- rows: 1 to 32 row objects; kind is header or body; each has exactly
  the declared number of cells.
- A cell is plain literal text or exactly one named placeholder, written
  as two opening braces + field name + two closing braces.

The plugin validates the layout and template input before emitting native
PukiWiki table rows. Header rows use both the h row suffix and the tilde
prefix per header cell; PukiWiki needs both for a THEAD with TH cells.

## Compatibility and constraints

- This initial implementation was tested in PukiWiki 1.5.4 with PHP 8.1.
- Values use a deliberately restricted Unicode plain-text character set.
  Wiki control tokens, HTML syntax, line breaks and plugin syntax are rejected.
  Rich formatting, explicit links, inline images and attachments are not yet
  supported. This is by design for the v1 security boundary.
- All declared fields are required, and all declared fields must be used.
- This version provides fixed rows only. Dynamic repeated rows, JSON/CSV/XLSX
  data sources and visual editors are future features.
- JSON catalog definitions are code-reviewed, Git-managed static assets, not
  editable by ordinary Wiki users. Pages hold parameter values in the plugin
  invocation, and do not receive a permanently copied snapshot.
- Catalog changes will affect all Wiki pages using an ID. Treat released
  template IDs as immutable; create an ID with a new version suffix when
  making incompatible changes.
- Do not store credentials or private data inside template JSON files.
- Block direct web access to the server-side plugin directory and catalog.
  The isolated Nginx lab already denies the plugin directory.

## Tests

Run Python offline unit tests:

    python3 -m unittest discover -s tests/markup -p 'test_*.py' -v

Run PHP syntax and compiler checks in an isolated PHP 8.1 runtime:

    php -l plugin/tabletpl.inc.php
    php tests/markup/check_tabletpl_compiler.php

Run the real HTTP integration tests against an isolated local PukiWiki site:

    python3 tests/markup/check_tabletpl_live.py \
      --site-dir /mnt/d/Administrator/Documents/DevEnvironments/PukiWiki/php81/site \
      --output-dir /mnt/d/Administrator/Documents/OperationArchive/PukiWiki/manual-tabletpl-tests

Also rerun the existing five baseline markup tests, to catch regressions in
the original table parser.

The GitHub Actions workflow runs Python offline tests plus the PHP compiler
tests within an ephemeral PHP container. It does not deploy a public website
or run the real HTTP test; real HTTP tests remain on WEINDY-PC.

## Next phases

Phase 2: map actual project/story sheets onto stable table schemas.
Phase 3: introduce typed optional fields, multi-row data sets and references
to uploaded images, after designing access control and editing semantics.
Phase 4: schema migration and import/export with explicit snapshot policy.
An admin UI should only follow stable, tested schemas and permission rules.
