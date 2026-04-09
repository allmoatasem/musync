"""Write musical data into a Logic Pro .logicx project file.

Strategy: locate the EvSq chunk containing MIDI notes, rebuild it with
new note data, splice it back into ProjectData. All other chunks are
preserved byte-for-byte.
"""

from __future__ import annotations

import plistlib
import shutil
import struct
from pathlib import Path

from ..model import Note, Project, Track
from .parser import MAGIC, PPQ, TAG_EVSQ, TICK_OFFSET

# EvSq header is 28 bytes before the data_size field, plus 8 more bytes = 36 total
EVSQ_HEADER_SIZE = 36

# End marker: f1 00 00 00 ff ff ff 3f 00 00 00 00 00 00 00 00
END_MARKER = b"\xf1\x00\x00\x00\xff\xff\xff\x3f\x00\x00\x00\x00\x00\x00\x00\x00"


def _build_note_record(note: Note) -> bytes:
    """Build a 64-byte MIDI note record from a Note.

    Format: 4 x 16-byte sub-records:
      1. Note On: status, note, pad, pad, pos_tick(u16 LE), pad, pad, pad, pad, pad, vel, byte12, pad, pad, 0x01
      2. Note Off: 0x80, zeros(6), 0x89, zeros(4), dur_tick(u16 LE), zeros(2)
      3. Onset time: onset_us(u24 LE), zeros(4), 0xa4, zeros(8)
      4. Duration time: dur_us(u24 LE), zeros(4), 0xa3, zeros(8)
    """
    # Absolute tick position
    abs_tick = note.position + TICK_OFFSET

    # Calculate microseconds from ticks at assumed 120 BPM
    # (This is approximate — Logic recalculates on load)
    us_per_tick = 1000000 * 60 / (120 * PPQ)  # ~520.83 µs/tick
    onset_us = int(note.position * us_per_tick)
    dur_us = int(note.duration * us_per_tick)

    record = bytearray(64)

    # Sub-record 1: Note On (16 bytes)
    record[0] = 0x90
    record[1] = note.pitch & 0x7F
    struct.pack_into("<H", record, 4, abs_tick & 0xFFFF)
    record[11] = note.velocity & 0x7F
    record[15] = 0x01

    # Sub-record 2: Note Off (16 bytes)
    record[16] = 0x80
    record[23] = 0x89
    struct.pack_into("<H", record, 28, note.duration & 0xFFFF)

    # Sub-record 3: Onset microseconds (16 bytes)
    record[32] = onset_us & 0xFF
    record[33] = (onset_us >> 8) & 0xFF
    record[34] = (onset_us >> 16) & 0xFF
    record[39] = 0xA4

    # Sub-record 4: Duration microseconds (16 bytes)
    record[48] = dur_us & 0xFF
    record[49] = (dur_us >> 8) & 0xFF
    record[50] = (dur_us >> 16) & 0xFF
    record[55] = 0xA3

    return bytes(record)


def _find_note_evsq(data: bytes) -> tuple[int, int] | None:
    """Find the EvSq chunk that contains MIDI note data.

    Returns (chunk_start, chunk_end) or None.
    """
    evsq_positions = []
    for i in range(len(data) - 4):
        if data[i : i + 4] == TAG_EVSQ:
            evsq_positions.append(i)

    for pos in evsq_positions:
        # Read data_size at offset +28
        data_size = struct.unpack_from("<I", data, pos + 28)[0]
        chunk_end = pos + EVSQ_HEADER_SIZE + data_size

        # Check if this EvSq has note events
        has_notes = False
        for j in range(pos + EVSQ_HEADER_SIZE, min(chunk_end, len(data)) - 16, 16):
            if data[j] == 0x90 and j + 16 < len(data) and data[j + 16] == 0x80:
                has_notes = True
                break

        if has_notes:
            return pos, chunk_end

    return None


def write_logic(project: Project, path: str, backup: bool = True) -> None:
    """Write a Project model into an existing Logic Pro .logicx project.

    Updates the MIDI notes in the first track's EvSq chunk and the MetaData.plist.
    """
    proj_path = Path(path)

    # Find the .logicx package
    if proj_path.suffix == ".logicx":
        logicx_path = proj_path
    else:
        candidates = list(proj_path.glob("*.logicx"))
        if not candidates:
            candidates = list(proj_path.glob("*/*.logicx"))
        if not candidates:
            raise FileNotFoundError(f"No .logicx found in {path}")
        logicx_path = candidates[0]

    project_data_path = logicx_path / "Alternatives" / "000" / "ProjectData"
    metadata_path = logicx_path / "Alternatives" / "000" / "MetaData.plist"

    if backup:
        backup_dir = logicx_path.parent / (logicx_path.stem + ".backup.logicx")
        if not backup_dir.exists():
            shutil.copytree(logicx_path, backup_dir)

    # Read existing ProjectData
    with open(project_data_path, "rb") as f:
        data = f.read()

    if data[:4] != MAGIC:
        raise ValueError(f"Invalid ProjectData magic: {data[:4].hex()}")

    # Find the note-containing EvSq
    result = _find_note_evsq(data)
    if result is None:
        # No existing note EvSq found — can't write without a template
        raise ValueError("No existing MIDI region found in Logic project to update")

    evsq_start, evsq_end = result
    header = data[evsq_start : evsq_start + EVSQ_HEADER_SIZE]

    # Collect all notes from all tracks
    all_notes = []
    for track in project.tracks:
        all_notes.extend(track.notes)
    all_notes.sort(key=lambda n: (n.position, n.pitch))

    # Build new note records
    note_data = bytearray()
    for note in all_notes:
        note_data.extend(_build_note_record(note))

    # Add end marker
    note_data.extend(END_MARKER)

    # Update data_size in header
    new_header = bytearray(header)
    struct.pack_into("<I", new_header, 28, len(note_data))

    # Splice: [before EvSq] [new header] [new note data] [after old EvSq]
    new_data = data[:evsq_start] + bytes(new_header) + bytes(note_data) + data[evsq_end:]

    # Write back
    with open(project_data_path, "wb") as f:
        f.write(new_data)

    # Update MetaData.plist if tempo/time sig changed
    if metadata_path.exists():
        _update_metadata(metadata_path, project)


def _update_metadata(metadata_path: Path, project: Project) -> None:
    """Update MetaData.plist with tempo/time sig/key sig from project."""
    with open(metadata_path, "rb") as f:
        plist = plistlib.load(f)

    if project.tempo_events:
        plist["BeatsPerMinute"] = project.tempo_events[0].bpm

    if project.time_signatures:
        ts = project.time_signatures[0]
        plist["SongSignatureNumerator"] = ts.numerator
        plist["SongSignatureDenominator"] = ts.denominator

    if project.key_signatures:
        ks = project.key_signatures[0]
        # Convert fifths to Logic's SignatureKey (fifths + 7)
        plist["SignatureKey"] = ks.fifths + 7
        plist["SongKey"] = ks.key_name.replace("m", "")
        plist["SongGenderKey"] = ks.mode

    with open(metadata_path, "wb") as f:
        plistlib.dump(plist, f)
