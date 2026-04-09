"""Extract musical data from a parsed StaffPad project into the common model."""

from __future__ import annotations

from ..model import (
    DEFAULT_PPQ,
    KeySignatureEvent,
    Note,
    Project,
    TempoEvent,
    TimeSignatureEvent,
    Track,
    midi_to_diatonic,
)
from .parser import StfChord, StfPart, StfProject

# Duration code to beat fraction mapping
# Bits [7:4] = duration level, bit 0 = dotted
# Level 1 = whole (4 beats), 2 = double whole?, 3 = half, 4 = quarter,
# 5 = eighth, 6 = 16th, 7 = 32nd, 8 = 64th
_DURATION_LEVEL_TO_BEATS = {
    0x10: 4.0,    # whole note
    0x11: 6.0,    # dotted whole
    0x20: 8.0,    # breve (double whole)
    0x21: 12.0,   # dotted breve
    0x30: 2.0,    # half note
    0x31: 3.0,    # dotted half
    0x40: 1.0,    # quarter note
    0x41: 1.5,    # dotted quarter
    0x50: 0.5,    # eighth note
    0x51: 0.75,   # dotted eighth
    0x60: 0.25,   # 16th note
    0x61: 0.375,  # dotted 16th
    0x70: 0.125,  # 32nd note
    0x71: 0.1875, # dotted 32nd
}


def _duration_code_to_ticks(code: int, ppq: int = DEFAULT_PPQ) -> int:
    """Convert a StaffPad duration code to ticks."""
    beats = _DURATION_LEVEL_TO_BEATS.get(code)
    if beats is not None:
        return int(beats * ppq)
    # Fallback: try to decode from the level/dot pattern
    level = (code >> 4) & 0xF
    dotted = code & 1
    if level == 0:
        return ppq  # default to quarter
    base_beats = 4.0 / (2 ** (level - 1))
    if dotted:
        base_beats *= 1.5
    return int(base_beats * ppq)


def _staff_position_to_midi(
    staff_pos: int, clef: str = "treble", key_accidentals: int = 0
) -> int:
    """Convert a StaffPad staff position to MIDI note number.

    Staff position 0 = middle line of staff.
    For treble clef: middle line = B4 (MIDI 71).
    Each step = one diatonic step.
    """
    # Treble clef: position 0 = B4
    # Position +1 = C5, +2 = D5, etc.
    # Position -1 = A4, -2 = G4, etc.
    if clef == "bass":
        # Bass clef: position 0 = D3 (MIDI 50)
        reference_step = 1  # D
        reference_octave = 3
    else:
        # Treble clef: position 0 = B4 (MIDI 71)
        reference_step = 6  # B
        reference_octave = 4

    # Calculate diatonic step and octave
    total_steps = reference_step + staff_pos
    octave_offset = total_steps // 7
    step = total_steps % 7
    if step < 0:
        step += 7
        octave_offset -= 1
    octave = reference_octave + octave_offset

    # Apply key signature accidentals
    # accidentals > 0 = sharps (F C G D A E B order)
    # accidentals < 0 = flats (B E A D G C F order)
    sharp_order = [3, 0, 4, 1, 5, 2, 6]  # F C G D A E B
    flat_order = [6, 2, 5, 1, 4, 0, 3]   # B E A D G C F

    alteration = 0
    if key_accidentals > 0:
        for i in range(min(key_accidentals, 7)):
            if step == sharp_order[i]:
                alteration = 1
                break
    elif key_accidentals < 0:
        for i in range(min(-key_accidentals, 7)):
            if step == flat_order[i]:
                alteration = -1
                break

    # Convert to MIDI
    semitones = [0, 2, 4, 5, 7, 9, 11]  # C D E F G A B
    midi = (octave + 1) * 12 + semitones[step] + alteration
    return midi


def extract_project(stf: StfProject) -> Project:
    """Extract a Project from a parsed StaffPad file."""
    project = Project(source_format="staffpad")
    project.title = stf.metadata.title

    # Tempo
    if stf.metadata.default_tempo > 0:
        project.tempo_events.append(
            TempoEvent(position=0, bpm=stf.metadata.default_tempo)
        )

    # Time signatures
    for ts in stf.time_signatures:
        position = _bar_index_to_ticks(ts.bar_index, stf.time_signatures, project.ppq)
        project.time_signatures.append(
            TimeSignatureEvent(
                position=position,
                numerator=ts.numerator,
                denominator=ts.denominator,
            )
        )

    # Key signatures
    for ks in stf.key_signatures:
        position = _bar_index_to_ticks(ks.bar_index, stf.time_signatures, project.ppq)
        project.key_signatures.append(
            KeySignatureEvent(
                position=position,
                fifths=ks.accidentals,
                mode="minor" if ks.sig_type == 1 else "major",
            )
        )

    # Get the active key accidentals for pitch interpretation
    key_accidentals = stf.key_signatures[0].accidentals if stf.key_signatures else 0

    # Parts → Tracks
    for part in stf.parts:
        track = _extract_track(part, stf, key_accidentals, project.ppq)
        project.tracks.append(track)

    return project


def _bar_index_to_ticks(
    bar_index: int,
    time_sigs: list,
    ppq: int,
) -> int:
    """Convert a bar index to a tick position."""
    if bar_index == 0:
        return 0

    # Walk through bars, using time signatures to calculate ticks per bar
    ticks = 0
    current_ts_num = 4
    current_ts_den = 4

    for b in range(bar_index):
        # Check if time sig changes at this bar
        for ts in time_sigs:
            if ts.bar_index == b:
                current_ts_num = ts.numerator
                current_ts_den = ts.denominator

        # Ticks per bar = ppq * numerator * (4 / denominator)
        ticks_per_bar = int(ppq * current_ts_num * 4 / current_ts_den)
        ticks += ticks_per_bar

    return ticks


def _extract_track(
    part: StfPart,
    stf: StfProject,
    key_accidentals: int,
    ppq: int,
) -> Track:
    """Extract a Track from a StaffPad Part."""
    track = Track(
        name=part.instrument.name,
        instrument=part.instrument.musicxml_sound_id or part.instrument.name,
    )

    for bar_index, chords in sorted(part.bars.items()):
        bar_tick_start = _bar_index_to_ticks(bar_index, stf.time_signatures, ppq)
        beat_offset_ticks = 0

        for chord in chords:
            duration_ticks = _duration_code_to_ticks(chord.duration_code, ppq)

            for note in chord.notes:
                midi_pitch = _staff_position_to_midi(
                    note.staff_position,
                    clef="treble",
                    key_accidentals=key_accidentals,
                )

                track.notes.append(Note(
                    pitch=midi_pitch,
                    velocity=80,  # StaffPad doesn't store per-note velocity in attributes
                    position=bar_tick_start + beat_offset_ticks,
                    duration=duration_ticks,
                ))

            beat_offset_ticks += duration_ticks

    return track
