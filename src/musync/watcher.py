"""File watcher daemon for automatic sync on save.

Usage:
    musync watch source.dorico dest.logicx

Monitors the source file. When it changes, syncs to the destination.
Debounces rapid saves (1-second quiet period) and skips writes triggered
by its own sync to prevent loops.
"""

from __future__ import annotations

import hashlib
import sys
import time
from pathlib import Path
from threading import Timer

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .sync.snapshot import save_snapshot


def _file_hash(path: Path) -> str:
    """SHA-256 of a file's contents."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _do_sync(source_path: str, dest_path: str) -> None:
    """Load source and write to dest (mirrors cmd_sync logic, silent on errors)."""
    from .cli import _load_project, _write_project

    try:
        source = _load_project(source_path)
        _write_project(source, dest_path)
        result = _load_project(dest_path)
        snap_n = save_snapshot(
            dest_path,
            result,
            message=f"watch: auto-sync from {Path(source_path).name}",
        )
        note_count = sum(len(t.notes) for t in result.tracks)
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] Synced → {dest_path}  ({note_count} notes, snapshot @{snap_n})")
    except Exception as e:
        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] Sync error: {e}", file=sys.stderr)


class _SyncHandler(FileSystemEventHandler):
    """Debounced handler that syncs source → dest whenever source changes."""

    DEBOUNCE_SECONDS = 1.0

    def __init__(self, source_path: str, dest_path: str) -> None:
        self.source = Path(source_path).resolve()
        self.dest = Path(dest_path).resolve()
        self._timer: Timer | None = None
        # Track the hash of what we last wrote, so we can skip self-induced events
        self._last_written_hash: str = ""

    def on_modified(self, event: FileSystemEvent) -> None:
        self._on_change(event)

    def on_created(self, event: FileSystemEvent) -> None:
        self._on_change(event)

    def _on_change(self, event: FileSystemEvent) -> None:
        changed = Path(str(event.src_path)).resolve()

        # Only react to the source file (or any file inside it for directory bundles)
        if changed != self.source and not str(changed).startswith(str(self.source)):
            return

        # Debounce: reset the timer on each rapid event
        if self._timer is not None:
            self._timer.cancel()
        self._timer = Timer(self.DEBOUNCE_SECONDS, self._fire)
        self._timer.daemon = True
        self._timer.start()

    def _fire(self) -> None:
        # Check if source file changed at all (avoid spurious events)
        current_hash = _file_hash(self.source)
        if current_hash == self._last_written_hash:
            return

        _do_sync(str(self.source), str(self.dest))

        # Record the hash of what we just wrote to dest
        self._last_written_hash = _file_hash(self.dest)


def start_watcher(source_path: str, dest_path: str) -> None:
    """Start watching source_path and syncing to dest_path. Blocks until Ctrl-C."""
    source = Path(source_path).resolve()
    dest = Path(dest_path).resolve()

    if not source.exists():
        print(f"Error: source not found: {source_path}", file=sys.stderr)
        sys.exit(1)
    if not dest.exists():
        print(f"Error: destination not found: {dest_path}", file=sys.stderr)
        sys.exit(1)

    handler = _SyncHandler(source_path, dest_path)

    # Watch the parent directory so we catch atomic saves (rename-into-place)
    watch_dir = str(source.parent)
    observer = Observer()
    observer.schedule(handler, watch_dir, recursive=True)
    observer.start()

    print(f"Watching: {source_path}")
    print(f"  → dest: {dest_path}")
    print(f"  Debounce: {_SyncHandler.DEBOUNCE_SECONDS}s  |  Press Ctrl-C to stop\n")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        observer.stop()
        observer.join()
