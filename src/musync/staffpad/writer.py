"""Write musical data into a StaffPad .stf project file.

Strategy: given an existing .stf file and a Project model, update notes in
matching tracks. Preserves all non-note data (mixer, plugins, video, etc.).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from pathlib import Path

from ..model import Note, Project, Track
from .extractor import _staff_position_to_midi
from .parser import parse_staffpad

# Duration: ticks → StaffPad duration code
# Reverse of extractor's _DURATION_LEVEL_TO_BEATS
_TICKS_TO_DURATION = {
    3840: 0x10,  # whole
    5760: 0x11,  # dotted whole
    1920: 0x30,  # half
    2880: 0x31,  # dotted half
    960: 0x40,   # quarter
    1440: 0x41,  # dotted quarter
    480: 0x50,   # eighth
    720: 0x51,   # dotted eighth
    240: 0x60,   # 16th
    360: 0x61,   # dotted 16th
    120: 0x70,   # 32nd
    180: 0x71,   # dotted 32nd
}

PPQ = 960


def _ticks_to_duration_code(ticks: int) -> int:
    """Convert tick duration to StaffPad duration code."""
    code = _TICKS_TO_DURATION.get(ticks)
    if code is not None:
        return code
    # Find closest match
    best_ticks = min(_TICKS_TO_DURATION.keys(), key=lambda t: abs(t - ticks))
    return _TICKS_TO_DURATION[best_ticks]


def _midi_to_staff_position(
    midi_pitch: int,
    clef: str = "treble",
    key_accidentals: int = 0,
) -> int:
    """Convert MIDI note number to StaffPad staff position.

    Inverse of extractor's _staff_position_to_midi.
    Treble clef: position 0 = B4 (MIDI 71).
    """
    # Build scale from key signature
    semitones = [0, 2, 4, 5, 7, 9, 11]
    sharp_order = [3, 0, 4, 1, 5, 2, 6]
    flat_order = [6, 2, 5, 1, 4, 0, 3]

    alterations = [0] * 7
    if key_accidentals > 0:
        for i in range(min(key_accidentals, 7)):
            alterations[sharp_order[i]] = 1
    elif key_accidentals < 0:
        for i in range(min(-key_accidentals, 7)):
            alterations[flat_order[i]] = -1

    # Reference point
    if clef == "bass":
        ref_step, ref_octave = 1, 3  # D3
    else:
        ref_step, ref_octave = 6, 4  # B4

    ref_midi = (ref_octave + 1) * 12 + semitones[ref_step] + alterations[ref_step]

    # Find the diatonic step closest to the target pitch
    target_octave = (midi_pitch // 12) - 1
    target_pc = midi_pitch % 12

    # Try each diatonic step in the target octave
    best_pos = None
    best_diff = 999

    for oct_offset in range(-1, 2):
        octave = target_octave + oct_offset
        for step in range(7):
            step_midi = (octave + 1) * 12 + semitones[step] + alterations[step]
            if step_midi == midi_pitch:
                # Calculate staff position relative to reference
                total_ref = ref_octave * 7 + ref_step
                total_note = octave * 7 + step
                pos = total_note - total_ref
                diff = abs(step_midi - midi_pitch)
                if diff < best_diff:
                    best_diff = diff
                    best_pos = pos

    if best_pos is not None:
        return best_pos

    # Fallback: chromatic approximation
    # Each octave = 7 diatonic steps, each semitone ≈ 7/12 steps
    return round((midi_pitch - 71) * 7 / 12)


def _ticks_to_beat_position(tick_offset: int, ppq: int = PPQ) -> tuple[int, int]:
    """Convert a tick offset within a bar to a beat position (numerator, denominator).

    Returns (numerator, denominator) where the position is numerator/denominator
    in whole-note units. denominator is always 4 (quarter-note based).
    """
    if tick_offset == 0:
        return (0, 0)
    # Convert to quarter notes
    quarter_beats = tick_offset / ppq
    # Express as fraction with denominator 4
    numerator = round(quarter_beats)
    return (numerator, 4)


def _random_blob(size: int) -> bytes:
    """Generate a random blob for object values."""
    return os.urandom(size)


def write_staffpad(project: Project, stf_path: str, backup: bool = True) -> None:
    """Write a Project model into an existing StaffPad .stf file.

    Updates notes in tracks that match by name. Creates a backup first.
    """
    stf_path = str(Path(stf_path).resolve())

    if backup:
        backup_path = stf_path + ".backup"
        shutil.copy2(stf_path, backup_path)

    # Parse existing project to get track mapping
    existing = parse_staffpad(stf_path)

    conn = sqlite3.connect(stf_path)
    conn.row_factory = sqlite3.Row
    try:
        # Get the user_actor for Part objects
        ua = _get_part_user_actor(conn)

        # Get current min_object_ref for ID allocation
        next_obj = _get_next_obj(conn)

        # Match tracks by name
        for track in project.tracks:
            if not track.notes:
                continue

            matching_part = None
            for part in existing.parts:
                if part.instrument.name == track.name:
                    matching_part = part
                    break

            if matching_part is None:
                continue

            # Get key accidentals for pitch conversion
            key_acc = existing.key_signatures[0].accidentals if existing.key_signatures else 0

            # Clear existing notes and chords for this part
            next_obj = _clear_part_notes(conn, matching_part.part_obj, ua)

            # Group notes by bar
            bars = _group_notes_by_bar(
                track.notes,
                existing.time_signatures,
                PPQ,
            )

            # Insert new notes
            next_obj = _insert_bars(
                conn, matching_part.part_obj, ua, bars, key_acc, next_obj
            )

        # Update min_object_ref
        conn.execute(
            "UPDATE metadata SET value = ? WHERE key = 'min_object_ref'",
            (str(next_obj),),
        )
        conn.commit()
    finally:
        conn.close()


def _get_part_user_actor(conn: sqlite3.Connection) -> int:
    """Get the user_actor used for Part objects."""
    row = conn.execute("""
        SELECT s.user_actor FROM score0 s
        JOIN typenames t ON s.typename = t.key
        WHERE t.type = 'Part' LIMIT 1
    """).fetchone()
    return row["user_actor"] if row else 1


def _get_next_obj(conn: sqlite3.Connection) -> int:
    """Get the next available object ID (max existing + 1024 for safety)."""
    row = conn.execute("SELECT MAX(obj) as max_obj FROM score0").fetchone()
    max_existing = row["max_obj"] if row and row["max_obj"] else 0
    meta_row = conn.execute(
        "SELECT value FROM metadata WHERE key = 'min_object_ref'"
    ).fetchone()
    meta_val = int(meta_row["value"]) if meta_row else 0
    return max(max_existing + 1024, meta_val)


def _clear_part_notes(conn: sqlite3.Connection, part_obj: int, ua: int) -> int:
    """Delete all Chord and Note objects from a part's bars. Returns next obj ID."""
    # Find the StandardStaff → bars array → each Bar → voices → Voice → duration_elements
    staff_row = conn.execute("""
        SELECT staff.obj
        FROM score0 ns
        JOIN typenames nst ON ns.typename = nst.key AND nst.name = 'notation_staves'
        JOIN score0 staff ON staff.parent_obj = ns.obj AND staff.user_actor = ns.user_actor
        JOIN typenames st ON staff.typename = st.key AND st.type = 'StandardStaff'
        WHERE ns.parent_obj = ? AND ns.user_actor = ?
        LIMIT 1
    """, (part_obj, ua)).fetchone()

    if not staff_row:
        return _get_next_obj(conn)

    bars_arr_row = conn.execute("""
        SELECT s.obj FROM score0 s
        JOIN typenames t ON s.typename = t.key AND t.name = 'bars'
        WHERE s.parent_obj = ? AND s.user_actor = ?
        LIMIT 1
    """, (staff_row["obj"], ua)).fetchone()

    if not bars_arr_row:
        return _get_next_obj(conn)

    # For each bar, find all chord objects and delete them + their children
    bar_rows = conn.execute("""
        SELECT bar.obj FROM score0 bar
        JOIN typenames bt ON bar.typename = bt.key AND bt.type = 'Bar'
        WHERE bar.parent_obj = ? AND bar.user_actor = ?
    """, (bars_arr_row["obj"], ua)).fetchall()

    for bar_row in bar_rows:
        _delete_bar_chords(conn, bar_row["obj"], ua)

    return _get_next_obj(conn)


