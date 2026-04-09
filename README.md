# MuSync

Sync musical data between **Logic Pro**, **Dorico**, and **StaffPad** projects.

MuSync parses each application's proprietary file format, extracts MIDI notes, tempo, time signatures, and key signatures into a common model, and writes them back to a destination project.

## Why

If you compose across multiple tools — sketching in StaffPad, engraving in Dorico, producing in Logic Pro — keeping all three projects in sync currently requires manual re-entry. MuSync bridges them by reading and writing the underlying file formats directly.

## Getting the app

**Musicians:** download the installer from the Releases page — no Python or terminal required.
- macOS: `MuSync-x.x.x.dmg`
- Windows: `MuSync-Setup-x.x.x.exe`

The app is fully self-contained. The Python engine is bundled inside.

## Developer installation (CLI)

```bash
pip install -e .
```

Requires Python 3.11+.

> **Tip (macOS):** If `musync` isn't found after installation, your shell may be inside a project-specific virtualenv. Install into the system Homebrew Python to make the command globally available:
> ```bash
> /opt/homebrew/bin/pip3 install -e . --break-system-packages
> ```

## Building the desktop app

```bash
# 1. Bundle Python into a self-contained binary (macOS)
./scripts/build-backend.sh         # → app/resources/musync-server/

# 1. Bundle Python (Windows, in PowerShell)
.\scripts\build-backend.ps1

# 2. Build the Electron installer
cd app
npm install
npm run dist:mac    # → app/release/MuSync-x.x.x.dmg
npm run dist:win    # → app/release/MuSync-Setup-x.x.x.exe

# Or do everything at once on macOS:
./scripts/build-all.sh
```

### Development mode (hot reload)

```bash
pip install -e .          # install musync CLI
cd app && npm install
npm run dev               # starts Vite + Electron concurrently
```

## Usage

### Read a project

Display the contents of any supported project:

```bash
musync read mysong.dorico
musync read mysong.stf
musync read MyProject.logicx        # or the parent directory
```

Output includes title, tempo, time/key signatures, tracks, and notes.

### Compare two projects

Show differences between two projects (any format combination):

```bash
musync diff mysong.dorico mysong.stf
musync diff MyProject.logicx mysong.stf
musync diff mysong.dorico @2          # current state vs snapshot 2
musync diff mysong.dorico @1 @3       # snapshot 1 vs snapshot 3
```

The diff highlights tempo/signature mismatches and per-track note differences (added, removed, changed).

### Sync from one project to another

Copy notes from a source project into a destination project:

```bash
musync sync source.stf dest.logicx
musync sync MyProject.logicx mysong.stf
```

Tracks are matched by name, then by fuzzy instrument alias (e.g. `"Violino."` ↔ `"violin"`), then by explicit mapping in `musync.toml`. Every successful sync automatically saves a snapshot of the destination.

> **Important — destination file behavior:** sync **modifies the destination file in place** after creating a backup. The source file is never touched.
> - StaffPad: backup at `<dest>.stf.backup`
> - Logic Pro: backup at `<DestName>.backup.logicx` next to the original
> - Sources are read-only

### Version history

Every sync saves a snapshot automatically. You can also inspect and restore history manually:

```bash
musync log mysong.dorico              # list all snapshots with timestamps and change summaries
musync diff mysong.dorico @2          # diff current state vs snapshot 2
musync diff mysong.dorico @1 @3       # diff snapshot 1 vs snapshot 3
musync revert mysong.dorico @3        # restore snapshot 3 (saves current state first)
```

Snapshots are stored in `.musync/<filename>/` next to the project file as plain JSON — human-readable and easy to back up.

### Auto-sync on save

```bash
musync watch source.dorico dest.logicx
```

Monitors the source file and syncs to the destination automatically whenever you save. Uses a 1-second debounce to handle rapid saves and ignores its own writes to prevent sync loops.

### Instrument mapping

Create a `musync.toml` in your project directory to map track names across formats:

```toml
[[tracks]]
logic    = "Inst 1"
dorico   = "Violino."
staffpad = "Violin"

[[tracks]]
logic    = "Piano"
dorico   = "Pianoforte."
```

Common instrument name variants (e.g. `violin` / `Violino.` / `vln`) are resolved automatically without any config.

## Supported formats

| Format          | Extension | Read | Write              | Notes |
|-----------------|-----------|------|--------------------|-------|
| Logic Pro       | `.logicx` | Yes  | Yes                | Modifies the binary `ProjectData` in place |
| StaffPad        | `.stf`    | Yes  | Yes                | SQLite-backed, fastest format to work with |
| Dorico          | `.dorico` | Yes  | Yes                | Clones existing `NoteEventDefinition` as template; both legacy and modern encodings |

Dorico read support covers both the **legacy binary encoding** (Dorico ≤ 4.x, opcodes `0xFC/FD/FE/FF`) and the **modern encoding** (Dorico 5.x+, opcodes `0x1C/1D/1E/1F`) — auto-detected per file. Both formats round-trip byte-identically.

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

### Phase 2 — Writers (complete)

