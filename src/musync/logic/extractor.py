"""Extract musical data from a parsed Logic Pro project into the common model."""

from __future__ import annotations

from ..model import (
    KeySignatureEvent,
    Note,
    Project,
    TempoEvent,
    TimeSignatureEvent,
    Track,
)
from .parser import TICK_OFFSET, LogicProject

# Logic Pro SignatureKey to fifths mapping
# SignatureKey 7 = C major (0 sharps/flats)
# Each +1 adds a sharp, each -1 adds a flat
_SIG_KEY_TO_FIFTHS = {
    0: -7,  # Cb
    1: -6,  # Gb
    2: -5,  # Db
    3: -4,  # Ab
    4: -3,  # Eb
    5: -2,  # Bb
    6: -1,  # F
    7: 0,   # C
    8: 1,   # G
    9: 2,   # D
    10: 3,  # A
    11: 4,  # E
    12: 5,  # B
    13: 6,  # F#
    14: 7,  # C#
}


def extract_project(logic: LogicProject) -> Project:
    """Extract a Project from a parsed Logic Pro file."""
    project = Project(source_format="logic")
    project.title = ""  # Logic doesn't store a song title in metadata

    # Tempo
    project.tempo_events.append(
        TempoEvent(position=0, bpm=logic.metadata.bpm)
    )

    # Time signatures from metadata (initial)
    project.time_signatures.append(
        TimeSignatureEvent(
            position=0,
            numerator=logic.metadata.sig_numerator,
            denominator=logic.metadata.sig_denominator,
        )
    )

    # Time signatures from global track events
    for ts in logic.time_signatures:
        adj_pos = max(0, ts.position_tick - TICK_OFFSET)
        if adj_pos > 0:  # Skip the initial one (already added from metadata)
            project.time_signatures.append(
                TimeSignatureEvent(
                    position=adj_pos,
                    numerator=ts.numerator,
                    denominator=ts.denominator,
                )
            )

    # Key signatures
    fifths = _SIG_KEY_TO_FIFTHS.get(logic.metadata.sig_key, 0)
    project.key_signatures.append(
        KeySignatureEvent(
            position=0,
            fifths=fifths,
            mode=logic.metadata.song_mode,
        )
    )

    # Regions → Tracks
    for region in logic.regions:
        track = Track(name=region.name, instrument=region.name)

        for note_event in region.notes:
            # Adjust tick position (subtract Logic's offset)
            adj_pos = max(0, note_event.position_tick - TICK_OFFSET)

            track.notes.append(
                Note(
                    pitch=note_event.midi_note,
                    velocity=note_event.velocity,
                    position=adj_pos,
                    duration=note_event.duration_tick,
                )
            )

        project.tracks.append(track)

    return project
