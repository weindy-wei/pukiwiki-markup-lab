# FilmWiki XLSX Import Adapter v0.1

This first step is **inventory only**, not an importer and not a PukiWiki renderer.

Run:
```sh
python3 tools/table_import/xlsx_inventory.py /path/to/workbook.xlsx --output /tmp/wiki-xlsx-inventory.json
```

Inventory reports worksheet names and physical row counts, OOXML drawing anchors (1-based row and column), media entries and SHA-256 fingerprints, plus embedded media without a resolved drawing anchor. Does not extract pictures to disk, modify the workbook, change Wiki pages or guess relationships. It deliberately refuses overwriting an existing output file.

Image handling policy:
1. Preserve original workbook and separately downloaded asset archive unchanged.
2. Treat workbook drawing anchors as *candidate* positions, not proof of business record association.
3. Use SHA-256 for asset identity and independent IDs for asset version and record association.
4. Keep unmatched and ambiguous assets in a review queue. Never silently bind by common filenames such as `image.png`.
5. Security follow-up: add ZIP decompression and XML size limits, malicious workbook fixtures, archive validation, and actual field mapping before handling untrusted XLSX.

Planned next stage: worksheet header mapping, row ID generation, preview diff, image archive indexing and read-only cross-source match reports, before any write operation.
