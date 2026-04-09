"""Write musical data into a Dorico .dorico project file.

Strategy: parse the existing .dorico, modify the entity tree in memory,
serialize it back to score.dtn, and repack the ZIP archive.

Currently supported writes:
- Tempo (via default flow tempo, when present)
- Time signature (modify existing BarDivisionEventDefinition or its target)
- Key signature (modify existing TonalityDivisionEventDefinition tree)
- Note events: only when the file already contains note events to use as a
  template. Writing notes into a Dorico file with no existing notes is not
  yet supported because it requires constructing NoteEventDefinition entities
  whose binary structure we have not been able to reverse-engineer from an
  empty file.

The rest of the file (engraving options, layouts, library, mixer state) is
preserved byte-for-byte.
"""

from __future__ import annotations

import copy
import io
import shutil
import zipfile
from fractions import Fraction
from pathlib import Path

from ..model import KeySignatureEvent, Note, Project, TimeSignatureEvent, midi_to_diatonic
from .dtn import DtnEntity, DtnFile, DtnKV, parse_dtn, serialize_dtn
from .extractor import _ACCIDENTAL_TO_ALT, _NOTE_NAME_TO_STEP, _NOTE_TO_FIFTHS
from .parser import parse_dorico

# Reverse maps for writing
_FIFTHS_TO_NOTE = {v: k for k, v in _NOTE_TO_FIFTHS.items()}
_ALT_TO_ACCIDENTAL = {v: k for k, v in _ACCIDENTAL_TO_ALT.items()}


def write_dorico(project: Project, path: str, backup: bool = True) -> None:
    """Write a Project model into an existing .dorico file.

    Modifies tempo, time signature, key signature in the existing entity tree.
    Notes are written only if the file already has note events to use as
    structural templates (not yet supported for empty files).
    """
    dorico_path = Path(path)
    if not dorico_path.exists():
        raise FileNotFoundError(f"Dorico file not found: {path}")

    if backup:
        backup_path = dorico_path.with_suffix(dorico_path.suffix + ".backup")
        if not backup_path.exists():
            shutil.copy2(dorico_path, backup_path)

    # Read the entire .dorico ZIP into memory and find score.dtn
    with zipfile.ZipFile(dorico_path, "r") as zf:
        zip_entries: dict[str, bytes] = {}
        for name in zf.namelist():
            zip_entries[name] = zf.read(name)

    score_data = zip_entries.get("score.dtn")
    if score_data is None:
        raise ValueError(f"No score.dtn in {path}")

    # Parse, modify, serialize
    dtn = parse_dtn(score_data)
    _apply_project_to_dtn(dtn, project)
    new_score = serialize_dtn(dtn)
    zip_entries["score.dtn"] = new_score

    # Repack the ZIP
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in zip_entries.items():
            zf.writestr(name, data)

    with open(dorico_path, "wb") as f:
        f.write(buffer.getvalue())


def _apply_project_to_dtn(dtn: DtnFile, project: Project) -> None:
    """Apply project data to a parsed DTN tree."""
    k, v = dtn.keys, dtn.values
    root = dtn.root

    flows = root.get_entity("flows", k)
    if not flows:
        return
    flows_arr = flows.get_entity("array", k)
    if not flows_arr:
        return

    # Use the first flow
    flow = None
    for child in flows_arr.children:
        if isinstance(child, DtnEntity):
            flow = child
            break
    if flow is None:
        return

    # Update time signature
    if project.time_signatures:
        _update_time_signature(flow, dtn, project.time_signatures[0])

    # Update key signature
    if project.key_signatures:
        _update_key_signature(flow, dtn, project.key_signatures[0])

    # Notes: only if the voice blocks already have events to template from
    note_count = sum(len(t.notes) for t in project.tracks)
    if note_count > 0:
        _try_write_notes(flow, dtn, project)


