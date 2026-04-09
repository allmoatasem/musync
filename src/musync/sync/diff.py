"""Diff engine for comparing two Project snapshots.

A note's identity is (track_name, pitch, position). If two notes share the
same identity but differ in duration or velocity, that's a "changed" note.
Notes present in A but not in B are "removed"; present in B but not A are "added".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model import Note, Project, Track

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _note_name(midi: int) -> str:
    return f"{_NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def _note_id(note: Note) -> tuple[int, int]:
    """Identity key: (pitch, position). Used to match notes across snapshots."""
    return (note.pitch, note.position)


@dataclass
class NoteChange:
    kind: str          # "added" | "removed" | "changed"
    track: str
    note: Note         # the new note (or the removed note)
    old_note: Note | None = None   # only for "changed"

    def describe(self) -> str:
        n = self.note
        name = _note_name(n.pitch)
        if self.kind == "added":
            return f"  + {name:5s}  pos={n.position:6d}  dur={n.duration:5d}  vel={n.velocity}  [{self.track}]"
        if self.kind == "removed":
            return f"  - {name:5s}  pos={n.position:6d}  dur={n.duration:5d}  vel={n.velocity}  [{self.track}]"
        # changed
        old = self.old_note
        assert old is not None
        parts = []
        if old.duration != n.duration:
            parts.append(f"dur {old.duration}→{n.duration}")
        if old.velocity != n.velocity:
            parts.append(f"vel {old.velocity}→{n.velocity}")
        return f"  ~ {name:5s}  pos={n.position:6d}  {', '.join(parts)}  [{self.track}]"


@dataclass
class ProjectDiff:
    tempo_changed: bool = False
    time_sig_changed: bool = False
    key_sig_changed: bool = False
    note_changes: list[NoteChange] = field(default_factory=list)

    @property
    def added(self) -> list[NoteChange]:
        return [c for c in self.note_changes if c.kind == "added"]

    @property
    def removed(self) -> list[NoteChange]:
        return [c for c in self.note_changes if c.kind == "removed"]

    @property
    def changed(self) -> list[NoteChange]:
        return [c for c in self.note_changes if c.kind == "changed"]

    @property
    def is_identical(self) -> bool:
        return not self.note_changes and not self.tempo_changed and not self.time_sig_changed and not self.key_sig_changed

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"+{len(self.added)} notes")
        if self.removed:
            parts.append(f"-{len(self.removed)} notes")
        if self.changed:
            parts.append(f"~{len(self.changed)} notes")
        if self.tempo_changed:
            parts.append("tempo")
        if self.time_sig_changed:
            parts.append("time sig")
        if self.key_sig_changed:
            parts.append("key sig")
        return ", ".join(parts) if parts else "identical"

    def print(self, label_a: str = "A", label_b: str = "B") -> None:
        if self.is_identical:
            print("  No differences.")
            return

        if self.tempo_changed:
            print("  Tempo changed")
        if self.time_sig_changed:
            print("  Time signature changed")
        if self.key_sig_changed:
            print("  Key signature changed")

        if self.added:
            print(f"\n  Added ({len(self.added)} notes):")
            for c in self.added:
                print(c.describe())

        if self.removed:
            print(f"\n  Removed ({len(self.removed)} notes):")
            for c in self.removed:
                print(c.describe())

        if self.changed:
            print(f"\n  Changed ({len(self.changed)} notes):")
            for c in self.changed:
                print(c.describe())


def diff_projects(a: Project, b: Project) -> ProjectDiff:
    """Compute the diff from project A to project B."""
    result = ProjectDiff()

    # Tempo
    a_tempos = {t.position: t.bpm for t in a.tempo_events}
    b_tempos = {t.position: t.bpm for t in b.tempo_events}
    result.tempo_changed = a_tempos != b_tempos

    # Time signatures
    a_ts = {ts.position: (ts.numerator, ts.denominator) for ts in a.time_signatures}
    b_ts = {ts.position: (ts.numerator, ts.denominator) for ts in b.time_signatures}
    result.time_sig_changed = a_ts != b_ts

    # Key signatures
    a_ks = {ks.position: (ks.fifths, ks.mode) for ks in a.key_signatures}
    b_ks = {ks.position: (ks.fifths, ks.mode) for ks in b.key_signatures}
    result.key_sig_changed = a_ks != b_ks

    # Notes — match tracks by name, fall back to index
    a_tracks = {t.name: t for t in a.tracks}
    b_tracks = {t.name: t for t in b.tracks}
    all_names = sorted(set(a_tracks) | set(b_tracks))

    for name in all_names:
        at = a_tracks.get(name)
        bt = b_tracks.get(name)
        if at and not bt:
            for n in at.notes:
                result.note_changes.append(NoteChange("removed", name, n))
        elif bt and not at:
            for n in bt.notes:
                result.note_changes.append(NoteChange("added", name, n))
        else:
            assert at is not None and bt is not None
            _diff_track(at, bt, name, result)

    # If no name overlap, compare by index
    if all_names and not (set(a_tracks) & set(b_tracks)):
        result.note_changes.clear()
        for at, bt in zip(a.tracks, b.tracks):
            _diff_track(at, bt, at.name or bt.name, result)

    result.note_changes.sort(key=lambda c: (c.track, c.note.position, c.note.pitch))
    return result


def _diff_track(a: Track, b: Track, name: str, result: ProjectDiff) -> None:
    a_notes = {_note_id(n): n for n in a.notes}
    b_notes = {_note_id(n): n for n in b.notes}

    for nid, n in a_notes.items():
        if nid not in b_notes:
            result.note_changes.append(NoteChange("removed", name, n))
        else:
            bn = b_notes[nid]
            if n.duration != bn.duration or n.velocity != bn.velocity:
                result.note_changes.append(NoteChange("changed", name, bn, old_note=n))

    for nid, n in b_notes.items():
        if nid not in a_notes:
            result.note_changes.append(NoteChange("added", name, n))