def _delete_bar_chords(conn: sqlite3.Connection, bar_obj: int, ua: int) -> None:
    """Delete all chord/note objects from a bar."""
    # Find voices → duration_elements → Chords
    voices_row = conn.execute("""
        SELECT s.obj FROM score0 s
        JOIN typenames t ON s.typename = t.key AND t.name = 'voices'
        WHERE s.parent_obj = ? AND s.user_actor = ?
        LIMIT 1
    """, (bar_obj, ua)).fetchone()

    if not voices_row:
        return

    voice_rows = conn.execute("""
        SELECT s.obj FROM score0 s
        JOIN typenames t ON s.typename = t.key AND t.type = 'Voice'
        WHERE s.parent_obj = ? AND s.user_actor = ?
    """, (voices_row["obj"], ua)).fetchall()

    for voice_row in voice_rows:
        de_row = conn.execute("""
            SELECT s.obj FROM score0 s
            JOIN typenames t ON s.typename = t.key AND t.name = 'duration_elements'
            WHERE s.parent_obj = ? AND s.user_actor = ?
            LIMIT 1
        """, (voice_row["obj"], ua)).fetchone()

        if not de_row:
            continue

        # Find all Chords and delete them recursively
        chord_rows = conn.execute("""
            SELECT s.obj FROM score0 s
            JOIN typenames t ON s.typename = t.key AND t.type = 'Chord'
            WHERE s.parent_obj = ? AND s.user_actor = ?
        """, (de_row["obj"], ua)).fetchall()

        for chord_row in chord_rows:
            _delete_obj_recursive(conn, chord_row["obj"], ua)


