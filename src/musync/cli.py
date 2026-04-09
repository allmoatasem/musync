"""MuSync CLI — read, compare, sync, and version-control Logic Pro / Dorico / StaffPad projects."""

from __future__ import annotations

import sys
from pathlib import Path

from .model import Project, Note

# Note names for display
_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _detect_format(path: str) -> str:
    """Detect project format from file extension or structure."""
    p = Path(path)
    if p.suffix == ".dorico":
        return "dorico"
    if p.suffix == ".stf":
        return "staffpad"
    if p.suffix == ".logicx":
        return "logic"
    if p.is_dir():
        if list(p.glob("*.logicx")) or list(p.glob("*/*.logicx")):
            return "logic"
    raise ValueError(f"Unknown format for: {path}")


def _load_project(path: str) -> Project:
    """Load a project file and extract to the common model."""
    fmt = _detect_format(path)

    if fmt == "dorico":
        from .dorico.parser import parse_dorico
        from .dorico.extractor import extract_project
        return extract_project(parse_dorico(path))

    elif fmt == "staffpad":
        from .staffpad.parser import parse_staffpad
        from .staffpad.extractor import extract_project
        return extract_project(parse_staffpad(path))

    elif fmt == "logic":
        from .logic.parser import parse_logic
        from .logic.extractor import extract_project
        return extract_project(parse_logic(path))

    raise ValueError(f"Unsupported format: {fmt}")


def _note_name(midi: int) -> str:
    return f"{_NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def _print_project(project: Project) -> None:
    print(f"  Format: {project.source_format}")
    if project.title:
        print(f"  Title: {project.title}")
    print(f"  PPQ: {project.ppq}")

    if project.tempo_events:
        tempos = ", ".join(f"{t.bpm} BPM at tick {t.position}" for t in project.tempo_events)
        print(f"  Tempo: {tempos}")

    if project.time_signatures:
        ts_str = ", ".join(
            f"{ts.numerator}/{ts.denominator} at tick {ts.position}"
            for ts in project.time_signatures
        )
        print(f"  Time sig: {ts_str}")

    if project.key_signatures:
        ks_str = ", ".join(f"{ks.key_name} at tick {ks.position}" for ks in project.key_signatures)
        print(f"  Key sig: {ks_str}")

    print(f"  Tracks: {len(project.tracks)}")
    for track in project.tracks:
        print(f"\n    [{track.name}] ({track.instrument})")
        print(f"    Notes: {len(track.notes)}")
        for note in track.notes[:20]:
            name = _note_name(note.pitch)
            print(f"      {name:5s} vel={note.velocity:3d} pos={note.position:6d} dur={note.duration:5d}")
        if len(track.notes) > 20:
            print(f"      ... and {len(track.notes) - 20} more notes")
        if track.dynamics:
            print(f"    Dynamics: {len(track.dynamics)}")
        if track.articulations:
            print(f"    Articulations: {len(track.articulations)}")


def _write_project(project: Project, path: str) -> None:
    fmt = _detect_format(path)
    if fmt == "staffpad":
        from .staffpad.writer import write_staffpad
        write_staffpad(project, path)
        print(f"  Written to StaffPad: {path}")
    elif fmt == "logic":
        from .logic.writer import write_logic
        write_logic(project, path)
        print(f"  Written to Logic Pro: {path}")
    elif fmt == "dorico":
        from .dorico.writer import write_dorico
        write_dorico(project, path)
        print(f"  Written to Dorico: {path}")
    else:
        print(f"  Writing not supported for format: {fmt}")


# ── version helpers ──────────────────────────────────────────────────────────

def _parse_snapshot_ref(arg: str) -> int | None:
    """Parse '@N' into N. Returns None if not a snapshot ref."""
    if arg.startswith("@"):
        try:
            return int(arg[1:])
        except ValueError:
            pass
    return None


def _load_snapshot_project(file_path: str, number: int) -> Project:
    from .sync.snapshot import load_snapshot
    _, project = load_snapshot(file_path, number)
    return project


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_read(args: list[str]) -> None:
    if not args:
        print("Usage: musync read <file>")
        sys.exit(1)
    path = args[0]
    print(f"Reading: {path}")
    _print_project(_load_project(path))


def cmd_diff(args: list[str]) -> None:
    """diff <file1> <file2>  |  diff <file> @N  |  diff <file> @N @M"""
    if len(args) < 2:
        print("Usage: musync diff <file1> <file2>")
        print("       musync diff <file> @N           (current vs snapshot N)")
        print("       musync diff <file> @N @M        (snapshot N vs snapshot M)")
        sys.exit(1)

    from .sync.diff import diff_projects

    # Supported patterns:
    #   diff file1 file2       — two different files
    #   diff file @N           — current state vs snapshot N
    #   diff file @N @M        — snapshot N vs snapshot M
    if len(args) >= 3:
        file_path = args[0]
        ref1 = _parse_snapshot_ref(args[1])
        ref2 = _parse_snapshot_ref(args[2])
        if ref1 is not None and ref2 is not None:
            a = _load_snapshot_project(file_path, ref1)
            b = _load_snapshot_project(file_path, ref2)
            label_a, label_b = f"@{ref1}", f"@{ref2}"
        else:
            print("Error: with three arguments, args 2 and 3 must be snapshot refs like @1 @2")
            sys.exit(1)
    else:
        first, second = args[0], args[1]
        ref2 = _parse_snapshot_ref(second)
        if ref2 is not None:
            # diff file @N
            file_path = first
            a = _load_project(file_path)
            b = _load_snapshot_project(file_path, ref2)
            label_a, label_b = "current", f"@{ref2}"
        else:
            # diff file1 file2
            a = _load_project(first)
            b = _load_project(second)
            label_a, label_b = first, second

    d = diff_projects(a, b)
    print(f"\nDiff ({label_a} → {label_b}): {d.summary()}\n")
    d.print(label_a, label_b)


