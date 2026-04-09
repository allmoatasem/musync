"""Parser for Dorico's .dtn binary format.

The .dtn format is a custom binary serialization used by Dorico to store
score data (score.dtn) and library definitions (scorelibrary.dtn).

Structure:
  - 12-byte header: version(u32 LE), type(u32 LE), key_count(u32 LE)
  - Key string table: key_count null-terminated UTF-8 strings (field names)
  - Value string table: val_count(u32 LE) then val_count null-terminated UTF-8 strings
  - Entity tree: binary tree using opcodes with LEB128 varints

Two opcode encodings exist (auto-detected from the first byte of the entity tree):

  Legacy encoding (Dorico ≤ 4.x, score.dtn files where first byte ≥ 0xFC):
    0xFE: Entity start
    0xFF: Array entity
    0xFC: Key-value pair
    0xFD: Null/empty placeholder

  Modern encoding (Dorico 5.x+, first byte < 0x20):
    0x1F: Entity start
    0x1E: Array entity
    0x1C: Key-value pair
    0x1D: Null/empty placeholder
"""

from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, field
from typing import BinaryIO

# Increase recursion limit for deeply nested entity trees
sys.setrecursionlimit(10000)

# Legacy opcodes (Dorico ≤ 4.x)
OP_ENTITY = 0xFE
OP_ARRAY = 0xFF
OP_KV = 0xFC
OP_NULL = 0xFD

# Modern opcodes (Dorico 5.x+)
OP_ENTITY_V2 = 0x1F
OP_ARRAY_V2 = 0x1E
OP_KV_V2 = 0x1C
OP_NULL_V2 = 0x1D


@dataclass
class DtnKV:
    """A key-value leaf node (FC/1C opcode)."""

    key_idx: int
    value_idx: int

    def key(self, keys: list[str]) -> str:
        return keys[self.key_idx] if self.key_idx < len(keys) else f"?{self.key_idx}"

    def value(self, values: list[str]) -> str:
        return values[self.value_idx] if self.value_idx < len(values) else f"?{self.value_idx}"


@dataclass
class DtnEntity:
    """An entity node (FE/FF or 1E/1F opcode) with children."""

    key_idx: int
    flags: int
    is_array: bool
    children: list[DtnEntity | DtnKV | None] = field(default_factory=list)
    # Original child key list values (opaque IDs from the source file).
    # Preserved for byte-identical round-trip serialization. When new children
    # are added, the serializer will append default values to this list.
    child_key_list: list[int] = field(default_factory=list)
    # Null-child placeholder data: each None child has its own (key, value) pair
    # consumed from the FD/1D opcode. Stored in order of appearance.
    null_child_data: list[tuple[int, int]] = field(default_factory=list)

    def key(self, keys: list[str]) -> str:
        return keys[self.key_idx] if self.key_idx < len(keys) else f"?{self.key_idx}"

    def get_kv(self, key_name: str, keys: list[str], values: list[str]) -> str | None:
        """Get a key-value child's value by key name."""
        for child in self.children:
            if isinstance(child, DtnKV) and child.key(keys) == key_name:
                return child.value(values)
        return None

    def get_entity(self, key_name: str, keys: list[str]) -> DtnEntity | None:
        """Get a child entity by key name."""
        for child in self.children:
            if isinstance(child, DtnEntity) and child.key(keys) == key_name:
                return child
        return None

    def get_entities(self, key_name: str, keys: list[str]) -> list[DtnEntity]:
        """Get all child entities with the given key name."""
        return [
            child
            for child in self.children
            if isinstance(child, DtnEntity) and child.key(keys) == key_name
        ]

    def get_all_kvs(self, keys: list[str], values: list[str]) -> dict[str, str]:
        """Get all key-value children as a dict."""
        result = {}
        for child in self.children:
            if isinstance(child, DtnKV):
                result[child.key(keys)] = child.value(values)
        return result


