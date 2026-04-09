# Build the Python backend into a self-contained binary for Windows.
# Output: app\resources\musync-server\musync-server.exe
#
# Requirements:
#   pip install pyinstaller
#   pip install -e .

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$OutDir = Join-Path $RepoRoot "app\resources"

Write-Host "Building Python backend with PyInstaller..." -ForegroundColor Cyan
Set-Location $RepoRoot

pyinstaller `
  --noconfirm `
  --onedir `
  --name musync-server `
  --distpath $OutDir `
  --workpath "$env:TEMP\pyinstaller-build" `
  --specpath "$env:TEMP\pyinstaller-specs" `
  --hidden-import musync `
  --hidden-import musync.cli `
  --hidden-import musync.server `
  --hidden-import musync.model `
  --hidden-import musync.mapping `
  --hidden-import musync.watcher `
  --hidden-import musync.sync.snapshot `
  --hidden-import musync.sync.diff `
  --hidden-import musync.dorico.dtn `
  --hidden-import musync.dorico.parser `
  --hidden-import musync.dorico.extractor `
  --hidden-import musync.dorico.writer `
  --hidden-import musync.staffpad.parser `
  --hidden-import musync.staffpad.extractor `
  --hidden-import musync.staffpad.writer `
  --hidden-import musync.logic.parser `
  --hidden-import musync.logic.extractor `
  --hidden-import musync.logic.writer `
  --hidden-import uvicorn `
  --hidden-import uvicorn.logging `
  --hidden-import uvicorn.loops `
  --hidden-import uvicorn.loops.auto `
  --hidden-import uvicorn.protocols `
  --hidden-import uvicorn.protocols.http `
  --hidden-import uvicorn.protocols.http.auto `
  --hidden-import uvicorn.protocols.websockets `
  --hidden-import uvicorn.protocols.websockets.auto `
  --hidden-import uvicorn.lifespan `
  --hidden-import uvicorn.lifespan.on `
  --hidden-import fastapi `
  --collect-all watchdog `
  src\musync\server.py

Write-Host ""
Write-Host "Done. Binary at: $OutDir\musync-server\musync-server.exe" -ForegroundColor Green
Write-Host "Next: cd app && npm run dist:win"
