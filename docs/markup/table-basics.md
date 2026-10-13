
# PukiWiki Table Markup Baseline

## Purpose

Document and test the original PukiWiki table syntax.

## Test 1: Basic Table

```text
|Weapon|Attack|
|Great Sword|120|
|Long Sword|100|
```

Expected: A table with two columns and three rows.

## Test 2: Rowspan

```text
|Weapon|Attack|
|Great Sword|120|
|~|135|
```

Expected: The Great Sword cell spans two rows.

## Test 3: Colspan

```text
|>|Great Sword|
|Attack|120|
```

Expected: The Great Sword cell spans two columns.

## Test 4: Header

```text
|~Weapon|~Attack|h
|Great Sword|120|
```

Expected: A table with header cells.

## Test 5: Cell Alignment

```text
|LEFT:Weapon|RIGHT:Attack|
|LEFT:Great Sword|RIGHT:120|
```

Expected: The first column is left-aligned,
and the second column is right-aligned.

Runtime rendering test is pending.

## Reference

Upstream: https://github.com/pukiwiki/pukiwiki

Parser: lib/convert_html.php

## Status

Syntax behavior identified from upstream source code.
Runtime rendering tests are pending.

Related issue: #1