@dataclass
class DtnFile:
    """A parsed .dtn file."""

    version: int
    file_type: int
    keys: list[str]
    values: list[str]
    root: DtnEntity
    # Wrapper entity that precedes the root in the binary.
    # Captured byte-for-byte so it can be reproduced on serialization.
    wrapper_bytes: bytes = b""
    # True when the file uses the modern 0x1C/1D/1E/1F opcode encoding
    # (Dorico 5.x+). False for the legacy 0xFC/FD/FE/FF encoding.
    uses_new_opcodes: bool = False

    def dump(self, max_depth: int = 3) -> str:
        """Return a human-readable dump of the entity tree."""
        lines: list[str] = []
        self._dump_node(self.root, lines, 0, max_depth)
        return "\n".join(lines)

    def _dump_node(
        self, node: DtnEntity | DtnKV | None, lines: list[str], depth: int, max_depth: int
    ) -> None:
        indent = "  " * depth
        if node is None:
            lines.append(f"{indent}(null)")
            return
        if isinstance(node, DtnKV):
            k = node.key(self.keys)
            v = node.value(self.values)
            lines.append(f"{indent}{k} = {repr(v)}")
            return
        tag = "[]" if node.is_array else "{}"
        k = node.key(self.keys)
        lines.append(f"{indent}{k} {tag[0]}")
        if depth < max_depth:
            for child in node.children:
                self._dump_node(child, lines, depth + 1, max_depth)
        elif node.children:
            lines.append(f"{indent}  ... ({len(node.children)} children)")
        lines.append(f"{indent}{tag[1]}")


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read an unsigned LEB128 varint. Returns (value, new_position)."""
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ValueError(f"Unexpected end of data reading varint at offset {pos}")
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if (b & 0x80) == 0:
            break
        shift += 7
    return result, pos


def _parse_children(
    data: bytes,
    pos: int,
    num_children: int,
    null_data: list[tuple[int, int]],
    op_entity: int,
    op_array: int,
    op_kv: int,
    op_null: int,
) -> tuple[list[DtnEntity | DtnKV | None], int]:
    """Parse num_children child nodes starting at pos.

    null_data is appended to with (key, value) pairs from each null opcode
    encountered, in order, so the serializer can reproduce them.
    """
    children: list[DtnEntity | DtnKV | None] = []
    for _ in range(num_children):
        if pos >= len(data):
            break
        opcode = data[pos]
        if opcode == op_kv:
            pos += 1
            key_idx, pos = read_varint(data, pos)
            value_idx, pos = read_varint(data, pos)
            children.append(DtnKV(key_idx=key_idx, value_idx=value_idx))
        elif opcode in (op_entity, op_array):
            entity, pos = _parse_entity(data, pos, op_entity, op_array, op_kv, op_null)
            children.append(entity)
        elif opcode == op_null:
            pos += 1
            null_key, pos = read_varint(data, pos)
            null_val, pos = read_varint(data, pos)
            null_data.append((null_key, null_val))
            children.append(None)
        else:
            raise ValueError(
                f"Unknown opcode 0x{opcode:02x} at offset 0x{pos:x}"
            )
    return children, pos


def _parse_entity(
    data: bytes,
    pos: int,
    op_entity: int,
    op_array: int,
    op_kv: int,
    op_null: int,
) -> tuple[DtnEntity, int]:
    """Parse an entity node. Returns (entity, end_position)."""
    is_array = data[pos] == op_array
    pos += 1  # skip opcode

    key_idx, pos = read_varint(data, pos)
    flags, pos = read_varint(data, pos)
    num_children, pos = read_varint(data, pos)

    # Read the child key list (opaque IDs — preserved for round-trip)
    child_key_list: list[int] = []
    for _ in range(num_children):
        ck, pos = read_varint(data, pos)
        child_key_list.append(ck)

    # Parse children by opcode
    null_data: list[tuple[int, int]] = []
    children, pos = _parse_children(
        data, pos, num_children, null_data, op_entity, op_array, op_kv, op_null
    )

    return DtnEntity(
        key_idx=key_idx,
        flags=flags,
        is_array=is_array,
        children=children,
        child_key_list=child_key_list,
        null_child_data=null_data,
    ), pos


def parse_dtn(data: bytes) -> DtnFile:
    """Parse a .dtn binary file into a DtnFile structure."""
    if len(data) < 12:
        raise ValueError("File too small for DTN header")

    version, file_type, key_count = struct.unpack_from("<III", data, 0)
    pos = 12

    # Parse key string table
    keys: list[str] = []
    for _ in range(key_count):
        end = data.index(0, pos)
        keys.append(data[pos:end].decode("utf-8"))
        pos = end + 1

    # Parse value string table
    val_count = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    values: list[str] = []
    for _ in range(val_count):
        end = data.index(0, pos)
        values.append(data[pos:end].decode("utf-8"))
        pos = end + 1

    # Detect opcode encoding from the first byte of the entity tree.
    # Modern Dorico 5.x+ files use 0x1E/0x1F; legacy files use 0xFE/0xFF.
    if pos >= len(data):
        raise ValueError("No entity tree data")

    first_byte = data[pos]
    if first_byte in (OP_ENTITY, OP_ARRAY):
        # Legacy encoding
        uses_new = False
        op_entity, op_array, op_kv, op_null = OP_ENTITY, OP_ARRAY, OP_KV, OP_NULL
    elif first_byte in (OP_ENTITY_V2, OP_ARRAY_V2):
        # Modern encoding
        uses_new = True
        op_entity, op_array, op_kv, op_null = OP_ENTITY_V2, OP_ARRAY_V2, OP_KV_V2, OP_NULL_V2
    else:
        raise ValueError(
            f"Unknown entity tree opcode 0x{first_byte:02x} at offset 0x{pos:x}. "
            f"Expected 0xFE/0xFF (legacy) or 0x1E/0x1F (modern Dorico 5+)."
        )

    wrapper_start = pos

    # Read wrapper header: opcode + key + flags + num_children varints
    pos += 1
    _, pos = read_varint(data, pos)   # wrapper key
    _, pos = read_varint(data, pos)   # wrapper flags
    wrapper_children, pos = read_varint(data, pos)  # usually 0

    # If wrapper claims children, skip its child key list
    for _ in range(wrapper_children):
        _, pos = read_varint(data, pos)

    # Capture wrapper bytes for byte-identical round-trip
    wrapper_bytes = data[wrapper_start:pos]

    # Parse root entity (kScore or equivalent)
    if data[pos] not in (op_entity, op_array):
        raise ValueError(
            f"Expected root entity at 0x{pos:x}, got 0x{data[pos]:02x}"
        )

    root, _ = _parse_entity(data, pos, op_entity, op_array, op_kv, op_null)

    return DtnFile(
        version=version,
        file_type=file_type,
        keys=keys,
        values=values,
        root=root,
        wrapper_bytes=wrapper_bytes,
        uses_new_opcodes=uses_new,
    )


def parse_dtn_file(path: str) -> DtnFile:
    """Parse a .dtn file from disk."""
    with open(path, "rb") as f:
        data = f.read()
    return parse_dtn(data)


# --- Serialization ---


def write_varint(value: int) -> bytes:
    """Encode an unsigned integer as LEB128 varint."""
    if value < 0:
        raise ValueError(f"Cannot encode negative varint: {value}")
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    return bytes(out)


def _serialize_entity(entity: DtnEntity, out: bytearray, op_entity: int, op_array: int, op_kv: int, op_null: int) -> None:
    """Serialize an entity and its children into out."""
    out.append(op_array if entity.is_array else op_entity)
    out.extend(write_varint(entity.key_idx))
    out.extend(write_varint(entity.flags))
    out.extend(write_varint(len(entity.children)))

    # Child key list — must have one entry per child.
    # Pad with 0 if the list is shorter than children (e.g., new children added).
    ckl = list(entity.child_key_list)
    while len(ckl) < len(entity.children):
        ckl.append(0)
    for ck in ckl[: len(entity.children)]:
        out.extend(write_varint(ck))

    # Serialize each child by opcode
    null_idx = 0
    for child in entity.children:
        if isinstance(child, DtnKV):
            out.append(op_kv)
            out.extend(write_varint(child.key_idx))
            out.extend(write_varint(child.value_idx))
        elif isinstance(child, DtnEntity):
            _serialize_entity(child, out, op_entity, op_array, op_kv, op_null)
        elif child is None:
            out.append(op_null)
            if null_idx < len(entity.null_child_data):
                nk, nv = entity.null_child_data[null_idx]
                null_idx += 1
            else:
                nk, nv = 0, 0
            out.extend(write_varint(nk))
            out.extend(write_varint(nv))
        else:
            raise ValueError(f"Unknown child type: {type(child)}")


def serialize_dtn(dtn: DtnFile) -> bytes:
    """Serialize a DtnFile back to its binary form."""
    out = bytearray()

    # File header: version, type, key_count
    out.extend(struct.pack("<III", dtn.version, dtn.file_type, len(dtn.keys)))

    # Key string table (null-terminated UTF-8)
    for key in dtn.keys:
        out.extend(key.encode("utf-8"))
        out.append(0)

    # Value string table: count then strings
    out.extend(struct.pack("<I", len(dtn.values)))
    for value in dtn.values:
        out.extend(value.encode("utf-8"))
        out.append(0)

    # Wrapper entity bytes (preserved verbatim)
    out.extend(dtn.wrapper_bytes)

    # Choose opcodes matching the original file
    if dtn.uses_new_opcodes:
        op_entity, op_array, op_kv, op_null = OP_ENTITY_V2, OP_ARRAY_V2, OP_KV_V2, OP_NULL_V2
    else:
        op_entity, op_array, op_kv, op_null = OP_ENTITY, OP_ARRAY, OP_KV, OP_NULL

    # Root entity tree
    _serialize_entity(dtn.root, out, op_entity, op_array, op_kv, op_null)

    return bytes(out)


def write_dtn_file(dtn: DtnFile, path: str) -> None:
    """Write a DtnFile to disk."""
    with open(path, "wb") as f:
        f.write(serialize_dtn(dtn))
