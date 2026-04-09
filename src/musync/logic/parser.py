"""Parse Logic Pro .logicx project files.

A .logicx file is a macOS directory bundle containing:
  - Resources/ProjectInformation.plist: version info
  - Alternatives/000/MetaData.plist: song metadata (tempo, key, time sig)
  - Alternatives/000/ProjectData: binary file with all musical content

ProjectData binary format:
  - Magic: 0x2347c0ab
  - Reversed FourCC chunks: gnoS(Song), qeSM(MSeq), karT(Trak),
    qSvE(EvSq), etc.
  - PPQ = 960, tick offset = 38400 before bar 1
  - MIDI notes: 64-byte records (4×16-byte sub-records)
"""

from __future__ import annotations

import plistlib
import struct
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = b"\x23\x47\xc0\xab"
PPQ = 960
TICK_OFFSET = 38400  # Ticks before bar 1

# FourCC tags (as they appear in the file, reversed from readable form)
TAG_SONG = b"gnoS"
TAG_MSEQ = b"qeSM"
TAG_TRAK = b"karT"
TAG_EVSQ = b"qSvE"


@dataclass
class LogicMetadata:
    bpm: float = 120.0
    sample_rate: int = 48000
    num_tracks: int = 0
    song_key: str = "C"
    song_mode: str = "major"
    sig_key: int = 7  # SignatureKey
    sig_numerator: int = 4
    sig_denominator: int = 4


@dataclass
class LogicNoteEvent:
    """A MIDI note event extracted from an EvSq chunk."""
    midi_note: int  # 0-127
    velocity: int  # 0-127
    position_tick: int  # absolute tick position
    duration_tick: int  # duration in ticks
    onset_us: int  # onset time in microseconds
    duration_us: int  # duration in microseconds


@dataclass
class LogicTimeSignature:
    position_tick: int
    numerator: int
    denominator: int


@dataclass
class LogicKeySignature:
    position_tick: int
    key_index: int  # Logic's internal key index


@dataclass
class LogicTrackRegion:
    """A region of MIDI events on a track."""
    name: str
    notes: list[LogicNoteEvent] = field(default_factory=list)


@dataclass
class LogicProject:
    """A parsed Logic Pro project."""
    metadata: LogicMetadata
    time_signatures: list[LogicTimeSignature]
    key_signatures: list[LogicKeySignature]
    regions: list[LogicTrackRegion]
    path: str


def parse_logic(path: str) -> LogicProject:
    """Parse a .logicx project."""
    proj_path = Path(path)
    if not proj_path.exists():
        raise FileNotFoundError(f"Logic project not found: {path}")

    # Handle both the .logicx package and the parent directory
    if proj_path.suffix == ".logicx":
        logicx_path = proj_path
    else:
        # Look for .logicx inside the directory
        logicx_candidates = list(proj_path.glob("*.logicx"))
        if not logicx_candidates:
            logicx_candidates = list(proj_path.glob("*/*.logicx"))
        if not logicx_candidates:
            raise FileNotFoundError(f"No .logicx package found in {path}")
        logicx_path = logicx_candidates[0]

    # Parse metadata plist
    metadata = _parse_metadata(logicx_path)

    # Parse ProjectData binary
    project_data_path = logicx_path / "Alternatives" / "000" / "ProjectData"
    if not project_data_path.exists():
        raise FileNotFoundError(f"ProjectData not found: {project_data_path}")

    with open(project_data_path, "rb") as f:
        data = f.read()

    if data[:4] != MAGIC:
        raise ValueError(f"Invalid ProjectData magic: {data[:4].hex()}")

    time_sigs, key_sigs, regions = _parse_project_data(data)

    return LogicProject(
        metadata=metadata,
        time_signatures=time_sigs,
        key_signatures=key_sigs,
        regions=regions,
        path=str(proj_path),
    )


def _parse_metadata(logicx_path: Path) -> LogicMetadata:
    """Parse MetaData.plist for song-level info."""
    meta_path = logicx_path / "Alternatives" / "000" / "MetaData.plist"
    meta = LogicMetadata()

    if not meta_path.exists():
        return meta

    with open(meta_path, "rb") as f:
        plist = plistlib.load(f)

    meta.bpm = float(plist.get("BeatsPerMinute", 120.0))
    meta.sample_rate = int(plist.get("SampleRate", 48000))
    meta.num_tracks = int(plist.get("NumberOfTracks", 0))
    meta.song_key = plist.get("SongKey", "C")
    meta.song_mode = plist.get("SongGenderKey", "major")
    meta.sig_key = int(plist.get("SignatureKey", 7))
    meta.sig_numerator = int(plist.get("SongSignatureNumerator", 4))
    meta.sig_denominator = int(plist.get("SongSignatureDenominator", 4))

    return meta