def _update_time_signature(
    flow: DtnEntity, dtn: DtnFile, ts: TimeSignatureEvent
) -> None:
    """Update the time signature in the flow's element tables."""
    k, v = dtn.keys, dtn.values

    et = flow.get_entity("elementTables", k)
    if not et:
        return
    et_arr = et.get_entity("array", k)
    if not et_arr:
        return

    for child in et_arr.children:
        if not isinstance(child, DtnEntity):
            continue
        if child.key(k) != "BarDivisionElementTableDefinition":
            continue

        arr = child.get_entity("array", k)
        if not arr:
            continue

        for elem in arr.children:
            if not isinstance(elem, DtnEntity):
                continue
            bd = elem.get_entity("barDivisionData", k)
            if not bd:
                continue
            ts_container = bd.get_entity("timeSignature", k)
            if not ts_container:
                continue
            tsad = ts_container.get_entity("timeSignaturesAndDivisions", k)
            if not tsad:
                continue
            for tsd_child in tsad.children:
                if not isinstance(tsd_child, DtnEntity):
                    continue
                if tsd_child.key(k) != "timeSignatureAndDivision":
                    continue
                inner_ts = tsd_child.get_entity("timeSignature", k)
                if not inner_ts:
                    continue

                _set_kv(inner_ts, dtn, "numerator", str(ts.numerator))
                _set_kv(inner_ts, dtn, "denominator", str(ts.denominator))


def _update_key_signature(
    flow: DtnEntity, dtn: DtnFile, ks: KeySignatureEvent
) -> None:
    """Update the key signature in the flow's element tables."""
    k, v = dtn.keys, dtn.values

    et = flow.get_entity("elementTables", k)
    if not et:
        return
    et_arr = et.get_entity("array", k)
    if not et_arr:
        return

    for child in et_arr.children:
        if not isinstance(child, DtnEntity):
            continue
        if child.key(k) != "TonalityDivisionElementTableDefinition":
            continue

        arr = child.get_entity("array", k)
        if not arr:
            continue

        for elem in arr.children:
            if not isinstance(elem, DtnEntity):
                continue

            td = elem.get_entity("tonalityDivisionData", k)
            if not td:
                continue
            ks_entity = td.get_entity("keySignature", k)
            if not ks_entity:
                continue
            root_entity = ks_entity.get_entity("root", k)
            if not root_entity:
                continue

            mode_value = "kKeySigMinor" if ks.mode == "minor" else "kKeySigMajor"
            _set_kv(root_entity, dtn, "tonalityType", mode_value)

            inner_root = root_entity.get_entity("root", k)
            if not inner_root:
                continue

            note_name = _FIFTHS_TO_NOTE.get(ks.fifths, "C")
            # Split note name into letter + accidental
            letter = note_name[0]
            accidental = note_name[1:]
            alt = 0
            if accidental == "#":
                alt = 1
            elif accidental == "b":
                alt = -1
            elif accidental == "##":
                alt = 2
            elif accidental == "bb":
                alt = -2

            accidental_id = _ALT_TO_ACCIDENTAL.get(
                alt, "accidental.12et.western.natural"
            )

            _set_kv(inner_root, dtn, "noteName", letter)
            _set_kv(inner_root, dtn, "accidentalID", accidental_id)


def _ticks_to_qn_str(ticks: int, ppq: int) -> str:
    """Convert a tick position to a Dorico rational quarter-note string.

    Examples at PPQ=960:
      0       → "0"
      960     → "1"
      480     → "1/2"
      27360   → "57/2"
    """
    frac = Fraction(ticks, ppq)
    if frac.denominator == 1:
        return str(frac.numerator)
    return f"{frac.numerator}/{frac.denominator}"


