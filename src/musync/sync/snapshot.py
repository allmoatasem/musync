"""Snapshot system for MuSync version history.

Each project file gets a `.musync/<filename>/` directory next to it.
Snapshots are numbered JSON files: 001.json, 002.json, …

Snapshot JSON structure:
{
  "version": 1,
  "number": 3,
  "timestamp": "2026-04-09T12:00:00Z",
  "source_file": "/abs/path/to/mysong.dorico",
  "message": "sync from Project.logicx",
  "project": {
    "title": "...",
    "source_format": "dorico",
    "ppq": 960,
    "tempo_events": [{"position": 0, "bpm": 120.0}],
    "time_signatures": [{"position": 0, "numerator": 4, "denominator": 4}],
    "key_signatures": [{"position": 0, "fifths": 0, "mode": "major"}],
    "tracks": [{
      "name": "Violin",
      "instrument": "violin",
      "notes": [{"pitch": 64, "velocity": 80, "position": 0, "duration": 960}]
    }]
  }
}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..model import (
    KeySignatureEvent,
    Note,
    Project,
    TempoEvent,
    TimeSignatureEvent,
    Track,
)

_SNAPSHOT_FORMAT_VERSION = 1


def _musync_dir(file_path: str | Path) -> Path:
    """Return the .musync/<filename>/ directory for a project file."""
    p = Path(file_path).resolve()
    return p.parent / ".musync" / p.name


def _snapshot_path(file_path: str | Path, number: int) -> Path:
    return _musync_dir(file_path) / f"{number:03d}.json"


def list_snapshots(file_path: str | Path) -> list[int]:
    """Return a sorted list of snapshot numbers for a project file."""
    d = _musync_dir(file_path)
    if not d.exists():
        return []
    numbers = []
    for f in d.iterdir():
        if f.suffix == ".json" and f.stem.isdigit():
            numbers.append(int(f.stem))
    return sorted(numbers)


def next_snapshot_number(file_path: str | Path) -> int:
    nums = list_snapshots(file_path)
    return (nums[-1] + 1) if nums else 1


def save_snapshot(
    file_path: str | Path,
    project: Project,
    message: str = "",
) -> int:
    """Save a snapshot of project. Returns the snapshot number."""
    d = _musync_dir(file_path)
    d.mkdir(parents=True, exist_ok=True)

    number = next_snapshot_number(file_path)
    data = {
        "version": _SNAPSHOT_FORMAT_VERSION,
        "number": number,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_file": str(Path(file_path).resolve()),
        "message": message,
        "project": _project_to_dict(project),
    }
    snap_path = _snapshot_path(file_path, number)
    snap_path.write_text(json.dumps(data, indent=2))
    return number


def load_snapshot(file_path: str | Path, number: int) -> tuple[dict, Project]:
    """Load a snapshot. Returns (metadata_dict, Project)."""
    snap_path = _snapshot_path(file_path, number)
    if not snap_path.exists():
        raise FileNotFoundError(f"Snapshot @{number} not found for {file_path}")
    data = json.loads(snap_path.read_text())
    return data, _project_from_dict(data["project"])


def load_latest_snapshot(file_path: str | Path) -> tuple[dict, Project] | None:
    """Load the most recent snapshot, or None if none exist."""
    nums = list_snapshots(file_path)
    if not nums:
        return None
    return load_snapshot(file_path, nums[-1])


# --- Serialization helpers ---

def _project_to_dict(p: Project) -> dict:
    return {
        "title": p.title,
        "source_format": p.source_format,
        "ppq": p.ppq,
        "tempo_events": [{"position": t.position, "bpm": t.bpm} for t in p.tempo_events],
        "time_signatures": [
            {"position": ts.position, "numerator": ts.numerator, "denominator": ts.denominator}
            for ts in p.time_signatures
        ],
        "key_signatures": [
            {"position": ks.position, "fifths": ks.fifths, "mode": ks.mode}
            for ks in p.key_signatures
        ],
        "tracks": [_track_to_dict(t) for t in p.tracks],
    }


def _track_to_dict(t: Track) -> dict:
    return {
        "name": t.name,
        "instrument": t.instrument,
        "notes": [
            {"pitch": n.pitch, "velocity": n.velocity, "position": n.position, "duration": n.duration}
            for n in t.notes
        ],
    }


def _project_from_dict(d: dict) -> Project:
    p = Project(
        title=d.get("title", ""),
        source_format=d.get("source_format", ""),
        ppq=d.get("ppq", 960),
    )
    for t in d.get("tempo_events", []):
        p.tempo_events.append(TempoEvent(position=t["position"], bpm=t["bpm"]))
    for ts in d.get("time_signatures", []):
        p.time_signatures.append(
            TimeSignatureEvent(position=ts["position"], numerator=ts["numerator"], denominator=ts["denominator"])
        )
    for ks in d.get("key_signatures", []):
        p.key_signatures.append(
            KeySignatureEvent(position=ks["position"], fifths=ks["fifths"], mode=ks["mode"])
        )
    for tr in d.get("tracks", []):
        track = Track(name=tr["name"], instrument=tr.get("instrument", ""))
        for n in tr.get("notes", []):
            track.notes.append(
                Note(pitch=n["pitch"], velocity=n["velocity"], position=n["position"], duration=n["duration"])
            )
        p.tracks.append(track)
    return p