def _delete_obj_recursive(conn: sqlite3.Connection, obj: int, ua: int) -> None:
    """Recursively delete an object and all its children."""
    # Find all children
    children = conn.execute(
        "SELECT obj FROM score0 WHERE parent_obj = ? AND user_actor = ?",
        (obj, ua),
    ).fetchall()

    for child in children:
        _delete_obj_recursive(conn, child["obj"], ua)

    # Delete the object itself
    conn.execute(
        "DELETE FROM score0 WHERE obj = ? AND user_actor = ?",
        (obj, ua),
    )


def _group_notes_by_bar(
    notes: list[Note],
    time_sigs: list,
    ppq: int,
) -> dict[int, list[tuple[Note, int]]]:
    """Group notes by bar index. Returns {bar_index: [(note, tick_offset_in_bar)]}."""
    bars: dict[int, list[tuple[Note, int]]] = {}

    current_ts_num = 4
    current_ts_den = 4
    if time_sigs:
        current_ts_num = time_sigs[0].numerator
        current_ts_den = time_sigs[0].denominator

    ticks_per_bar = int(ppq * current_ts_num * 4 / current_ts_den)

    for note in sorted(notes, key=lambda n: n.position):
        bar_index = note.position // ticks_per_bar
        tick_offset = note.position % ticks_per_bar
        bars.setdefault(bar_index, []).append((note, tick_offset))

    return bars


