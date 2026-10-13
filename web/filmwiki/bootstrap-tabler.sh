#!/usr/bin/env bash
# Prepare a locally hosted, pinned Tabler CSS/JS build. No runtime network.
set -euo pipefail
VERSION="1.6.1"
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HERE/vendor/tabler"
if [[ -f "$DEST/VERSION.txt" ]] && grep -qx "$VERSION" "$DEST/VERSION.txt"; then
  echo "TABLER_VENDOR_PRESENT=$VERSION"
  exit 0
fi
command -v npm >/dev/null || { echo "npm required for one-time setup" >&2; exit 2; }
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
(cd "$TMP" && npm pack "@tabler/core@$VERSION" --silent --ignore-scripts >/dev/null)
PACKAGE="$TMP/tabler-core-$VERSION.tgz"
test -s "$PACKAGE"
tar -xzf "$PACKAGE" -C "$TMP"
test -s "$TMP/package/dist/css/tabler.min.css"
test -s "$TMP/package/dist/js/tabler.min.js"
LICENSE_FILE=""
for candidate in "$TMP/package/LICENSE" "$TMP/package/LICENSE.md" "$TMP/package/LICENSE.txt"; do
  if [[ -f "$candidate" ]]; then LICENSE_FILE="$candidate"; break; fi
done
if [[ -z "$LICENSE_FILE" ]]; then
  echo "Missing license in @tabler/core package" >&2
  exit 1
fi
mkdir -p "$DEST/css" "$DEST/js"
cp "$TMP/package/dist/css/tabler.min.css" "$DEST/css/tabler.min.css"
cp "$TMP/package/dist/js/tabler.min.js" "$DEST/js/tabler.min.js"
cp "$LICENSE_FILE" "$DEST/LICENSE"
printf '%s\n' "$VERSION" > "$DEST/VERSION.txt"
(cd "$DEST" && sha256sum css/tabler.min.css js/tabler.min.js LICENSE > SHA256SUMS && sha256sum -c SHA256SUMS)
echo "TABLER_VENDOR_READY=$DEST ($VERSION)"
