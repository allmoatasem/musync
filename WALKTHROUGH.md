# Logico — Educational Walkthrough

A guided tour through the Logico codebase for someone learning Python. We'll start from the file you'd open first if you cloned this repo, then drill down into the techniques that make each part of the project work.

If you've never written Python before, you'll still get something out of this — the focus is on *why* the code is shaped the way it is, not just *what* it does.

---

## Table of contents

1. [The big picture](#1-the-big-picture)
2. [Project layout: what each folder does](#2-project-layout-what-each-folder-does)
3. [Concept: a "common model" and why we need one](#3-concept-a-common-model-and-why-we-need-one)
4. [Python tools you'll see throughout](#4-python-tools-youll-see-throughout)
5. [Walkthrough: the StaffPad parser (start here — easiest)](#5-walkthrough-the-staffpad-parser-start-here--easiest)
6. [Walkthrough: the Logic Pro parser (raw bytes)](#6-walkthrough-the-logic-pro-parser-raw-bytes)
7. [Walkthrough: the Dorico parser (custom binary format)](#7-walkthrough-the-dorico-parser-custom-binary-format)
8. [Walkthrough: the writers (modifying files in place)](#8-walkthrough-the-writers-modifying-files-in-place)
9. [Walkthrough: the CLI](#9-walkthrough-the-cli)
10. [Reverse engineering: how do you even start?](#10-reverse-engineering-how-do-you-even-start)
11. [Common Python patterns to remember](#11-common-python-patterns-to-remember)

---

## 1. The big picture

The problem: three music applications (Logic Pro, Dorico, StaffPad) each store the same musical information in completely different file formats. None of them is documented. We want to read notes from any of them and write notes into any of them.

The shape of the solution looks like this:

```
   read                       write
   ────                       ─────
.dorico ──┐               ┌── .dorico
.stf    ──┼─→ Project ──→─┼── .stf
.logicx ──┘               └── .logicx
```

`Project` is our **common model** — a Python class that holds notes, tempo, time signatures, etc., in a format we control. Every parser converts *into* this shape; every writer converts *out of* it. We never compare a `.dorico` directly to a `.logicx` — they always meet in the middle.

This is called a **hub-and-spoke architecture**, and it's how almost every multi-format converter is built (Pandoc for documents, FFmpeg for video, etc.).

---

## 2. Project layout: what each folder does

```
src/logico/
├── model.py              ← The "common model" — shared data classes
├── cli.py                ← Command-line interface (logico read / diff / sync)
├── dorico/
│   ├── dtn.py            ← Low-level binary parser/serializer for Dorico's .dtn format
│   ├── parser.py         ← Opens a .dorico ZIP and hands the .dtn to dtn.py
│   ├── extractor.py      ← Walks the parsed .dtn tree to fill a Project
│   └── writer.py         ← Modifies a .dorico file (still partial)
├── logic/
│   ├── parser.py         ← Reads Logic Pro's binary ProjectData and MetaData.plist
│   ├── extractor.py      ← Converts parsed Logic data → Project
│   └── writer.py         ← Splices new note records back into ProjectData
└── staffpad/
    ├── parser.py         ← Queries StaffPad's SQLite database
    ├── extractor.py      ← Converts queried data → Project
    └── writer.py         ← Inserts/updates rows in the SQLite database
```

You'll notice each format has the same three files: **parser**, **extractor**, **writer**. That repetition is intentional — once you understand how one format is structured, the others click immediately.

- **parser**: knows the *physical* format (ZIP, SQLite, binary chunks). Returns a format-specific data structure.
- **extractor**: knows nothing about files. Walks the format-specific structure and produces a `Project`. Pure data transformation.
- **writer**: takes a `Project` and a destination file, and modifies the file.

This **separation of concerns** means we can change *how* we parse a format without touching *how* we extract data from it, and vice versa. It's the same idea as keeping HTML, CSS, and JavaScript in separate files.

---

## 3. Concept: a "common model" and why we need one

Open [src/logico/model.py](src/logico/model.py). The whole file is essentially a list of `@dataclass` definitions:

```python
@dataclass
class Note:
    pitch: int          # MIDI note number 0-127
    velocity: int       # 0-127
    position: int       # ticks from start of flow/region
    duration: int       # ticks
    channel: int = 0
```

A **dataclass** is a Python feature (added in 3.7) that turns a class into a "data bag" with no boilerplate. Without it you'd write:

```python
class Note:
    def __init__(self, pitch, velocity, position, duration, channel=0):
        self.pitch = pitch
        self.velocity = velocity
        self.position = position
        self.duration = duration
        self.channel = channel

    def __repr__(self):
        return f"Note(pitch={self.pitch}, ...)"

    def __eq__(self, other):
        return (self.pitch == other.pitch and ...)
```

The `@dataclass` decorator generates all of that for you. `@` is Python's syntax for **decorators** — functions that wrap or modify other functions/classes. You'll see them all over Python code.

### Why we use a common model

Imagine if we *didn't*. Then every conversion would be its own special case:

```
dorico_to_logic()      stf_to_dorico()
dorico_to_stf()        stf_to_logic()
logic_to_dorico()      logic_to_stf()
```

Six functions, all duplicating logic. Add a fourth format and you have twelve. With the common model:

```
parse_dorico() → extract_dorico() → Project
                                       ↓
                                    write_logic()
```

Each format only needs an extractor (Project ← format) and a writer (format ← Project). Adding a new format is a constant amount of work, not a multiplying amount.

### Why MIDI numbers?

The `Note.pitch` field is an integer 0–127. That's the MIDI standard, where 60 = middle C. We chose it as the common representation because:

1. Logic Pro already uses it.
2. It has a single canonical encoding (no ambiguity about C# vs Db).
3. It's tiny and trivial to compare.

But Dorico and StaffPad don't store MIDI numbers — they store *diatonic* pitch (the letter name + accidental + octave). So our extractors have to convert. The reverse functions live in `model.py`:

```python
def diatonic_to_midi(step, alteration, octave): ...
def midi_to_diatonic(midi_note, fifths=0): ...
```

These functions know things like "C is step 0, D is step 1, ... B is step 6" and "C in octave 4 is MIDI 60". The math is in the source if you're curious — it's surprisingly clean once you write it out.

---

## 4. Python tools you'll see throughout

A vocabulary list before we read any real code:

| Tool | What it does | Example in this codebase |
|---|---|---|
| `from __future__ import annotations` | Lets us use future Python type-hint syntax in older versions | Top of every file |
| `dataclass` | Auto-generates `__init__`, `__repr__`, `__eq__` for data-holding classes | `model.py`, all parsers |
| `Pathlib` | Object-oriented file paths instead of string manipulation | `Path(path).resolve()` |
| `struct` | Read/write fixed-layout binary data (4-byte integer, etc.) | Logic Pro & Dorico parsers |
| `sqlite3` | Built-in SQLite database driver | StaffPad parser |
| `zipfile` | Read/write ZIP archives | Dorico parser |
| `plistlib` | Read/write Apple's plist files (XML or binary) | Logic Pro parser |
| `fractions.Fraction` | Exact rational arithmetic (no floating-point rounding) | Dorico extractor — position strings like `'57/2'` |
| Type hints | Annotations like `def f(x: int) -> str:` for documentation + tools | Everywhere |
| `Optional` / `\| None` | "This may be None" — Python's null type | Many places |
| List/dict comprehensions | `[x*2 for x in xs]` — concise data transforms | All the extractors |
| `with` statements | Auto-cleanup for files/connections | File reads, DB connections |
| f-strings | `f"Hello, {name}"` — string interpolation | All print statements |

If you don't recognize one of these, look it up before continuing. They appear constantly.

---

## 5. Walkthrough: the StaffPad parser (start here — easiest)

StaffPad stores its projects as **SQLite databases**. SQLite is a full SQL database that lives entirely inside a single file. Python has a built-in driver, so reading a `.stf` file is just SQL queries.

Open [src/logico/staffpad/parser.py](src/logico/staffpad/parser.py).

### Step 1: Open the database

```python
import sqlite3

def parse_staffpad(path: str) -> StfProject:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row     # ← lets us access columns by name
    try:
        metadata = _parse_metadata(conn)
        time_sigs = _parse_time_signatures(conn)
        ...
    finally:
        conn.close()
```

The `try / finally` ensures the database connection is always closed, even if something goes wrong. This is one of the most important patterns in Python — anything you open, you must close. (You can also use `with` statements for the same effect, which we do elsewhere.)

`row_factory = sqlite3.Row` is a small quality-of-life upgrade. By default SQLite gives you rows as tuples, so you'd write `row[0]`, `row[1]`. With `Row` you can write `row["title"]`, `row["composer"]` instead — much more readable.

### Step 2: Run a query

```python
for row in conn.execute("SELECT key, value FROM metadata"):
    k, v = row["key"], row["value"]
    if k == "title":
        meta.title = v or ""
    elif k == "composer":
        meta.composer = v or ""
```

`conn.execute(sql)` returns a **cursor** that you can iterate over with `for`. Each iteration gives you one row. This is **lazy iteration** — SQLite doesn't load everything into memory; it streams rows as you ask for them.

The `or ""` trick is a Python idiom: `v or ""` returns `v` if it's truthy, otherwise `""`. Useful when a database column might be `None` and you want an empty string.

### Step 3: Walk a hierarchy

The interesting part of StaffPad is that it stores a **tree** in a flat table. Every row has a `parent_obj` field, so you can find children by querying:

```sql
SELECT * FROM score0 WHERE parent_obj = 4097
```

To navigate the tree (Score → Part → Staff → Bar → Voice → Chord → Note), we walk it step by step:

```python
def _find_child_obj(conn, parent_obj, ua, collection_name, child_type):
    """Find a child object through a collection."""
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
```

The `?` placeholders are how you safely pass values to SQL — never use string formatting (`%s` or f-strings) for SQL inputs! That's the classic SQL injection vulnerability. Parameterized queries fix it for free.

### Performance lesson learned

When I first wrote this parser, I tried to do everything in one giant query with 8 JOINs. It worked on tiny files but took *minutes* on a 300 MB file. The fix was to break it into smaller queries that work step-by-step (like `_find_child_obj` above). The whole parser now runs in 0.3 seconds.

Lesson: **smaller queries are often faster than big ones**, especially when SQLite's query planner can't figure out a good plan for a complex chain of joins.

---

## 6. Walkthrough: the Logic Pro parser (raw bytes)

Open [src/logico/logic/parser.py](src/logico/logic/parser.py). This is harder — we're reading **raw bytes** from a binary file, with no schema, no documentation, no SQL.

### Step 1: Read the file

```python
with open(project_data_path, "rb") as f:
    data = f.read()
```

`"rb"` means **read binary** — we get a `bytes` object back, which is like a string but holds raw byte values 0–255 instead of characters. The `with` block auto-closes the file when we leave it.

### Step 2: Verify the magic number

```python
if data[:4] != MAGIC:  # MAGIC = b"\x23\x47\xc0\xab"
    raise ValueError(f"Invalid ProjectData magic: {data[:4].hex()}")
```

Most binary file formats start with a few "magic" bytes that identify them. PNG files start with `89 50 4E 47`, ZIP files with `50 4B`, etc. Logic Pro's ProjectData starts with `23 47 C0 AB`. If those four bytes aren't there, we're not looking at a Logic Pro project, so we bail out early.

`data[:4]` is **slicing** — get bytes 0 through 3. Slicing works on strings, lists, tuples, and bytes objects.

### Step 3: Find the chunks we care about

```python
evsq_positions = []
for i in range(len(data) - 4):
    if data[i:i+4] == TAG_EVSQ:  # b"qSvE"
        evsq_positions.append(i)
```

Logic's binary file is structured as a stream of "chunks". Each chunk is identified by a 4-byte tag at the start. We find all the EvSq (event sequence) chunks by scanning every position in the file looking for that tag.

The fun thing about Logic's tags is they're stored *backwards*. The chunk for "Song" appears as `gnoS`, "MSeq" as `qeSM`, "EvSq" as `qSvE`. This is because Logic was originally written for big-endian Macs, and the byte order got flipped when they ported to Intel.

### Step 4: Decode a binary record

Once we find an EvSq chunk that contains MIDI notes, each note is a fixed 64-byte record:

```python
import struct

# Sub-record 1: Note On (16 bytes)
record[0] = 0x90                                    # MIDI status: Note On
record[1] = note.pitch & 0x7F                       # MIDI note number (0-127)
struct.pack_into("<H", record, 4, abs_tick & 0xFFFF)  # tick position
record[11] = note.velocity & 0x7F                   # velocity (0-127)
```

`struct.pack_into("<H", buffer, offset, value)` writes a 2-byte little-endian unsigned integer (`H` = unsigned short, `<` = little-endian) into `buffer` at `offset`. The `struct` module is Python's swiss-army knife for binary data — once you learn the format codes (`b`, `B`, `h`, `H`, `i`, `I`, `f`, `d`), you can read or write any C-style binary structure.

### Bitwise operations

The `& 0x7F` is **bitwise AND**. MIDI note numbers and velocities are limited to 7 bits (0–127), so we mask off bit 7 just in case. `0x7F` is hexadecimal for `01111111` in binary. Bitwise operators (`&`, `|`, `^`, `~`, `<<`, `>>`) come up constantly when working with binary protocols.

---

## 7. Walkthrough: the Dorico parser (custom binary format)

This is the deepest rabbit hole. Open [src/logico/dorico/dtn.py](src/logico/dorico/dtn.py).

A Dorico `.dorico` file is a ZIP archive. Inside the ZIP is a file called `score.dtn` that uses a **completely custom binary format** invented by Dorico's developers. There's no documentation. We had to figure it out from scratch by staring at hex dumps for hours.

### Step 1: Unzip

```python
import zipfile

with zipfile.ZipFile(path, "r") as zf:
    score_data = zf.read("score.dtn")
```

That's it for the outer container. The hard part is *inside* `score.dtn`.

### Step 2: Parse the file header

```python
import struct

version, file_type, key_count = struct.unpack_from("<III", data, 0)
```

`struct.unpack_from("<III", data, offset)` reads three little-endian unsigned 32-bit integers (`<III`) starting at `offset` in `data`. This is the inverse of `pack_into`.

`<` = little-endian byte order
`I` = unsigned 32-bit integer
`III` = three of them

The result is a tuple, which we **unpack** into three variables on the left.

### Step 3: The string tables

Dorico's format is clever: instead of repeating field names like `"position"` thousands of times throughout the file, it has a **key string table** at the start that lists every field name once. Then later, when an event mentions `"position"`, it just stores the index into the key table (e.g., `key 3061`).

```python
keys: list[str] = []
for _ in range(key_count):
    end = data.index(0, pos)        # find next null byte
    keys.append(data[pos:end].decode("utf-8"))
    pos = end + 1
```

Strings are stored null-terminated (a `\0` byte marks the end), C-style. `data.index(0, pos)` finds the next zero byte starting from `pos`. We slice the bytes between `pos` and that null, decode them as UTF-8, and add to our list.

### Step 4: The entity tree (the hard part)

After the string tables comes a binary tree. Each node starts with one of four single-byte **opcodes**:

| Byte | Meaning |
|------|---------|
| `0xFE` | Start of an entity (named object with children) |
| `0xFF` | Same as 0xFE but represents an array |
| `0xFC` | Key-value pair (a leaf) |
| `0xFD` | Null/empty placeholder |

After the opcode comes one or more **varints** — a compact way to encode integers where each byte uses 7 bits for the value and 1 bit to say "more bytes follow":

```python
def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = data[pos]
        result |= (b & 0x7F) << shift   # take 7 bits, shift them into place
        pos += 1
        if (b & 0x80) == 0:             # if top bit is clear, we're done
            break
        shift += 7
    return result, pos
```

This is the same encoding Google's Protobuf uses. The number `300` would be `0xAC 0x02` (binary `10101100 00000010` → strip the top bits → `0101100 0000010` → reorder → `00000010 0101100` → 300).

### Step 5: Recursion!

Entities can contain other entities, which can contain more entities, etc. The natural way to parse a tree like this is with **recursion** — a function that calls itself:

```python
def _parse_entity(data, pos):
    pos += 1  # skip opcode
    key_idx, pos = read_varint(data, pos)
    flags, pos = read_varint(data, pos)
    num_children, pos = read_varint(data, pos)

    # Skip child key list
    for _ in range(num_children):
        _, pos = read_varint(data, pos)

    # Parse children — some of which may be entities (recurse!)
    children, pos = _parse_children(data, pos, num_children)

    return DtnEntity(...), pos
```

`_parse_children` calls `_parse_entity` for each child entity. `_parse_entity` calls `_parse_children` to get its own children. They reference each other — that's **mutual recursion**. The score.dtn file has entity trees nested 30+ levels deep, so we have to bump Python's recursion limit:

```python
sys.setrecursionlimit(10000)
```

By default Python only allows 1000 levels of function nesting to prevent stack overflows. For deeply nested data, you raise the limit (or convert to an iterative approach with an explicit stack).

### Step 6: Discovering a format revision in the wild

One of the most instructive reverse-engineering moments in this project came from trying to read a real Dorico 5 project file (Salut d'Amour, Op. 12).

The parser crashed immediately:

```
ValueError: Expected FE opcode at tree start (0x53d72)
```

The byte at the expected position was `0x1F`, not `0xFE`. Our whole entity-tree parser assumed `0xFE` = entity start. Something had changed.

**How we investigated:**

1. **Compared the headers.** Both files were `version=4`. The version field didn't distinguish them.

2. **Printed the raw bytes.** The first byte after the value table was `0x1F` in the new file vs `0xFE` in the old one. `0x1F` is 31; `0xFE` is 254. The difference is exactly `0xE0 = 224`.

3. **Checked all four opcodes.** `0xFC` (252) → `0x1C` (28). `0xFD` (253) → `0x1D` (29). `0xFE` (254) → `0x1E` (30). `0xFF` (255) → `0x1F` (31). Every opcode shifted by exactly −224. Dorico changed its encoding systematically.

4. **Verified by parsing.** We wrote a quick test parser using `0x1C/1D/1E/1F` and it immediately produced:
   ```
   ARR 'kScore' flags=0 n=15
     KV 'fileVersion'='1.1301'
     KV 'title'="Salut d'Amour..."
   ```
   The data made sense, confirming the theory.

**The fix in code:**

After parsing the key and value tables, we peek at the first byte and branch:

```python
first_byte = data[pos]
if first_byte in (OP_ENTITY, OP_ARRAY):         # 0xFE or 0xFF
    uses_new = False
    op_entity, op_array, op_kv, op_null = OP_ENTITY, OP_ARRAY, OP_KV, OP_NULL
elif first_byte in (OP_ENTITY_V2, OP_ARRAY_V2): # 0x1F or 0x1E
    uses_new = True
    op_entity, op_array, op_kv, op_null = OP_ENTITY_V2, OP_ARRAY_V2, OP_KV_V2, OP_NULL_V2
```

We pass the four opcode values through every recursive parsing function. The serializer uses whichever set the parser detected. The result: both the old `test.dorico` and the new Salut d'Amour file round-trip byte-identically.

**New format, new pitch encoding.** The Dorico 5 format also changed how note pitch is stored. The legacy format used a nested entity with three fields (`diatonicStep`, `chromaticAlteration`, `octave`) that together encode a pitch in music-theory terms. The modern format just stores a MIDI integer directly:

```python
# Legacy: pitch entity with three KV children
pitch_entity = event.get_entity("pitch", k)
step, alteration, octave = ...
midi_pitch = diatonic_to_midi(step, alteration, octave)

# Modern: pitch is a single KV on the event itself
midi_pitch = int(kvs["pitch"])   # e.g., "80" → Ab5
```

Positions also changed: from integer ticks to rational strings in quarter notes (`"57/2"` = 28.5 quarter notes). Python's `fractions.Fraction` handles this cleanly:

```python
from fractions import Fraction

def _parse_position(pos_str: str, ppq: int) -> int:
    return int(Fraction(pos_str) * ppq)
# "57/2" * 960 = 27360 ticks
```

`Fraction` does exact arithmetic — `Fraction("57/2") * 960` gives exactly `27360`, with no floating-point rounding.

**The lesson:** formats evolve. A robust parser detects the variant rather than hard-coding assumptions. The version number in the file header is often *not* enough — you have to check the actual bytes.

### Step 7: The serializer is just the inverse

The cool thing about a clean parser is that the serializer becomes obvious — just do everything in reverse:

```python
def _serialize_entity(entity, out):
    out.append(OP_ARRAY if entity.is_array else OP_ENTITY)
    out.extend(write_varint(entity.key_idx))
    out.extend(write_varint(entity.flags))
    out.extend(write_varint(len(entity.children)))
    # ... write child key list, then children recursively
```

We test by parsing real files and re-serializing them — the output is byte-identical to the input. That's our proof that we understood the format completely. Both the legacy `test.dorico` and the modern Salut d'Amour file pass this test.

---

## 8. Walkthrough: the writers (modifying files in place)

Each writer follows a slightly different strategy depending on the format.

### StaffPad: SQL INSERT/UPDATE/DELETE

Easiest by far. We open the same SQLite file, find the rows we want to change, and run SQL:

```python
conn.execute(
    "INSERT INTO score0 VALUES (?, ?, ?, ?, ?, ?)",
    (ua, chord_obj, ua, de_arr_obj, tn["Chord_duration_elements"], _random_blob(7)),
)
```

The challenge isn't the SQL — it's understanding *what to insert*. StaffPad's chord-and-note data is spread across ~12 different rows per chord (the chord itself, its attributes, its bar position, its notes collection, each note inside, each note's attributes). We have to insert all of them in the right order with the right parent references.

### Logic Pro: byte-level splice

Logic stores notes in a chunk of fixed-size 64-byte records. To replace them:

```python
new_data = data[:evsq_start] + bytes(new_header) + bytes(note_data) + data[evsq_end:]
```

That `+` operator concatenates `bytes` objects (just like with strings). We slice the original file, drop in the new chunk, and slice the rest of the file unchanged. Then we update the chunk's `data_size` field so Logic knows how big the replacement is.

### Dorico: parse, modify, serialize

For Dorico we go the long way: parse the entire file into a tree, walk the tree to modify what we want, then serialize the tree back to bytes:

```python
dtn = parse_dtn(score_data)
_apply_project_to_dtn(dtn, project)        # walk + mutate
new_score = serialize_dtn(dtn)
```

This works because we built a clean parser/serializer pair. The "walk + mutate" function uses helpers like `_set_kv` that find a key-value child by name and update it (or add a new one if it doesn't exist).

### Always back up first

Every writer creates a `.backup` copy before touching anything:

```python
import shutil

if backup:
    backup_path = stf_path + ".backup"
    shutil.copy2(stf_path, backup_path)
```

`shutil.copy2` copies a file *and* preserves its metadata (modification time, permissions). It's the right tool when you want a "true" copy.

For Logic Pro it's `shutil.copytree` because `.logicx` is actually a directory:

```python
shutil.copytree(logicx_path, backup_dir)
```

---

## 9. Walkthrough: the CLI

Open [src/logico/cli.py](src/logico/cli.py). This is the user-facing entry point.

```python
def main() -> None:
    args = sys.argv[1:]    # skip the program name itself

    if not args or args[0] in ("-h", "--help", "help"):
        print("Logico — ...")
        sys.exit(0)

    command = args[0]
    rest = args[1:]

    if command == "read":
        cmd_read(rest)
    elif command == "diff":
        cmd_diff(rest)
    elif command == "sync":
        cmd_sync(rest)
```

This is the simplest possible argument parser — just a chain of `if` statements over `sys.argv`. For something this small, that's totally fine. For anything more complex you'd reach for `argparse` (in the standard library) or `click` (a third-party library that we already depend on but haven't fully wired up).

The CLI is registered as a system command via `pyproject.toml`:

```toml
[project.scripts]
logico = "logico.cli:main"
```

This tells `pip` that when the package is installed, it should create a command-line tool called `logico` that runs the `main` function in `logico.cli`. After `pip install -e .`, you can type `logico` from any terminal.

### Auto-detecting formats

```python
def _detect_format(path: str) -> str:
    p = Path(path)
    if p.suffix == ".dorico":
        return "dorico"
    if p.suffix == ".stf":
        return "staffpad"
    if p.suffix == ".logicx":
        return "logic"
    if p.is_dir():
        if list(p.glob("*.logicx")):
            return "logic"
    raise ValueError(f"Unknown format for: {path}")
```

`Path` from `pathlib` is the modern way to handle file paths in Python. `p.suffix` gives the extension, `p.is_dir()` checks if it's a folder, `p.glob("*.logicx")` finds matching children. All cleaner than the old `os.path.*` functions.

### Lazy imports

Notice that the parsers/writers are imported *inside* the functions that use them, not at the top of the file:

```python
def _load_project(path):
    fmt = _detect_format(path)
    if fmt == "dorico":
        from .dorico.parser import parse_dorico
        from .dorico.extractor import extract_project
        return extract_project(parse_dorico(path))
```

This is a deliberate choice. If you only ever use `logico read mysong.stf`, you don't need to load the Dorico or Logic parsers at all. Lazy imports keep startup time fast and let the CLI work even if one parser has a bug — you only crash on the format you actually use.

---

## 10. Reverse engineering: how do you even start?

This is the part of the project that doesn't fit in any "Python tutorial" — it's more about how to investigate an unknown system.

### Step 1: Look at the file with `file` and `xxd`

```bash
file mysong.stf
# → SQLite 3.x database

file Project.logicx/Alternatives/000/ProjectData
# → data         (no clue)

xxd ProjectData | head
# 00000000: 2347 c0ab cf09 0300 0400 0000 0100 0800
# 00000010: f8b2 0200 0000 0000 676e 6f53 0600 ffff
```

`file` recognizes hundreds of formats by their magic numbers. SQLite was instantly identified — that told us we could just open it with the `sqlite3` command-line tool. Logic Pro's binary was opaque, so we used `xxd` to dump its bytes in hex.

### Step 2: Look for patterns

Reading hex dumps is mostly about pattern recognition:

- **ASCII text**: bytes in the range `0x20`–`0x7E` are printable. Long runs of them mean strings.
- **Repeating structures**: if you see the same byte pattern at regular intervals, that's probably a record.
- **Magic numbers**: short distinctive byte sequences at the start usually identify the format.
- **Counts before data**: many formats use a 4-byte length followed by that many bytes of data.

In Logic's ProjectData, we noticed `qSvE` appearing several times (that's `EvSq` reversed). That gave us our first chunk tag and made it possible to find more.

### Step 3: Use known data as ground truth

The most powerful trick: open a project in the real application, save it with **specific known content** (e.g., one note at middle C, velocity 64), then look at the file and find that data. Now you have a Rosetta Stone.

We did this for Logic Pro. The test project has 3 E4 notes at velocity 5, 40, and 39. When parsing the binary, we knew to look for `0x40` (= MIDI 64 = E4) and the velocity values nearby. Once we found one note record, we could see the structure repeat.

### Step 4: Verify by round-tripping

The ultimate proof that you understand a format is **byte-perfect round-tripping**: parse the file, immediately serialize it back, and check that the bytes are identical to the original. If they are, you've captured every detail. If they're not, the diff tells you exactly where your understanding is wrong.

We round-trip the 2 MB Dorico score.dtn file byte-identically. That gave us confidence to start modifying it.

### Step 5: Modify minimally and observe

When we wanted to learn how Dorico encodes time signatures, we made the smallest possible change (4/4 → 6/8) and re-saved the file. Then we diffed the binaries to see exactly which bytes changed. That's how we found the `numerator`/`denominator` fields.

---

## 11. Common Python patterns to remember

### Dataclasses for data, regular classes for behavior

If a class is mostly attributes you set and read, use `@dataclass`. If it has lots of methods that *do* things, write a regular class.

### Always close what you open

```python
# Manual:
conn = sqlite3.connect(path)
try:
    do_stuff(conn)
finally:
    conn.close()

# Better:
with sqlite3.connect(path) as conn:
    do_stuff(conn)
```

`with` blocks are Python's RAII. Use them whenever a class supports it (files, connections, locks, etc.).

### Type hints help your future self

```python
def parse_dtn(data: bytes) -> DtnFile: ...
```

Type hints don't change how the code runs (Python is still dynamically typed), but they let editors and tools like `mypy` catch bugs before you hit them. They also serve as inline documentation.

### Slices are everywhere

```python
header = data[:12]              # first 12 bytes
trailer = data[-16:]            # last 16 bytes
middle = data[12:24]            # bytes 12 through 23
reversed_bytes = data[::-1]     # all bytes, backwards
```

Slicing is one of the most powerful features in Python. It works on strings, lists, tuples, bytes, and any class that implements `__getitem__`.

### List comprehensions over loops

```python
# Good:
key_indices = [k for k, name in enumerate(keys) if "note" in name]

# Less good:
key_indices = []
for k, name in enumerate(keys):
    if "note" in name:
        key_indices.append(k)
```

Comprehensions are usually faster, more readable, and harder to mess up than the equivalent loop.

### `enumerate` for index + value

```python
for i, item in enumerate(my_list):
    print(f"{i}: {item}")
```

Use `enumerate` instead of `range(len(my_list))`. Cleaner and less error-prone.

### `zip` for parallel iteration

```python
for original, modified in zip(orig_notes, new_notes):
    if original.pitch != modified.pitch:
        print("changed!")
```

`zip` lets you walk multiple iterables in lockstep.

### f-strings beat `.format` and `%`

```python
# Modern (since Python 3.6):
print(f"Found {count} notes at position {pos:6d}")

# Old:
print("Found {} notes at position {:6d}".format(count, pos))
print("Found %d notes at position %6d" % (count, pos))
```

The `:6d` inside an f-string is a **format specifier** — `6d` means "decimal integer, right-aligned in a 6-character field". You can format hex, floats, strings, and more this way.

### Exceptions for exceptional cases

```python
if data[:4] != MAGIC:
    raise ValueError(f"Invalid magic: {data[:4].hex()}")
```

When something goes wrong, **raise an exception** with a descriptive message. Don't return error codes or `None` and hope the caller checks. Exceptions force the issue to be handled (or crash visibly), and the message tells you exactly what's wrong.

---

## Where to go next

If you want to extend this project, the most interesting (and most needed) tasks are:

1. **Dorico note writing** — the `NoteEventDefinition` entity structure is now fully understood from a real score (Salut d'Amour), and the DTN serializer works. What's left is the *cloning* logic: clear existing events from a voice block, clone the template entity structure for each note, set its `pitch`, `position`, and `duration` fields, and write it back. The infrastructure is ready; this is just careful tree manipulation.

2. **Multi-track Dorico read** — right now all voice stream blocks get collapsed into one track. The project needs to map voice blocks → instrument tracks using the flow's `eventStreams` → `blockInstanceIDs` → player/stave metadata chain.

3. **Better track matching** — the current sync only matches tracks by exact name. Add fuzzy matching, instrument family detection, and a `logico.toml` config file for user-defined mappings.

4. **A file watcher** — `logico watch source.stf dest.logicx` that automatically syncs whenever either file changes. Use the `watchdog` library (already in our dependencies). The main challenge is debouncing (the app may write the file several times during a save) and avoiding sync loops (the watcher shouldn't re-trigger on its own writes).

5. **A test suite** — write `pytest` tests that verify round-tripping for all three formats, so we don't break things accidentally. The test projects (`test.dorico`, `Code Noir.stf`, `Project.logicx`) are already in the repo.

Each of those is a self-contained project, and each one teaches a different Python skill (binary tree manipulation / data modelling / OS event handling / testing).

Have fun.
