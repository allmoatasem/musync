"""Parse a .dorico project file into a DtnFile structure.

A .dorico file is a ZIP archive containing:
  - score.dtn: Main score data (binary DTN format)
  - scorelibrary.dtn: Library definitions
  - supplementary_data/: XML config, previews, audio engine data
  - META-INF/container.xml: ODF-like manifest
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

from .dtn import DtnFile, parse_dtn


@dataclass
class DoricoProject:
    """A parsed .dorico project."""

    score: DtnFile
    path: str

    # Raw ZIP contents for round-trip preservation
    _zip_entries: dict[str, bytes] | None = None


def parse_dorico(path: str) -> DoricoProject:
    """Parse a .dorico file and return its score data."""
    with zipfile.ZipFile(path, "r") as zf:
        # Preserve all ZIP entries for later round-trip writing
        zip_entries = {}
        for name in zf.namelist():
            zip_entries[name] = zf.read(name)

        score_data = zip_entries.get("score.dtn")
        if score_data is None:
            raise ValueError(f"No score.dtn found in {path}")

        score = parse_dtn(score_data)

    project = DoricoProject(score=score, path=path)
    project._zip_entries = zip_entries
    return project
