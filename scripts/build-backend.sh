#!/usr/bin/env bash
# Build the Python backend into a self-contained binary for macOS.
# Output: app/resources/musync-server  (bundled into the Electron app by electron-builder)
#
# Requirements:
#   pip install pyinstaller
#   pip install -e .   (musync package itself)

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$REPO_ROOT/app/resources"

echo "Building Python backend with PyInstaller…"
cd "$REPO_ROOT"

pyinstaller \
  --noconfirm \
  --onedir \
  --name musync-server \
  --distpath "$OUT_DIR" \
  --workpath /tmp/pyinstaller-build \
  --specpath /tmp/pyinstaller-specs \
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
  --hidden-import uvicorn \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols \
  --hidden-import uvicorn.protocols.http \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan \
  --hidden-import uvicorn.lifespan.on \
  --hidden-import fastapi \
  --collect-all watchdog \
  src/musync/server.py

echo ""
echo "Done. Binary at: $OUT_DIR/musync-server/musync-server"
echo "Next: cd app && npm run dist:mac"