def cmd_log(args: list[str]) -> None:
    """log <file> — list all snapshots for a project."""
    if not args:
        print("Usage: musync log <file>")
        sys.exit(1)
    from .sync.snapshot import list_snapshots, load_snapshot
    from .sync.diff import diff_projects

    file_path = args[0]
    numbers = list_snapshots(file_path)
    if not numbers:
        print(f"No snapshots for {file_path}. Run 'musync sync' to create one.")
        return

    print(f"Snapshots for {file_path}:\n")
    prev_project = None
    for n in numbers:
        meta, project = load_snapshot(file_path, n)
        ts = meta.get("timestamp", "")[:19].replace("T", " ")
        msg = meta.get("message", "")
        note_count = sum(len(t.notes) for t in project.tracks)
        if prev_project is not None:
            d = diff_projects(prev_project, project)
            change = f"  ({d.summary()})"
        else:
            change = f"  ({note_count} notes)"
        marker = " ← latest" if n == numbers[-1] else ""
        print(f"  @{n:<3d}  {ts}  {msg}{change}{marker}")
        prev_project = project


def cmd_revert(args: list[str]) -> None:
    """revert <file> @N — restore snapshot N into the project file."""
    if len(args) < 2:
        print("Usage: musync revert <file> @N")
        sys.exit(1)

    file_path = args[0]
    ref = _parse_snapshot_ref(args[1])
    if ref is None:
        print(f"Error: expected a snapshot reference like @3, got '{args[1]}'")
        sys.exit(1)

    from .sync.snapshot import load_snapshot, save_snapshot

    # Save the current state as a new snapshot before overwriting
    print(f"Saving current state as a new snapshot…")
    try:
        current = _load_project(file_path)
        n = save_snapshot(file_path, current, message="pre-revert backup")
        print(f"  Saved as @{n}")
    except Exception as e:
        print(f"  Warning: could not snapshot current state: {e}")

    _, snapshot_project = load_snapshot(file_path, ref)

    print(f"Reverting {file_path} to @{ref}…")
    _write_project(snapshot_project, file_path)
    print(f"Done. {file_path} restored to snapshot @{ref}.")


def cmd_sync(args: list[str]) -> None:
    if len(args) < 2:
        print("Usage: musync sync <source> <destination>")
        sys.exit(1)

    source_path = args[0]
    dest_path = args[1]

    print(f"Source: {source_path}")
    source = _load_project(source_path)
    note_count = sum(len(t.notes) for t in source.tracks)
    print(f"  {source.source_format}: {note_count} notes across {len(source.tracks)} tracks")

    print(f"\nDestination: {dest_path}")
    print("  Syncing notes…")
    _write_project(source, dest_path)

    # Auto-snapshot the destination after each successful sync
    from .sync.snapshot import save_snapshot
    result = _load_project(dest_path)
    snap_n = save_snapshot(
        dest_path,
        result,
        message=f"sync from {Path(source_path).name}",
    )
    print(f"  Snapshot @{snap_n} saved.")

    print("\nVerifying…")
    print(f"  {result.source_format}: {sum(len(t.notes) for t in result.tracks)} notes across {len(result.tracks)} tracks")
    print("\nSync complete.")


def cmd_serve(args: list[str]) -> None:
    """serve [--port N] — start the local HTTP API server for the desktop app."""
    port = 7765
    if "--port" in args:
        try:
            port = int(args[args.index("--port") + 1])
        except (IndexError, ValueError):
            pass
    from .server import serve
    print(f"MuSync server listening on http://127.0.0.1:{port}")
    serve(port)


def cmd_watch(args: list[str]) -> None:
    """watch <source> <destination> — auto-sync on every save."""
    if len(args) < 2:
        print("Usage: musync watch <source> <destination>")
        sys.exit(1)
    from .watcher import start_watcher
    start_watcher(args[0], args[1])


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print("MuSync — Sync between Logic Pro, Dorico, and StaffPad")
        print()
        print("Commands:")
        print("  musync read <file>              Read and display a project")
        print("  musync diff <file1> <file2>     Compare two projects")
        print("  musync diff <file> @N           Compare current state vs snapshot N")
        print("  musync diff <file> @N @M        Compare two snapshots")
        print("  musync sync <source> <dest>     Sync notes from source to destination")
        print("  musync log <file>               List all snapshots for a project")
        print("  musync revert <file> @N         Restore a project to snapshot N")
        print("  musync watch <source> <dest>    Auto-sync on every save")
        print()
        print("Supported formats: .dorico  .stf  .logicx")
        sys.exit(0)

    command = args[0]
    rest = args[1:]

    dispatch = {
        "read": cmd_read,
        "diff": cmd_diff,
        "sync": cmd_sync,
        "log": cmd_log,
        "revert": cmd_revert,
        "watch": cmd_watch,
        "serve": cmd_serve,
    }

    if command in dispatch:
        dispatch[command](rest)
    else:
        print(f"Unknown command: {command}")
        print("Run 'musync --help' for usage")
        sys.exit(1)


if __name__ == "__main__":
    main()