def _parse_project_data(
    data: bytes,
) -> tuple[list[LogicTimeSignature], list[LogicKeySignature], list[LogicTrackRegion]]:
    """Parse the ProjectData binary for MIDI events."""
    # Find all EvSq chunks
    evsq_positions = []
    for i in range(len(data) - 4):
        if data[i : i + 4] == TAG_EVSQ:
            evsq_positions.append(i)

    # Find MSeq chunks for track names
    mseq_names = _extract_mseq_names(data)

    time_sigs: list[LogicTimeSignature] = []
    key_sigs: list[LogicKeySignature] = []
    regions: list[LogicTrackRegion] = []

    for idx, pos in enumerate(evsq_positions):
        # Parse EvSq header to find event data
        # Header varies but note events follow after a ~36-40 byte header
        header_end = pos + 36  # approximate

        # Check if this is the global track (first EvSq typically)
        if idx == 0:
            _parse_global_events(data, pos, pos + 200, time_sigs, key_sigs)
            continue

        # Scan for MIDI note events (0x90 note-on followed by 0x80 note-off)
        notes = _extract_note_events(data, header_end, min(pos + 10000, len(data)))
        if notes:
            # Find associated track name
            name = mseq_names.get(idx, f"Track {idx}")
            regions.append(LogicTrackRegion(name=name, notes=notes))

    return time_sigs, key_sigs, regions


def _extract_mseq_names(data: bytes) -> dict[int, str]:
    """Extract track names from MSeq chunks."""
    names: dict[int, str] = {}
    mseq_positions = []

    for i in range(len(data) - 4):
        if data[i : i + 4] == TAG_MSEQ:
            mseq_positions.append(i)

    for idx, pos in enumerate(mseq_positions):
        # MSeq names are embedded as null-terminated strings after the header
        # Search for printable ASCII sequences after the tag
        for j in range(pos + 10, min(pos + 200, len(data))):
            # Look for a sequence of printable chars followed by null
            name_start = j
            while j < min(pos + 200, len(data)) and 32 <= data[j] < 127:
                j += 1
            if j > name_start + 1 and j < len(data) and data[j] == 0:
                name = data[name_start:j].decode("ascii")
                if name not in ("", "TRASH"):
                    names[idx] = name
                break

    return names


def _parse_global_events(
    data: bytes,
    start: int,
    end: int,
    time_sigs: list[LogicTimeSignature],
    key_sigs: list[LogicKeySignature],
) -> None:
    """Parse global track events (time sig, key sig, etc.)."""
    for j in range(start, min(end, len(data) - 16)):
        if data[j] == 0x30 and data[j + 1] == 0x00:
            # Time signature event
            pos_tick = struct.unpack_from("<I", data, j + 4)[0]
            numerator = data[j + 12]
            denom_power = data[j + 11]
            if numerator > 0 and denom_power < 8:
                denominator = 2**denom_power
                time_sigs.append(
                    LogicTimeSignature(
                        position_tick=pos_tick,
                        numerator=numerator,
                        denominator=denominator,
                    )
                )
        elif data[j] == 0x32 and data[j + 1] == 0x00:
            # Key signature event
            pos_tick = struct.unpack_from("<I", data, j + 4)[0]
            key_index = data[j + 12]
            key_sigs.append(
                LogicKeySignature(position_tick=pos_tick, key_index=key_index)
            )
        elif data[j] == 0xF1 and data[j + 1] == 0x00:
            # End marker - stop scanning
            break


def _extract_note_events(
    data: bytes, start: int, end: int
) -> list[LogicNoteEvent]:
    """Extract 64-byte MIDI note events from a data range."""
    notes: list[LogicNoteEvent] = []

    j = start
    while j + 64 <= end:
        # Look for note-on (0x90) followed by note-off (0x80) 16 bytes later
        if data[j] == 0x90 and j + 16 < end and data[j + 16] == 0x80:
            # Sub-record 1: Note On (16 bytes)
            midi_note = data[j + 1]
            position_tick = struct.unpack_from("<H", data, j + 4)[0]
            velocity = data[j + 11]

            # Sub-record 2: Note Off (16 bytes)
            duration_tick = struct.unpack_from("<H", data, j + 28)[0]

            # Sub-record 3: Onset time in microseconds (16 bytes)
            onset_us = int.from_bytes(data[j + 32 : j + 35], "little")

            # Sub-record 4: Duration in microseconds (16 bytes)
            duration_us = int.from_bytes(data[j + 48 : j + 51], "little")

            notes.append(
                LogicNoteEvent(
                    midi_note=midi_note,
                    velocity=velocity,
                    position_tick=position_tick,
                    duration_tick=duration_tick,
                    onset_us=onset_us,
                    duration_us=duration_us,
                )
            )
            j += 64  # Skip to next potential note
        else:
            j += 16  # Advance by sub-record size

    return notes