def _insert_bars(
    conn: sqlite3.Connection,
    part_obj: int,
    ua: int,
    bars: dict[int, list[tuple[Note, int]]],
    key_accidentals: int,
    next_obj: int,
) -> int:
    """Insert chord/note objects for each bar. Returns updated next_obj."""
    # Find duration_elements arrays for each bar
    staff_row = conn.execute("""
        SELECT staff.obj
        FROM score0 ns
        JOIN typenames nst ON ns.typename = nst.key AND nst.name = 'notation_staves'
        JOIN score0 staff ON staff.parent_obj = ns.obj AND staff.user_actor = ns.user_actor
        JOIN typenames st ON staff.typename = st.key AND st.type = 'StandardStaff'
        WHERE ns.parent_obj = ? AND ns.user_actor = ?
        LIMIT 1
    """, (part_obj, ua)).fetchone()

    if not staff_row:
        return next_obj

    bars_arr_row = conn.execute("""
        SELECT s.obj FROM score0 s
        JOIN typenames t ON s.typename = t.key AND t.name = 'bars'
        WHERE s.parent_obj = ? AND s.user_actor = ?
        LIMIT 1
    """, (staff_row["obj"], ua)).fetchone()

    if not bars_arr_row:
        return next_obj

    # Map bar_index → Bar obj
    bar_map = {}
    bar_rows = conn.execute("""
        SELECT bar.obj, bi.value as bar_index
        FROM score0 bar
        JOIN typenames bt ON bar.typename = bt.key AND bt.type = 'Bar'
        JOIN score0 bi ON bi.parent_obj = bar.obj AND bi.user_actor = bar.user_actor
        JOIN typenames bit ON bi.typename = bit.key AND bit.name = 'bar_index'
        WHERE bar.parent_obj = ? AND bar.user_actor = ?
    """, (bars_arr_row["obj"], ua)).fetchall()

    for br in bar_rows:
        bar_map[int(br["bar_index"])] = br["obj"]

    # Get typename keys
    tn = _get_typename_keys(conn)

    for bar_index, notes_in_bar in sorted(bars.items()):
        bar_obj = bar_map.get(bar_index)
        if bar_obj is None:
            continue  # Bar doesn't exist in StaffPad file

        # Find the Voice's duration_elements array
        de_arr_obj = _find_de_array(conn, bar_obj, ua)
        if de_arr_obj is None:
            continue

        # Group notes at the same position into chords
        chords: dict[int, list[tuple[Note, int]]] = {}
        for note, tick_offset in notes_in_bar:
            chords.setdefault(tick_offset, []).append((note, tick_offset))

        for tick_offset, chord_notes in sorted(chords.items()):
            # All notes at same position share the same chord
            first_note = chord_notes[0][0]
            duration_code = _ticks_to_duration_code(first_note.duration)
            beat_num, beat_den = _ticks_to_beat_position(tick_offset)

            # Create Chord object
            chord_obj = next_obj
            next_obj += 1

            # Chord attributes: 0x00[duration]0101
            chord_attrs = (duration_code << 16) | 0x0101

            # Insert Chord
            conn.execute(
                "INSERT INTO score0 VALUES (?, ?, ?, ?, ?, ?)",
                (ua, chord_obj, ua, de_arr_obj, tn["Chord_duration_elements"], _random_blob(7)),
            )

            # Chord children
            attr_obj = next_obj; next_obj += 1
            conn.execute("INSERT INTO score0 VALUES (?, ?, ?, ?, ?, ?)",
                         (ua, attr_obj, ua, chord_obj, tn["flip.Int_attributes"], chord_attrs))

            nlu_obj = next_obj; next_obj += 1
            conn.execute("INSERT INTO score0 VALUES (?, ?, ?, ?, ?, ?)",
                         (ua, nlu_obj, ua, chord_obj, tn["flip.Int_nonstandard_length_upper"], -1))

            nll_obj = next_obj; next_obj += 1
            conn.execute("INSERT INTO score0 VALUES (?, ?, ?, ?, ?, ?)",
                         (ua, nll_obj, ua, chord_obj, tn["flip.Int_nonstandard_length_lower"], 1))

            bb_obj = next_obj; next_obj += 1
            conn.execute("INSERT INTO score0 VALUES (?, ?, ?, ?, ?, ?)",
                         (ua, bb_obj, ua, chord_obj, tn["BarBeat_bar_beat"], b'\x00'))

            bi_obj = next_obj; next_obj += 1
            conn.execute("INSERT INTO score0 VALUES (?, ?, ?, ?, ?, ?)",
                         (ua, bi_obj, ua, bb_obj, tn["flip.Int_bar_index"], bar_index))

            bn_obj = next_obj; next_obj += 1
            conn.execute("INSERT INTO score0 VALUES (?, ?, ?, ?, ?, ?)",
                         (ua, bn_obj, ua, bb_obj, tn["flip.Int_numerator"], beat_num))

            bd_obj = next_obj; next_obj += 1
            conn.execute("INSERT INTO score0 VALUES (?, ?, ?, ?, ?, ?)",
                         (ua, bd_obj, ua, bb_obj, tn["flip.Int_denominator"], beat_den))

            ao_obj = next_obj; next_obj += 1
            conn.execute("INSERT INTO score0 VALUES (?, ?, ?, ?, ?, ?)",
                         (ua, ao_obj, ua, chord_obj, tn["flip.Float_absolute_offset"], 0))

            acc_obj = next_obj; next_obj += 1
            conn.execute("INSERT INTO score0 VALUES (?, ?, ?, ?, ?, ?)",
                         (ua, acc_obj, ua, chord_obj, tn["flip.Int_accents"], 0))

            notes_coll_obj = next_obj; next_obj += 1
            conn.execute("INSERT INTO score0 VALUES (?, ?, ?, ?, ?, ?)",
                         (ua, notes_coll_obj, ua, chord_obj, tn["flip.Collection_notes"], b'\x06'))

            # Insert each note in the chord
            for note, _ in chord_notes:
                staff_pos = _midi_to_staff_position(note.pitch, "treble", key_accidentals)

                note_obj = next_obj; next_obj += 1
                conn.execute("INSERT INTO score0 VALUES (?, ?, ?, ?, ?, ?)",
                             (ua, note_obj, ua, notes_coll_obj, tn["Note_notes"], _random_blob(20)))

                # Note attributes: upper32=0x00001004, lower32=staff_position
                if staff_pos < 0:
                    lower32 = staff_pos + 0x100000000
                else:
                    lower32 = staff_pos
                note_attrs = (0x00001004 << 32) | lower32

                na_obj = next_obj; next_obj += 1
                conn.execute("INSERT INTO score0 VALUES (?, ?, ?, ?, ?, ?)",
                             (ua, na_obj, ua, note_obj, tn["flip.Int_attributes_note"], note_attrs))

    return next_obj


