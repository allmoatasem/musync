#!/usr/bin/env bash
# Build the MuSync desktop app into a self-contained bundle for macOS.
# Output: dist/MuSync.app  (via PyInstaller + pywebview)
#
# Requirements:
#   pip install pyinstaller pywebview
#   pip install .        (musync — must be non-editable for PyInstaller to bundle it)
#   cd app && npm run build   (produces app/dist/)

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$REPO_ROOT/dist"

echo "Building MuSync desktop app with PyInstaller…"
cd "$REPO_ROOT"

pyinstaller \
  --noconfirm \
  --onedir \
  --windowed \
  --name MuSync \
  --distpath "$OUT_DIR" \
  --workpath /tmp/pyinstaller-build \
  --specpath /tmp/pyinstaller-specs \
  --add-data "$REPO_ROOT/app/dist:web" \
  --hidden-import musync \
  --hidden-import musync.cli \
  --hidden-import musync.server \
  --hidden-import musync.model \
  --hidden-import musync.mapping \
  --hidden-import musync.watcher \
  --hidden-import musync.sync.snapshot \
  --hidden-import musync.sync.diff \
  --hidden-import musync.dorico.dtn \
  --hidden-import musync.dorico.parser \
  --hidden-import musync.dorico.extractor \
  --hidden-import musync.dorico.writer \
  --hidden-import musync.staffpad.parser \
  --hidden-import musync.staffpad.extractor \
  --hidden-import musync.staffpad.writer \
  --hidden-import musync.logic.parser \
  --hidden-import musync.logic.extractor \
  --hidden-import musync.logic.writer \
  --collect-all watchdog \
  --collect-all webview \
  "$REPO_ROOT/app/main.py"

echo ""
echo "Done. App bundle at: $OUT_DIR/MuSync.app"
