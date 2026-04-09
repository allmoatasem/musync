# Logico

Sync musical data between **Logic Pro**, **Dorico**, and **StaffPad** projects.

Logico parses each application's proprietary file format, extracts MIDI notes, tempo, time signatures, and key signatures into a common model, and writes them back to a destination project.

## Why

If you compose across multiple tools — sketching in StaffPad, engraving in Dorico, producing in Logic Pro — keeping all three projects in sync currently requires manual re-entry. Logico bridges them by reading and writing the underlying file formats directly.

## Installation

```bash
pip install -e .
```

Requires Python 3.11+.

> **Tip (macOS):** If `logico` isn't found after installation, your shell may be inside a project-specific virtualenv. Install into the system Homebrew Python to make the command globally available:
> ```bash
> /opt/homebrew/bin/pip3 install -e . --break-system-packages
> ```

## Usage

### Read a project

Display the contents of any supported project:

```bash
logico read mysong.dorico
logico read mysong.stf
logico read MyProject.logicx        # or the parent directory
```

Output includes title, tempo, time/key signatures, tracks, and notes.

### Compare two projects

Show differences between two projects (any format combination):

```bash
logico diff mysong.dorico mysong.stf
logico diff MyProject.logicx mysong.stf
```

The diff highlights tempo/signature mismatches and per-track note differences.

### Sync from one project to another

Copy notes from a source project into a destination project:

```bash
logico sync source.stf dest.logicx
logico sync MyProject.logicx mysong.stf
```

Tracks are matched by name. Only matching tracks in the destination are updated; everything else (mixer, plugins, video, unrelated tracks) is preserved.

> **Important — destination file behavior:** sync **modifies the destination file in place** after creating a backup. The source file is never touched.
> - StaffPad: backup at `<dest>.stf.backup`
> - Logic Pro: backup at `<DestName>.backup.logicx` next to the original
> - Sources are read-only

## Supported formats

| Format          | Extension | Read | Write              | Notes |
|-----------------|-----------|------|--------------------|-------|
| Logic Pro       | `.logicx` | Yes  | Yes                | Modifies the binary `ProjectData` in place |
| StaffPad        | `.stf`    | Yes  | Yes                | SQLite-backed, fastest format to work with |
| Dorico          | `.dorico` | Yes  | Partial†           | Tempo/time-signature/key-signature only; note writing pending |

Dorico read support covers both the **legacy binary encoding** (Dorico ≤ 4.x, opcodes `0xFC/FD/FE/FF`) and the **modern encoding** (Dorico 5.x+, opcodes `0x1C/1D/1E/1F`) — auto-detected per file. Both formats round-trip byte-identically.

† Note *writing* into Dorico is still pending. The DTN binary parser/serializer is complete, and tempo/time-signature/key-signature writes are working. Note writing requires cloning the `NoteEventDefinition` entity structure from an existing note — the structure is now fully understood from a real score, but the cloning logic hasn't been wired up yet.

## Status

### Phase 1 — Read parsers (complete)

All three formats parse into a common music model:

- **Notes**: pitch, velocity, position, duration
- **Tempo events**
- **Time signatures**
- **Key signatures**
- **Tracks** with instrument names

Verified against real project files:
- `Code Noir.stf` (StaffPad, 9 parts, ~96 notes) — parses in ~0.3s
- `test.dorico` (Dorico, 1 Flugelhorn, legacy format) — parses cleanly
- `Salut d'Amour.dorico` (Dorico 5.1.81, violin + piano, 1056 notes) — full read including new opcode format
- `Project.logicx` (Logic Pro, 1 track, 3 notes) — parses cleanly
- `Salut d'Amour.logicx` (Logic Pro, MIDI from the Dorico score) — parses cleanly
- `Code Noir.logicx` (Logic Pro, 4 tracks) — parses cleanly

### Phase 2 — Writers (mostly done)