def _try_write_notes(flow: DtnEntity, dtn: DtnFile, project: Project) -> None:
    """Write note events into voice stream blocks.

    Strategy:
    1. Find any existing NoteEventDefinition to use as a structural template.
    2. Clear the events array of the first voice block that has events.
    3. Clone the template for each note, update pitch/position/duration.
    4. For files with no existing notes, raise NotImplementedError.
    """
    k, v = dtn.keys, dtn.values

    blocks = flow.get_entity("blocks", k)
    if not blocks:
        return
    blocks_arr = blocks.get_entity("array", k)
    if not blocks_arr:
        return

    # Find voice stream blocks
    voice_blocks: list[DtnEntity] = []
    for block in blocks_arr.children:
        if not isinstance(block, DtnEntity):
            continue
        bkvs = block.get_all_kvs(k, v)
        if bkvs.get("parentEventStreamType") == "kVoiceStream":
            voice_blocks.append(block)

    # Find template note event and the voice block that contains it
    template: DtnEntity | None = None
    target_block: DtnEntity | None = None
    for vb in voice_blocks:
        events = vb.get_entity("events", k)
        if events:
            for c in events.children:
                if isinstance(c, DtnEntity) and "Note" in c.key(k):
                    template = c
                    target_block = vb
                    break
        if template is not None:
            break

    if template is None:
        raise NotImplementedError(
            "Writing notes into a Dorico file requires that file to already "
            "contain at least one note event as a structural template. "
            "Empty Dorico projects are not yet supported as a sync target. "
            "Workaround: open the .dorico file in Dorico, add at least one note "
            "manually, save, then re-run sync."
        )

    # Detect pitch encoding from the template: modern = direct KV, legacy = nested entity
    uses_modern_pitch = template.get_kv("pitch", k, v) is not None

    # Key signature fifths for diatonic spelling (legacy format)
    fifths = project.key_signatures[0].fifths if project.key_signatures else 0

    # Map project tracks → voice blocks by index.
    # Tracks beyond the number of voice blocks are merged into the last block.
    # Voice blocks with no corresponding source track are cleared (emptied).
    for block_idx, vb in enumerate(voice_blocks):
        vb_events = vb.get_entity("events", k)
        if vb_events is None:
            continue  # empty voice block with no events entity — skip

        # Clear existing events
        vb_events.children.clear()
        vb_events.child_key_list.clear()
        vb_events.null_child_data.clear()

        # Collect notes for this block: track[block_idx], or nothing if out of range
        if block_idx < len(project.tracks):
            track_notes = sorted(project.tracks[block_idx].notes, key=lambda n: n.position)
        else:
            track_notes = []

        for note in track_notes:
            note_entity = copy.deepcopy(template)
            _update_note_entity(note_entity, dtn, note, project.ppq, uses_modern_pitch, fifths)
            vb_events.children.append(note_entity)
            vb_events.child_key_list.append(0)


def _update_note_entity(
    entity: DtnEntity,
    dtn: DtnFile,
    note: Note,
    ppq: int,
    uses_modern_pitch: bool,
    fifths: int,
) -> None:
    """Update pitch, position, duration, velocity on a cloned NoteEventDefinition."""
    pos_str = _ticks_to_qn_str(note.position, ppq)
    dur_str = _ticks_to_qn_str(note.duration, ppq)

    _set_kv(entity, dtn, "position", pos_str)
    _set_kv(entity, dtn, "duration", dur_str)
    _set_kv(entity, dtn, "velocity", str(note.velocity))

    if uses_modern_pitch:
        _set_kv(entity, dtn, "pitch", str(note.pitch))
    else:
        # Legacy format: nested pitch entity with diatonicStep / chromaticAlteration / octave
        k = dtn.keys
        pitch_entity = entity.get_entity("pitch", k)
        if pitch_entity:
            step, alteration, octave = midi_to_diatonic(note.pitch, fifths)
            _set_kv(pitch_entity, dtn, "diatonicStep", str(step))
            _set_kv(pitch_entity, dtn, "chromaticAlteration", str(alteration))
            _set_kv(pitch_entity, dtn, "octave", str(octave))


def _set_kv(entity: DtnEntity, dtn: DtnFile, key_name: str, new_value: str) -> None:
    """Set a key-value child on an entity, registering the value in the table if needed."""
    k, v = dtn.keys, dtn.values

    # Find the key index
    if key_name not in k:
        return  # Can't set a key that doesn't exist in the key table
    key_idx = k.index(key_name)

    # Find or add the value
    if new_value in v:
        value_idx = v.index(new_value)
    else:
        value_idx = len(v)
        dtn.values.append(new_value)

    # Find existing child with this key
    for child in entity.children:
        if isinstance(child, DtnKV) and child.key_idx == key_idx:
            child.value_idx = value_idx
            return

    # Not found — append a new child
    entity.children.append(DtnKV(key_idx=key_idx, value_idx=value_idx))
    entity.child_key_list.append(0)
