"""Parse StaffPad .stf project files.

StaffPad projects are SQLite databases with a specific schema:
  - metadata: key-value pairs (title, composer, etc.)
  - score0: object tree (user_actor, obj, parent, typename, value)
  - typenames: type definitions (key, type, name, is_obj)
  - usersactors: user/actor mappings
  - versions: version history
  - audio/video/image: media blobs

The object tree in score0 encodes the full score hierarchy:
  Score → Parts → StandardStaff → Bars → Voices → Chords → Notes

Duration encoding in Chord.attributes (byte 1):
  bits [7:4] = duration level: 1=whole, 2=breve?, 3=half, 4=quarter, 5=eighth, 6=16th, 7=32nd
  bit 0 = dotted flag

Note pitch in Note.attributes (lower 32 bits):
  Signed diatonic staff position (0 = middle line of staff, positive = up)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StfMetadata:
    title: str = ""
    subtitle: str = ""
    composer: str = ""
    default_tempo: float = 120.0
    bar_count: int = 0
    first_bar_number: int = 1


@dataclass
class StfTimeSignature:
    bar_index: int
    numerator: int
    denominator: int


@dataclass
class StfKeySignature:
    bar_index: int
    accidentals: int  # -7 to +7 (flats negative, sharps positive)
    sig_type: int  # 0 = major, 1 = minor?


@dataclass
class StfNote:
    staff_position: int  # diatonic position (signed, 0 = middle line)
    upper_attrs: int  # upper 32 bits of attributes


@dataclass
class StfChord:
    bar_index: int
    duration_code: int  # byte 1 of attributes
    voice_byte: int  # byte 2 of attributes
    flags_byte: int  # byte 3 of attributes
    notes: list[StfNote] = field(default_factory=list)
    beat_position: tuple[int, int] = (0, 0)  # (numerator, denominator) within bar


@dataclass
class StfInstrument:
    name: str
    abbreviation: str
    musicxml_sound_id: str
    transposition: int = 0  # playback_transposition semitones


@dataclass
class StfPart:
    part_obj: int
    instrument: StfInstrument
    bars: dict[int, list[StfChord]] = field(default_factory=dict)


@dataclass
class StfProject:
    metadata: StfMetadata
    time_signatures: list[StfTimeSignature]
    key_signatures: list[StfKeySignature]
    parts: list[StfPart]
    path: str


def parse_staffpad(path: str) -> StfProject:
    """Parse a .stf file and return structured data."""
    db_path = Path(path)
    if not db_path.exists():
        raise FileNotFoundError(f"StaffPad file not found: {path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        metadata = _parse_metadata(conn)
        time_sigs = _parse_time_signatures(conn)
        key_sigs = _parse_key_signatures(conn)
        parts = _parse_parts(conn)

        return StfProject(
            metadata=metadata,
            time_signatures=time_sigs,
            key_signatures=key_sigs,
            parts=parts,
            path=path,
        )
    finally:
        conn.close()


def _parse_metadata(conn: sqlite3.Connection) -> StfMetadata:
    """Parse score metadata from metadata table and Score object."""
    meta = StfMetadata()

    # From metadata table
    for row in conn.execute("SELECT key, value FROM metadata"):
        k, v = row["key"], row["value"]
        if k == "title":
            meta.title = v or ""
        elif k == "subtitle":
            meta.subtitle = v or ""
        elif k == "composer":
            meta.composer = v or ""

    # From Score object (obj=1 in user_actor that has it)
    for row in conn.execute("""
        SELECT t.name, s.value FROM score0 s
        JOIN typenames t ON s.typename = t.key
        WHERE s.parent_obj = 1 AND t.name IN ('default_tempo', 'bar_count', 'first_bar_number')
    """):
        name, val = row["name"], row["value"]
        if name == "default_tempo" and val is not None:
            meta.default_tempo = float(val)
        elif name == "bar_count" and val is not None:
            meta.bar_count = int(val)
        elif name == "first_bar_number" and val is not None:
            meta.first_bar_number = int(val)

    return meta


def _parse_time_signatures(conn: sqlite3.Connection) -> list[StfTimeSignature]:
    """Parse time signatures."""
    results = []
    # Find TimeSignature objects
    rows = conn.execute("""
        SELECT ts.obj, ts.user_actor
        FROM score0 ts
        JOIN typenames t ON ts.typename = t.key
        WHERE t.type = 'TimeSignature'
    """).fetchall()

    for row in rows:
        ts_obj = row["obj"]
        ua = row["user_actor"]

        children = {}
        for child in conn.execute("""
            SELECT t.name, s.value FROM score0 s
            JOIN typenames t ON s.typename = t.key
            WHERE s.parent_obj = ? AND s.user_actor = ?
        """, (ts_obj, ua)):
            children[child["name"]] = child["value"]

        results.append(StfTimeSignature(
            bar_index=int(children.get("bar_index", 0)),
            numerator=int(children.get("top", 4)),
            denominator=int(children.get("bottom", 4)),
        ))

    return sorted(results, key=lambda ts: ts.bar_index)


def _parse_key_signatures(conn: sqlite3.Connection) -> list[StfKeySignature]:
    """Parse key signatures."""
    results = []
    rows = conn.execute("""
        SELECT ks.obj, ks.user_actor
        FROM score0 ks
        JOIN typenames t ON ks.typename = t.key
        WHERE t.type = 'KeySignature'
    """).fetchall()

    for row in rows:
        ks_obj = row["obj"]
        ua = row["user_actor"]

        children = {}
        for child in conn.execute("""
            SELECT t.name, s.value FROM score0 s
            JOIN typenames t ON s.typename = t.key
            WHERE s.parent_obj = ? AND s.user_actor = ?
        """, (ks_obj, ua)):
            children[child["name"]] = child["value"]

        results.append(StfKeySignature(
            bar_index=int(children.get("bar_index", 0)),
            accidentals=int(children.get("accidentals", 0)),
            sig_type=int(children.get("type", 0)),
        ))

    return sorted(results, key=lambda ks: ks.bar_index)


def _parse_parts(conn: sqlite3.Connection) -> list[StfPart]:
    """Parse all parts with their instrument info and notes."""
    parts = []

    # Find Part objects (user_actor=1 typically)
    part_rows = conn.execute("""
        SELECT s.obj, s.user_actor
        FROM score0 s
        JOIN typenames t ON s.typename = t.key
        WHERE t.type = 'Part' AND t.name = 'tracks'
        ORDER BY s.obj
    """).fetchall()

    for part_row in part_rows:
        part_obj = part_row["obj"]
        ua = part_row["user_actor"]

        instrument = _parse_instrument(conn, part_obj, ua)
        if instrument.name == "Video":
            continue  # Skip video tracks

        part = StfPart(part_obj=part_obj, instrument=instrument)
        _parse_part_notes(conn, part, ua)
        parts.append(part)

    return parts


def _parse_instrument(conn: sqlite3.Connection, part_obj: int, ua: int) -> StfInstrument:
    """Parse instrument info from InstrumentChange child."""
    inst = StfInstrument(name="Unknown", abbreviation="", musicxml_sound_id="")

    # Find InstrumentChange via instrument_changes collection
    ic_rows = conn.execute("""
        SELECT ic.obj
        FROM score0 coll
        JOIN typenames ct ON coll.typename = ct.key AND ct.name = 'instrument_changes'
        JOIN score0 ic ON ic.parent_obj = coll.obj AND ic.user_actor = coll.user_actor
        JOIN typenames ict ON ic.typename = ict.key AND ict.type = 'InstrumentChange'
        WHERE coll.parent_obj = ? AND coll.user_actor = ?
        LIMIT 1
    """, (part_obj, ua)).fetchall()

    if not ic_rows:
        return inst

    ic_obj = ic_rows[0]["obj"]

    for row in conn.execute("""
        SELECT t.name, s.value, typeof(s.value) as vtype
        FROM score0 s
        JOIN typenames t ON s.typename = t.key
        WHERE s.parent_obj = ? AND s.user_actor = ?
        AND t.name IN ('name', 'abbreviation', 'musicxml_sound_id', 'playback_transposition')
    """, (ic_obj, ua)):
        name = row["name"]
        val = row["value"]
        if name == "name" and val is not None:
            if isinstance(val, bytes):
                inst.name = val.decode("utf-8", errors="replace")
            else:
                inst.name = str(val)
        elif name == "abbreviation" and val is not None:
            if isinstance(val, bytes):
                inst.abbreviation = val.decode("utf-8", errors="replace")
            else:
                inst.abbreviation = str(val)
        elif name == "musicxml_sound_id" and val is not None:
            if isinstance(val, bytes):
                inst.musicxml_sound_id = val.decode("utf-8", errors="replace")
            else:
                inst.musicxml_sound_id = str(val)
        elif name == "playback_transposition" and val is not None:
            inst.transposition = int(val)

    return inst


def _parse_part_notes(conn: sqlite3.Connection, part: StfPart, ua: int) -> None:
    """Parse all notes for a part using step-by-step queries for performance."""
    # Step 1: Find the bars array for this part
    # Part → notation_staves → StandardStaff → bars
    staff_obj = _find_child_obj(conn, part.part_obj, ua, "notation_staves", "StandardStaff")
    if staff_obj is None:
        return

    bars_arr_obj = _find_child_by_name(conn, staff_obj, ua, "bars")
    if bars_arr_obj is None:
        return

    # Step 2: Get all Bar objects with their bar_index
    bar_rows = conn.execute("""
        SELECT bar.obj, bi.value as bar_index
        FROM score0 bar
        JOIN typenames bt ON bar.typename = bt.key AND bt.type = 'Bar'
        JOIN score0 bi ON bi.parent_obj = bar.obj AND bi.user_actor = bar.user_actor
        JOIN typenames bit ON bi.typename = bit.key AND bit.name = 'bar_index'
        WHERE bar.parent_obj = ? AND bar.user_actor = ?
        ORDER BY CAST(bi.value AS INTEGER)
    """, (bars_arr_obj, ua)).fetchall()

    for bar_row in bar_rows:
        bar_obj = bar_row["obj"]
        bar_index = int(bar_row["bar_index"])

        chords = _parse_bar_chords_stepwise(conn, bar_obj, bar_index, ua)
        if chords:
            part.bars[bar_index] = chords


def _find_child_obj(
    conn: sqlite3.Connection, parent_obj: int, ua: int,
    collection_name: str, child_type: str
) -> int | None:
    """Find a child object through a collection: parent → collection → typed child."""
    row = conn.execute("""
        SELECT child.obj
        FROM score0 coll
        JOIN typenames ct ON coll.typename = ct.key AND ct.name = ?
        JOIN score0 child ON child.parent_obj = coll.obj AND child.user_actor = coll.user_actor
        JOIN typenames cht ON child.typename = cht.key AND cht.type = ?
        WHERE coll.parent_obj = ? AND coll.user_actor = ?
        LIMIT 1
    """, (collection_name, child_type, parent_obj, ua)).fetchone()
    return row["obj"] if row else None


def _find_child_by_name(
    conn: sqlite3.Connection, parent_obj: int, ua: int, name: str
) -> int | None:
    """Find a direct child object by typename name."""
    row = conn.execute("""
        SELECT s.obj FROM score0 s
        JOIN typenames t ON s.typename = t.key AND t.name = ?
        WHERE s.parent_obj = ? AND s.user_actor = ?
        LIMIT 1
    """, (name, parent_obj, ua)).fetchone()
    return row["obj"] if row else None


def _parse_bar_chords_stepwise(
    conn: sqlite3.Connection, bar_obj: int, bar_index: int, ua: int
) -> list[StfChord]:
    """Parse all chords in a bar using stepwise queries."""
    chords = []

    # Step 1: Find voices map → Voice objects
    voices_map_obj = _find_child_by_name(conn, bar_obj, ua, "voices")
    if voices_map_obj is None:
        return chords

    voice_rows = conn.execute("""
        SELECT s.obj FROM score0 s
        JOIN typenames t ON s.typename = t.key AND t.type = 'Voice'
        WHERE s.parent_obj = ? AND s.user_actor = ?
    """, (voices_map_obj, ua)).fetchall()

    for voice_row in voice_rows:
        voice_obj = voice_row["obj"]

        # Step 2: Find duration_elements array
        de_arr_obj = _find_child_by_name(conn, voice_obj, ua, "duration_elements")
        if de_arr_obj is None:
            continue

        # Step 3: Get Chord objects with attributes
        chord_rows = conn.execute("""
            SELECT chord.obj, attr.value as attrs
            FROM score0 chord
            JOIN typenames ct ON chord.typename = ct.key AND ct.type = 'Chord'
            JOIN score0 attr ON attr.parent_obj = chord.obj AND attr.user_actor = chord.user_actor
            JOIN typenames at2 ON attr.typename = at2.key AND at2.name = 'attributes'
            WHERE chord.parent_obj = ? AND chord.user_actor = ?
            ORDER BY chord.obj
        """, (de_arr_obj, ua)).fetchall()

        for crow in chord_rows:
            chord_obj = crow["obj"]
            attrs = int(crow["attrs"])

            duration_code = (attrs >> 16) & 0xFF
            voice_byte = (attrs >> 8) & 0xFF
            flags_byte = attrs & 0xFF

            chord = StfChord(
                bar_index=bar_index,
                duration_code=duration_code,
                voice_byte=voice_byte,
                flags_byte=flags_byte,
            )

            # Step 4: Get notes
            notes_coll_obj = _find_child_by_name(conn, chord_obj, ua, "notes")
            if notes_coll_obj is not None:
                note_rows = conn.execute("""
                    SELECT attr.value as attrs
                    FROM score0 note
                    JOIN typenames nt ON note.typename = nt.key AND nt.type = 'Note'
                    JOIN score0 attr ON attr.parent_obj = note.obj AND attr.user_actor = note.user_actor
                    JOIN typenames at2 ON attr.typename = at2.key AND at2.name = 'attributes'
                    WHERE note.parent_obj = ? AND note.user_actor = ?
                """, (notes_coll_obj, ua)).fetchall()

                for nrow in note_rows:
                    note_attrs = int(nrow["attrs"])
                    lower32 = note_attrs & 0xFFFFFFFF
                    if lower32 >= 0x80000000:
                        staff_pos = lower32 - 0x100000000
                    else:
                        staff_pos = lower32
                    upper32 = (note_attrs >> 32) & 0xFFFFFFFF

                    chord.notes.append(StfNote(
                        staff_position=staff_pos,
                        upper_attrs=upper32,
                    ))

            chords.append(chord)

    return chords
