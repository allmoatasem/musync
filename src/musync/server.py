"""FastAPI server — exposes all MuSync operations as a local HTTP API.

The Electron app spawns this process on startup and communicates with it
over localhost. It is never exposed to the network.

Start manually:
    musync serve [--port 7765]
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .cli import _detect_format, _load_project, _write_project

app = FastAPI(title="MuSync", version="0.1.0", docs_url=None, redoc_url=None)

# Allow the Electron renderer (file:// or localhost:5173 in dev) to call us
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── shared watcher state ────────────────────────────────────────────────────

_watch_observer = None
_watch_source: str | None = None
_watch_dest: str | None = None


# ── response helpers ────────────────────────────────────────────────────────

def _note_name(midi: int) -> str:
    names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    return f"{names[midi % 12]}{midi // 12 - 1}"


def _project_to_dict(project) -> dict:
    return {
        "title": project.title,
        "source_format": project.source_format,
        "ppq": project.ppq,
        "tempo_events": [{"position": t.position, "bpm": t.bpm} for t in project.tempo_events],
        "time_signatures": [
            {"position": ts.position, "numerator": ts.numerator, "denominator": ts.denominator}
            for ts in project.time_signatures
        ],
        "key_signatures": [
            {"position": ks.position, "fifths": ks.fifths, "mode": ks.mode, "key_name": ks.key_name}
            for ks in project.key_signatures
        ],
        "tracks": [
            {
                "name": t.name,
                "instrument": t.instrument,
                "note_count": len(t.notes),
                "notes": [
                    {
                        "pitch": n.pitch,
                        "name": _note_name(n.pitch),
                        "velocity": n.velocity,
                        "position": n.position,
                        "duration": n.duration,
                    }
                    for n in t.notes[:200]  # cap display notes
                ],
            }
            for t in project.tracks
        ],
    }


# ── endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/read")
def read_project(path: str = Query(...)) -> dict:
    try:
        project = _load_project(path)
        return _project_to_dict(project)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class SyncRequest(BaseModel):
    source: str
    dest: str


@app.post("/sync")
def sync_projects(body: SyncRequest) -> dict:
    from .sync.snapshot import save_snapshot

    try:
        source = _load_project(body.source)
        _write_project(source, body.dest)
        result = _load_project(body.dest)
        snap_n = save_snapshot(
            body.dest,
            result,
            message=f"sync from {Path(body.source).name}",
        )
        note_count = sum(len(t.notes) for t in result.tracks)
        return {"ok": True, "note_count": note_count, "snapshot": snap_n}
    except NotImplementedError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/log")
def get_log(path: str = Query(...)) -> dict:
    from .sync.snapshot import list_snapshots, load_snapshot
    from .sync.diff import diff_projects

    try:
        numbers = list_snapshots(path)
        snapshots = []
        prev = None
        for n in numbers:
            meta, project = load_snapshot(path, n)
            note_count = sum(len(t.notes) for t in project.tracks)
            if prev is not None:
                from .sync.diff import diff_projects as dp
                d = dp(prev, project)
                summary = d.summary()
            else:
                summary = f"{note_count} notes"
            snapshots.append({
                "number": n,
                "timestamp": meta.get("timestamp", ""),
                "message": meta.get("message", ""),
                "note_count": note_count,
                "summary": summary,
            })
            prev = project
        return {"snapshots": snapshots}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class DiffRequest(BaseModel):
    path_a: str
    path_b: str | None = None
    snapshot_a: int | None = None
    snapshot_b: int | None = None


@app.post("/diff")
def diff(body: DiffRequest) -> dict:
    from .sync.snapshot import load_snapshot
    from .sync.diff import diff_projects

    try:
        # Resolve A
        if body.snapshot_a is not None:
            _, a = load_snapshot(body.path_a, body.snapshot_a)
        else:
            a = _load_project(body.path_a)

        # Resolve B
        if body.snapshot_b is not None:
            ref_path = body.path_b or body.path_a
            _, b = load_snapshot(ref_path, body.snapshot_b)
        elif body.path_b:
            b = _load_project(body.path_b)
        else:
            raise HTTPException(status_code=400, detail="Must supply path_b or snapshot_b")

        d = diff_projects(a, b)
        return {
            "summary": d.summary(),
            "tempo_changed": d.tempo_changed,
            "time_sig_changed": d.time_sig_changed,
            "key_sig_changed": d.key_sig_changed,
            "added": [
                {"track": c.track, "pitch": c.note.pitch, "name": _note_name(c.note.pitch),
                 "position": c.note.position, "duration": c.note.duration, "velocity": c.note.velocity}
                for c in d.added
            ],
            "removed": [
                {"track": c.track, "pitch": c.note.pitch, "name": _note_name(c.note.pitch),
                 "position": c.note.position, "duration": c.note.duration, "velocity": c.note.velocity}
                for c in d.removed
            ],
            "changed": [
                {"track": c.track, "pitch": c.note.pitch, "name": _note_name(c.note.pitch),
                 "position": c.note.position,
                 "old_duration": c.old_note.duration if c.old_note else None,
                 "new_duration": c.note.duration,
                 "old_velocity": c.old_note.velocity if c.old_note else None,
                 "new_velocity": c.note.velocity}
                for c in d.changed
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class RevertRequest(BaseModel):
    path: str
    snapshot: int


@app.post("/revert")
def revert(body: RevertRequest) -> dict:
    from .sync.snapshot import load_snapshot, save_snapshot

    try:
        # Back up current state
        current = _load_project(body.path)
        backup_n = save_snapshot(body.path, current, message="pre-revert backup")

        _, snap_project = load_snapshot(body.path, body.snapshot)
        _write_project(snap_project, body.path)
        return {"ok": True, "backup_snapshot": backup_n, "restored_snapshot": body.snapshot}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class WatchRequest(BaseModel):
    source: str
    dest: str


@app.post("/watch/start")
def watch_start(body: WatchRequest) -> dict:
    global _watch_observer, _watch_source, _watch_dest
    from .watcher import _SyncHandler
    from watchdog.observers import Observer

    if _watch_observer is not None:
        _watch_observer.stop()
        _watch_observer.join()

    handler = _SyncHandler(body.source, body.dest)
    source = Path(body.source).resolve()
    observer = Observer()
    observer.schedule(handler, str(source.parent), recursive=True)
    observer.start()

    _watch_observer = observer
    _watch_source = body.source
    _watch_dest = body.dest
    return {"ok": True, "source": body.source, "dest": body.dest}


@app.delete("/watch")
def watch_stop() -> dict:
    global _watch_observer, _watch_source, _watch_dest
    if _watch_observer is not None:
        _watch_observer.stop()
        _watch_observer.join()
        _watch_observer = None
        _watch_source = None
        _watch_dest = None
    return {"ok": True}


@app.get("/watch/status")
def watch_status() -> dict:
    return {
        "watching": _watch_observer is not None and _watch_observer.is_alive(),
        "source": _watch_source,
        "dest": _watch_dest,
    }


# ── entry point ──────────────────────────────────────────────────────────────

def serve(port: int = 7765) -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 7765
    serve(port)