- **StaffPad writer** — Done. Round-trip verified across all 96 notes.
- **Logic Pro writer** — Done. Round-trip verified across all 3 notes.
- **Dorico writer** — Done. Note writing implemented via template cloning. Round-trip verified: 1056-note Salut d'Amour round-trip = 0 mismatches. Both modern (Dorico 5+, direct MIDI pitch KV) and legacy (Dorico ≤4, diatonic entity) pitch encodings supported.

### Phase 3 — Version history & diff/merge engine (complete)

Every sync creates a commit in a `.musync/` directory alongside your projects — a lightweight version history for your musical data, independent of what Logic/Dorico/StaffPad do internally.

```bash
musync log mysong.dorico           # show all past versions
musync diff mysong.dorico @2       # diff current vs version 2
musync revert mysong.dorico @3     # restore version 3 (backs up current first)
```

- **Snapshots** — each sync saves a canonical JSON snapshot of the Project model (`notes, tempo, time sig, key sig`) with a timestamp and hash
- **Diff** — compare any two snapshots or a snapshot against the current file: added/removed/changed notes, tempo or signature changes
- **Three-way merge** — when both source and destination changed since the last sync, merge changes automatically; flag conflicts (same note position, different pitch) for manual resolution
- **Revert** — restore any past snapshot back into the project file, with an automatic backup of the current state

### Phase 4 — File watcher daemon (complete)

```bash
musync watch source.dorico dest.logicx   # auto-sync on every save
```

Uses `watchdog` (already a dependency) to monitor both files. On save, diffs against the last snapshot and applies only the changed notes — not a full rewrite. Debounces rapid saves and detects its own writes to prevent sync loops.

### Phase 5 — Extended musical data (partial)

- **Dynamics** — sync `pp/p/mp/mf/f/ff` markings and hairpins (crescendo/diminuendo); map Dorico dynamic entities ↔ Logic MIDI CC1/CC11
- **Articulations** — staccato, accent, tenuto, marcato; map Dorico articulation IDs ↔ Logic note flags
- **Instrument mapping** — `musync.toml` config for matching tracks across formats by instrument family, not just exact name; fuzzy matching and alias tables

### Phase 6 — Cross-platform desktop UI (complete)

A native-feeling Electron + React app targeting macOS and Windows. No Python knowledge required — the Python engine is bundled inside the installer via PyInstaller.

**Architecture:** Electron shell spawns a local FastAPI server (`musync serve`) on startup and communicates with it over localhost. All format logic stays in Python; the UI is pure React.

**Views:**
- **Sync** — pick source + destination, click Sync Now, see the result with note count and snapshot number
- **History** — browse all past snapshots for any project file, diff any snapshot against the current state, restore with one click
- **Watch** — toggle auto-sync on save; status indicator shows live watch state

**Bundling:**
- `scripts/build-backend.sh` (macOS) / `scripts/build-backend.ps1` (Windows) — PyInstaller bundles the Python runtime + all dependencies into `app/resources/musync-server/`
- `electron-builder` packages the Electron app + the Python binary into a signed DMG (macOS) or NSIS installer (Windows)
- In dev mode, Electron spawns `python -m musync serve` against the local source tree so no build step is needed

## How it works

### Architecture

```
                    ┌─────────────┐
                    │  MuSync CLI  │
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
musync/                           ← monorepo root
├── src/musync/                   ← Python backend
│   ├── model.py                  Common music data model
│   ├── cli.py                    CLI (read, diff, sync, log, revert, watch, serve)
│   ├── server.py                 FastAPI server (used by the desktop app)
│   ├── mapping.py                Instrument/track name mapping + fuzzy alias table
│   ├── watcher.py                File watcher daemon
│   ├── dorico/
│   │   ├── dtn.py                DTN binary parser/serializer (legacy + modern opcodes)
│   │   ├── parser.py             .dorico ZIP → DtnFile
│   │   ├── extractor.py          DtnFile → Project (both format variants)
│   │   └── writer.py             Project → .dorico (full read+write)
│   ├── staffpad/
│   │   ├── parser.py, extractor.py, writer.py
│   ├── logic/
│   │   ├── parser.py, extractor.py, writer.py
│   └── sync/
│       ├── snapshot.py           JSON snapshot save/load
│       └── diff.py               Note/tempo/sig diff engine
├── app/                          ← Electron + React frontend
│   ├── electron/
│   │   ├── main.js               Main process (spawns Python, file dialogs)
│   │   └── preload.js            Context bridge
│   ├── src/
│   │   ├── App.tsx               Root component + nav
│   │   ├── api.ts                HTTP client for the Python server
│   │   └── components/
│   │       ├── SyncView.tsx      Sync panel
│   │       ├── HistoryView.tsx   Snapshot browser + diff
│   │       └── WatchView.tsx     Watch mode toggle
│   ├── package.json              Electron + Vite + electron-builder
│   └── resources/                PyInstaller output lands here (git-ignored)
├── scripts/
│   ├── build-backend.sh          PyInstaller (macOS)
│   ├── build-backend.ps1         PyInstaller (Windows)
│   └── build-all.sh              Full build (macOS)
└── pyproject.toml                Python package (fastapi, uvicorn, watchdog, …)
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
