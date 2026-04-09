"""Common music data model shared between Logic Pro and Dorico."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


DEFAULT_PPQ = 960


class DynamicType(str, Enum):
    PPPP = "pppp"
    PPP = "ppp"
    PP = "pp"
    P = "p"
    MP = "mp"
    MF = "mf"
    F = "f"
    FF = "ff"
    FFF = "fff"
    FFFF = "ffff"
    FP = "fp"
    SFZ = "sfz"
    SF = "sf"
    SFF = "sff"
    SFP = "sfp"
    SFPP = "sfpp"
    RFZ = "rfz"
    FZ = "fz"


class ArticulationType(str, Enum):
    STACCATO = "staccato"
    STACCATISSIMO = "staccatissimo"
    ACCENT = "accent"
    MARCATO = "marcato"
    TENUTO = "tenuto"
    FERMATA = "fermata"
    TRILL = "trill"
    MORDENT = "mordent"
    TURN = "turn"
    PORTATO = "portato"  # tenuto + staccato


@dataclass
class Note:
    """A single musical note event."""

    pitch: int  # MIDI note number 0-127
    velocity: int  # 0-127
    position: int  # ticks from start of flow/region
    duration: int  # ticks
    channel: int = 0  # voice/layer index

    # Dorico-enriched pitch representation (for lossless round-trip)
    diatonic_step: int | None = None  # 0=C, 1=D, 2=E, 3=F, 4=G, 5=A, 6=B
    chromatic_alteration: int | None = None  # -2=bb, -1=b, 0=natural, 1=#, 2=##
    octave: int | None = None  # scientific pitch octave

    def __post_init__(self) -> None:
        if self.diatonic_step is not None and self.octave is not None:
            # Verify consistency with MIDI pitch
            expected = diatonic_to_midi(
                self.diatonic_step,
                self.chromatic_alteration or 0,
                self.octave,
            )
            if expected != self.pitch:
                raise ValueError(
                    f"Pitch mismatch: MIDI {self.pitch} vs "
                    f"diatonic ({self.diatonic_step}, {self.chromatic_alteration}, {self.octave}) = {expected}"
                )


@dataclass
class TempoEvent:
    """A tempo change at a given position."""

    position: int  # ticks
    bpm: float


@dataclass
class TimeSignatureEvent:
    """A time signature change."""

    position: int  # ticks
    numerator: int
    denominator: int  # as actual value (4 = quarter, 8 = eighth, etc.)


@dataclass
class KeySignatureEvent:
    """A key signature change."""

    position: int  # ticks
    fifths: int  # -7 to +7 (negative = flats, positive = sharps)
    mode: str  # "major" or "minor"

    @property
    def key_name(self) -> str:
        major_keys = [
            "Cb", "Gb", "Db", "Ab", "Eb", "Bb", "F",
            "C", "G", "D", "A", "E", "B", "F#", "C#",
        ]
        minor_keys = [
            "Ab", "Eb", "Bb", "F", "C", "G", "D",
            "A", "E", "B", "F#", "C#", "G#", "D#", "A#",
        ]
        idx = self.fifths + 7
        if self.mode == "minor":
            return minor_keys[idx] + "m"
        return major_keys[idx]


@dataclass
class Dynamic:
    """A dynamic marking."""

    position: int  # ticks
    type: DynamicType


@dataclass
class Hairpin:
    """A crescendo or diminuendo hairpin."""

    position: int  # ticks (start)
    end_position: int  # ticks (end)
    crescendo: bool  # True = crescendo, False = diminuendo


@dataclass
class Articulation:
    """An articulation attached to a note."""

    position: int  # ticks — matches the Note.position it belongs to
    pitch: int  # MIDI pitch — identifies which note at that position
    type: ArticulationType


@dataclass
class Track:
    """A single instrument track/player."""

    name: str
    instrument: str  # instrument identifier (e.g. "flugelhorn", "piano")
    notes: list[Note] = field(default_factory=list)
    dynamics: list[Dynamic] = field(default_factory=list)
    hairpins: list[Hairpin] = field(default_factory=list)
    articulations: list[Articulation] = field(default_factory=list)

    def sorted(self) -> Track:
        """Return a copy with all events sorted by position."""
        return Track(
            name=self.name,
            instrument=self.instrument,
            notes=sorted(self.notes, key=lambda n: (n.position, n.pitch)),
            dynamics=sorted(self.dynamics, key=lambda d: d.position),
            hairpins=sorted(self.hairpins, key=lambda h: h.position),
            articulations=sorted(self.articulations, key=lambda a: (a.position, a.pitch)),
        )


@dataclass
class Project:
    """Complete musical project — the common representation."""

    tempo_events: list[TempoEvent] = field(default_factory=list)
    time_signatures: list[TimeSignatureEvent] = field(default_factory=list)
    key_signatures: list[KeySignatureEvent] = field(default_factory=list)
    tracks: list[Track] = field(default_factory=list)
    ppq: int = DEFAULT_PPQ

    # Source metadata (not synced, just informational)
    title: str = ""
    source_format: str = ""  # "logic" or "dorico"

    def sorted(self) -> Project:
        """Return a copy with all events sorted."""
        return Project(
            tempo_events=sorted(self.tempo_events, key=lambda t: t.position),
            time_signatures=sorted(self.time_signatures, key=lambda t: t.position),
            key_signatures=sorted(self.key_signatures, key=lambda k: k.position),
            tracks=[t.sorted() for t in self.tracks],
            ppq=self.ppq,
            title=self.title,
            source_format=self.source_format,
        )


# --- Pitch conversion utilities ---

# Semitone offsets for each diatonic step from C
_DIATONIC_SEMITONES = [0, 2, 4, 5, 7, 9, 11]  # C D E F G A B

# Diatonic step names
STEP_NAMES = ["C", "D", "E", "F", "G", "A", "B"]

# Alteration names
ALTERATION_NAMES = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}


def diatonic_to_midi(step: int, alteration: int, octave: int) -> int:
    """Convert Dorico-style diatonic pitch to MIDI note number.

    Args:
        step: 0=C, 1=D, 2=E, 3=F, 4=G, 5=A, 6=B
        alteration: -2=double flat, -1=flat, 0=natural, 1=sharp, 2=double sharp
        octave: scientific pitch octave (middle C = octave 4)

    Returns:
        MIDI note number (middle C = 60)
    """
    return (octave + 1) * 12 + _DIATONIC_SEMITONES[step] + alteration


def midi_to_diatonic(
    midi_note: int, fifths: int = 0
) -> tuple[int, int, int]:
    """Convert MIDI note number to diatonic pitch representation.

    Uses the key signature (fifths) to choose the correct enharmonic spelling.

    Args:
        midi_note: MIDI note number (0-127)
        fifths: key signature (-7 to +7)

    Returns:
        (step, alteration, octave) tuple
    """
    octave = (midi_note // 12) - 1
    pitch_class = midi_note % 12

    # Build the scale based on key signature
    # Sharp order: F C G D A E B
    # Flat order: B E A D G C F
    sharp_order = [3, 0, 4, 1, 5, 2, 6]  # steps: F C G D A E B
    flat_order = [6, 2, 5, 1, 4, 0, 3]  # steps: B E A D G C F

    # Start with natural notes, apply key signature
    alterations = [0] * 7  # one per diatonic step
    if fifths > 0:
        for i in range(fifths):
            alterations[sharp_order[i]] = 1
    elif fifths < 0:
        for i in range(-fifths):
            alterations[flat_order[i]] = -1

    # Try to find matching diatonic note in key
    for step in range(7):
        base = _DIATONIC_SEMITONES[step] + alterations[step]
        if base % 12 == pitch_class:
            # Adjust octave if the alteration pushes us across a boundary
            actual_octave = octave
            if base < 0:
                actual_octave += 1
            elif base >= 12:
                actual_octave -= 1
            return step, alterations[step], actual_octave

    # Not in key — find closest diatonic spelling
    # Prefer sharps in sharp keys, flats in flat keys
    for step in range(7):
        base_semitone = _DIATONIC_SEMITONES[step]
        diff = (pitch_class - base_semitone) % 12
        if diff == 0:
            return step, 0, octave
        if diff == 1:
            return step, 1, octave
        if diff == 11:  # -1 mod 12
            actual_octave = octave
            if base_semitone == 0:  # C flat wraps
                actual_octave += 1
            return step, -1, actual_octave

    # Fallback: use sharp spelling
    for step in range(7):
        base_semitone = _DIATONIC_SEMITONES[step]
        diff = (pitch_class - base_semitone) % 12
        if diff <= 2:
            return step, diff, octave

    return 0, 0, octave  # should never reach here