- **StaffPad writer** — Done. Round-trip verified across all 96 notes.
- **Logic Pro writer** — Done. Round-trip verified across all 3 notes.
- **Dorico writer** — Tempo/time signature/key signature writes done. The DTN binary serializer round-trips both a 2 MB Dorico 4.x file and a 1 MB Dorico 5.x file byte-identically. Note writing is the last piece — the `NoteEventDefinition` structure is now fully understood from a real score (Salut d'Amour); the cloning/insertion logic is still to be implemented.

### Future phases

- **Phase 3**: Diff & merge engine with three-way merge and conflict resolution
- **Phase 4**: File watcher daemon (`logico watch`) for automatic sync on save
- **Phase 5**: Extended musical data — dynamics, articulations, hairpins, instrument mapping

## How it works

### Architecture

```
                    ┌─────────────┐
                    │  Logico CLI  │
                    └──────┬──────┘
                           │
                ┌──────────┴──────────┐
                │   Common Music Model │   (notes, tempo, tracks, etc.)
                └──────────┬──────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
  ┌─────▼─────┐      ┌─────▼─────┐      ┌─────▼─────┐
  │   Logic   │      │  StaffPad  │      │   Dorico   │
  │  Parser   │      │   Parser   │      │   Parser   │
  │  Writer   │      │   Writer   │      │   (R/O)    │
  └───────────┘      └────────────┘      └────────────┘
```

Hub-and-spoke: every format converts to and from the common model, so any pair can sync in any direction.

### Format details (reverse-engineered)

#### StaffPad `.stf`
A SQLite database with a hierarchical object model:

```
Score → Parts → StandardStaff → Bars → Voices → Chords → Notes
```

- Object tree stored in the `score0` table (`user_actor`, `obj`, `parent_obj`, `typename`, `value`)
- Note pitch encoded in lower 32 bits of `Note.attributes` as a signed diatonic staff position (0 = middle line of staff)
- Duration encoded in `Chord.attributes` byte 1: `0x30` = half, `0x31` = dotted half, `0x40` = quarter, etc.
- Beat position stored in `BarBeat` child object as `numerator/denominator`

#### Logic Pro `.logicx`
A macOS directory bundle containing:
- `Resources/ProjectInformation.plist` — version info
- `Alternatives/000/MetaData.plist` — tempo, key, time signature
- `Alternatives/000/ProjectData` — binary file with all musical content

The `ProjectData` binary uses reversed FourCC chunks:
- Magic: `0x2347c0ab`
- Tags: `gnoS` (Song), `qeSM` (MSeq), `karT` (Trak), `qSvE` (EvSq), `OCuA` (AuCO), etc.
- PPQ = 960, with a fixed 38400-tick offset before bar 1
- MIDI notes are stored as 64-byte records (4 × 16-byte sub-records: note-on, note-off, onset microseconds, duration microseconds)
- `EvSq` chunk header has `data_size` at offset +28 (uint32 LE)

#### Dorico `.dorico`
A ZIP archive containing custom binary `.dtn` files:

- 12-byte header: version, type, key_count
- Key string table (field names)
- Value string table (all values stored as strings)
- Entity tree using LEB128 varints and four opcodes

Two opcode encodings exist (auto-detected):

| Encoding | Files | Entity | Array | Key-value | Null |
|----------|-------|--------|-------|-----------|------|
| Legacy (Dorico ≤ 4.x) | first byte ≥ `0xFC` | `0xFE` | `0xFF` | `0xFC` | `0xFD` |
| Modern (Dorico 5.x+) | first byte < `0x20` | `0x1F` | `0x1E` | `0x1C` | `0x1D` |

Musical hierarchy: `kScore → flows → blocks → events`. Notes live in `kVoiceStream` blocks.

- **Legacy format**: note pitch stored as `diatonicStep + chromaticAlteration + octave` in a nested `pitch` entity.
- **Modern format**: note pitch stored directly as a MIDI integer string in a `pitch` key-value pair. Positions are rational strings (`"57/2"` = 28.5 quarter notes).

## Project structure

```
src/logico/
├── model.py              Common music data model
├── cli.py                CLI entry point
├── dorico/
│   ├── dtn.py            DTN binary parser/serializer (legacy + modern opcodes)
│   ├── parser.py         .dorico ZIP → DtnFile
│   ├── extractor.py      DtnFile → Project (both format variants)
│   └── writer.py         Project → .dorico (tempo/time-sig/key-sig; notes pending)
├── staffpad/
│   ├── parser.py         .stf SQLite parser
│   ├── extractor.py      StfProject → Project
│   └── writer.py         Project → .stf
├── logic/
│   ├── parser.py         .logicx binary parser
│   ├── extractor.py      LogicProject → Project
│   └── writer.py         Project → .logicx
└── sync/                 (placeholder for Phase 3)
```

## Safety notes

- **Backups are automatic** but you should keep your own backups too. The reverse-engineered formats are not officially documented and edge cases exist.
- **Test on copies first.** Especially for Logic Pro, where the binary format has many fields whose meaning isn't fully understood.
- **Close the destination application** before syncing. Logic Pro and Dorico may lock or overwrite project files while open.
- **The source is never modified** — only read.

## Limitations (current)

- Dorico is read-only
- Velocity is not preserved by StaffPad (it doesn't store per-note velocity)
- Dynamics, articulations, and hairpins are not yet synced (Phase 5)
- Tempo/time signature changes mid-piece work but are minimally tested
- Instrument mapping between formats is by track name only — no automatic mapping yet
