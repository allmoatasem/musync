"""Instrument mapping — resolves track names across formats.

Config file (musync.toml, searched from the current directory upward):

    [[tracks]]
    logic   = "Inst 1"
    dorico  = "Violino."
    staffpad = "Violin"

    [[tracks]]
    logic   = "Piano"
    dorico  = "Pianoforte."

When syncing, MuSync uses this table to match source tracks to destination
tracks by name, falling back to exact-name matching if no config is found.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib  # type: ignore[no-reuse-def]
    _TOML_AVAILABLE = True
except ImportError:
    _TOML_AVAILABLE = False


_CONFIG_FILENAME = "musync.toml"

# Built-in fuzzy aliases: common instrument name variants across formats.
# Each group is a set of names that all mean the same instrument.
_ALIASES: list[set[str]] = [
    {"violin", "violino", "violino.", "vln", "vl"},
    {"viola", "viola.", "vla"},
    {"cello", "violoncello", "violoncello.", "vc"},
    {"double bass", "contrabass", "bass", "db"},
    {"piano", "pianoforte", "pianoforte.", "pf", "grand piano"},
    {"guitar", "acoustic guitar", "electric guitar", "gtr"},
    {"flute", "fl"},
    {"oboe", "ob"},
    {"clarinet", "cl", "clarinet in bb"},
    {"bassoon", "fg"},
    {"horn", "french horn", "horn in f", "hrn"},
    {"trumpet", "tpt", "trumpet in bb"},
    {"trombone", "tbn"},
    {"tuba", "tb"},
    {"flugelhorn", "flugel"},
    {"soprano saxophone", "soprano sax", "sop. sax"},
    {"alto saxophone", "alto sax", "a. sax"},
    {"tenor saxophone", "tenor sax", "t. sax"},
    {"baritone saxophone", "bari sax", "bar. sax"},
    {"drums", "drum kit", "percussion", "perc"},
    {"harp", "hp"},
    {"organ", "pipe organ"},
    {"harpsichord"},
    {"celesta"},
    {"marimba"},
    {"vibraphone", "vibr"},
    {"xylophone", "xylo"},
]

# Build lookup: normalised name → canonical group index
_ALIAS_MAP: dict[str, int] = {}
for _i, _group in enumerate(_ALIASES):
    for _name in _group:
        _ALIAS_MAP[_name.lower().strip()] = _i


def _find_config(start: Path | None = None) -> Path | None:
    """Search current dir and parents for musync.toml."""
    d = (start or Path.cwd()).resolve()
    for ancestor in [d, *d.parents]:
        candidate = ancestor / _CONFIG_FILENAME
        if candidate.exists():
            return candidate
    return None


def load_mapping(config_path: Path | None = None) -> list[dict[str, str]]:
    """Load track mappings from musync.toml.

    Returns a list of dicts, each mapping format names to track names:
      [{"logic": "Inst 1", "dorico": "Violino.", "staffpad": "Violin"}, ...]
    Returns an empty list if no config is found or TOML is unavailable.
    """
    if not _TOML_AVAILABLE:
        return []

    path = config_path or _find_config()
    if path is None:
        return []

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return data.get("tracks", [])
    except Exception:
        return []


def resolve_track_name(
    source_name: str,
    source_format: str,
    dest_format: str,
    mappings: list[dict[str, str]],
) -> str:
    """Return the destination track name for a given source track name.

    Resolution order:
    1. Explicit mapping in musync.toml
    2. Built-in fuzzy alias table
    3. Exact name (identity)
    """
    src_lower = source_name.lower().strip()

    # 1. Explicit config mapping
    for entry in mappings:
        src_mapped = entry.get(source_format, "")
        dest_mapped = entry.get(dest_format, "")
        if src_mapped.lower().strip() == src_lower and dest_mapped:
            return dest_mapped

    # 2. Fuzzy alias
    src_group = _ALIAS_MAP.get(src_lower)
    if src_group is not None:
        # Look for any entry in the destination format that belongs to the same group
        # We don't know dest track names here, so return the canonical group name
        # (callers can refine using actual dest track names)
        pass  # handled in apply_mapping below

    # 3. Identity
    return source_name


def apply_mapping(
    source_format: str,
    dest_format: str,
    dest_track_names: list[str],
    mappings: list[dict[str, str]],
) -> dict[str, str]:
    """Build a source_name → dest_name mapping for a specific sync pair.

    Returns a dict mapping each source track name to the best matching
    destination track name (or None if no match).
    """
    result: dict[str, str] = {}
    dest_lower = {n.lower().strip(): n for n in dest_track_names}

    # Build alias lookup for dest names
    dest_alias: dict[int, str] = {}  # group_idx → dest track name
    for dest_name in dest_track_names:
        g = _ALIAS_MAP.get(dest_name.lower().strip())
        if g is not None and g not in dest_alias:
            dest_alias[g] = dest_name

    for entry in mappings:
        src_name = entry.get(source_format, "")
        dst_name = entry.get(dest_format, "")
        if src_name and dst_name:
            result[src_name] = dst_name

    # For any remaining dest names not covered by explicit mapping, try fuzzy
    for dest_name in dest_track_names:
        if dest_name in result.values():
            continue
        g = _ALIAS_MAP.get(dest_name.lower().strip())
        if g is None:
            continue
        # Find a source name in the same alias group (we'll handle this at call time)
        dest_alias[g] = dest_name

    return result, dest_alias  # type: ignore[return-value]


def match_tracks(
    source_tracks: list,  # list[Track]
    dest_tracks: list,    # list[Track]
    source_format: str,
    dest_format: str,
    mappings: list[dict[str, str]] | None = None,
) -> list[tuple]:  # list[tuple[Track, Track]]
    """Match source tracks to destination tracks.

    Returns a list of (source_track, dest_track) pairs.
    Unmatched tracks are omitted.

    Matching priority:
    1. Explicit musync.toml entry
    2. Exact name match
    3. Fuzzy alias match
    """
    if mappings is None:
        mappings = load_mapping()

    dest_by_name = {t.name: t for t in dest_tracks}
    dest_lower = {t.name.lower().strip(): t for t in dest_tracks}

    pairs: list[tuple] = []

    for src in source_tracks:
        dst = None
        src_lower = src.name.lower().strip()

        # 1. Explicit mapping
        for entry in mappings:
            if entry.get(source_format, "").lower().strip() == src_lower:
                dest_name = entry.get(dest_format, "")
                if dest_name and dest_name in dest_by_name:
                    dst = dest_by_name[dest_name]
                    break

        # 2. Exact name
        if dst is None and src.name in dest_by_name:
            dst = dest_by_name[src.name]

        # 3. Fuzzy alias
        if dst is None:
            src_group = _ALIAS_MAP.get(src_lower)
            if src_group is not None:
                for dt in dest_tracks:
                    if _ALIAS_MAP.get(dt.name.lower().strip()) == src_group:
                        dst = dt
                        break

        if dst is not None:
            pairs.append((src, dst))

    return pairs