def _find_de_array(conn: sqlite3.Connection, bar_obj: int, ua: int) -> int | None:
    """Find the duration_elements array inside a bar's first voice."""
    row = conn.execute("""
        SELECT de.obj
        FROM score0 vm
        JOIN typenames vmt ON vm.typename = vmt.key AND vmt.name = 'voices'
        JOIN score0 voice ON voice.parent_obj = vm.obj AND voice.user_actor = vm.user_actor
        JOIN typenames vt ON voice.typename = vt.key AND vt.type = 'Voice'
        JOIN score0 de ON de.parent_obj = voice.obj AND de.user_actor = voice.user_actor
        JOIN typenames det ON de.typename = det.key AND det.name = 'duration_elements'
        WHERE vm.parent_obj = ? AND vm.user_actor = ?
        LIMIT 1
    """, (bar_obj, ua)).fetchone()
    return row["obj"] if row else None


def _get_typename_keys(conn: sqlite3.Connection) -> dict[str, int]:
    """Get typename keys needed for inserting objects."""
    result = {}
    mappings = {
        "Chord_duration_elements": ("Chord", "duration_elements"),
        "flip.Int_attributes": ("flip.Int", "attributes"),
        "flip.Int_nonstandard_length_upper": ("flip.Int", "nonstandard_length_upper"),
        "flip.Int_nonstandard_length_lower": ("flip.Int", "nonstandard_length_lower"),
        "BarBeat_bar_beat": ("BarBeat", "bar_beat"),
        "flip.Int_bar_index": ("flip.Int", "bar_index"),
        "flip.Int_numerator": ("flip.Int", "numerator"),
        "flip.Int_denominator": ("flip.Int", "denominator"),
        "flip.Float_absolute_offset": ("flip.Float", "absolute_offset"),
        "flip.Int_accents": ("flip.Int", "accents"),
        "flip.Collection_notes": ("flip.Collection", "notes"),
        "Note_notes": ("Note", "notes"),
    }

    for label, (type_name, name) in mappings.items():
        row = conn.execute(
            "SELECT key FROM typenames WHERE type = ? AND name = ?",
            (type_name, name),
        ).fetchone()
        if row:
            result[label] = row["key"]

    # Note attributes uses the same typename as Chord attributes (flip.Int, attributes)
    # but for notes specifically. They share the same typename key.
    result["flip.Int_attributes_note"] = result["flip.Int_attributes"]

    return result
