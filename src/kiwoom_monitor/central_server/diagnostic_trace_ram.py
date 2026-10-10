"""Private framed RAM storage; no files, hashing, compression, or payload thawing."""
from __future__ import annotations

import json
import struct
import sys
from collections.abc import Mapping

_HEADER = struct.Struct('>8sIQQ')
_FRAME = struct.Struct('>II')
_MAGIC = b'KTRACE01'
_ABSENT = 0xffffffff
_INTERNAL = frozenset(('payload', '_memory_charge', '_scalar_charge'))
PAYLOAD_BLOCK_BYTES = 1024 * 1024
MAX_BLOCK_PAYLOAD_BYTES = 128 * 1024 * 1024
MAX_PAYLOAD_BLOCKS = 128


def encode_payload_blocks(value, *, block_bytes=PAYLOAD_BLOCK_BYTES,
                          maximum_bytes=MAX_BLOCK_PAYLOAD_BYTES):
    """Encode exact legacy JSON into bounded bytes blocks, never a whole JSON string."""
    if type(block_bytes) is not int or not 1 <= block_bytes <= PAYLOAD_BLOCK_BYTES:
        raise ValueError('trace_payload_block_size_invalid')
    blocks, buffer, total = [], bytearray(), 0
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(',', ':'))
    for piece in encoder.iterencode(value):
        # iterencode may yield a whole escaped string. Bound its UTF-8 conversion too.
        for start in range(0, len(piece), 16384):
            encoded = piece[start:start + 16384].encode('utf-8')
            total += len(encoded)
            if total > maximum_bytes:
                raise OSError('trace_event_too_large')
            offset = 0
            while offset < len(encoded):
                length = min(block_bytes - len(buffer), len(encoded) - offset)
                buffer.extend(encoded[offset:offset + length])
                offset += length
                if len(buffer) == block_bytes:
                    blocks.append(bytes(buffer))
                    buffer.clear()
    if buffer:
        blocks.append(bytes(buffer))
    if not blocks or len(blocks) > MAX_PAYLOAD_BLOCKS:
        raise OSError('trace_event_too_large')
    return tuple(blocks)


class Segment:
    __slots__ = ('data', 'count', 'first_seq', 'last_seq', 'charge', 'scalar_charge',
                 'issued_offset', 'committed_offset', 'issued', 'committed')

    def __init__(self, data: bytes, scalar_bytes: int):
        magic, count, first, last = _HEADER.unpack_from(data)
        if magic != _MAGIC or not count or first > last:
            raise ValueError('trace_ram_header_invalid')
        self.data, self.count, self.first_seq, self.last_seq = data, count, first, last
        # Include the bytes allocation, segment/cursor object and deque allocation slack.
        self.charge = sys.getsizeof(data) + sys.getsizeof(self) + 128
        self.scalar_charge = scalar_bytes + self.charge - len(data)
        self.issued_offset = self.committed_offset = _HEADER.size
        self.issued = self.committed = 0

    def take(self):
        if self.issued == self.count:
            return None
        row = PackedRow(self, self.issued_offset)
        self.issued_offset, self.issued = row.end, self.issued + 1
        return row

    def commit(self, row):
        if row.segment is not self or row.offset != self.committed_offset:
            raise RuntimeError('trace_ram_commit_out_of_order')
        self.committed_offset, self.committed = row.end, self.committed + 1
        if self.committed == self.count and row.end != len(self.data):
            raise ValueError('trace_ram_trailing_bytes')

    def rewind(self):
        self.issued_offset, self.issued = self.committed_offset, self.committed


class PackedRow(Mapping):
    """Bounded writer metadata view. The payload remains encoded in its segment."""
    __slots__ = ('segment', 'offset', 'end', 'metadata', 'payload_offset', 'payload_size')

    def __init__(self, segment, offset):
        data = segment.data
        if offset < _HEADER.size or offset + _FRAME.size > len(data):
            raise ValueError('trace_ram_frame_invalid')
        metadata_size, payload_size = _FRAME.unpack_from(data, offset)
        start = offset + _FRAME.size
        payload_offset = start + metadata_size
        end = payload_offset + (0 if payload_size == _ABSENT else payload_size)
        if not metadata_size or end > len(data):
            raise ValueError('trace_ram_frame_invalid')
        metadata = json.loads(data[start:payload_offset])
        if type(metadata) is not dict or type(metadata.get('seq')) is not int:
            raise ValueError('trace_ram_metadata_invalid')
        self.segment, self.offset, self.end, self.metadata = segment, offset, end, metadata
        self.payload_offset, self.payload_size = payload_offset, payload_size

    def payload_bytes(self):
        if self.payload_size == _ABSENT:
            return None
        return self.segment.data[self.payload_offset:self.end]

    def payload_blocks(self):
        value = self.payload_bytes()
        return () if value is None else (value,)

    def __iter__(self):
        return iter(self.metadata)

    def __len__(self):
        return len(self.metadata)

    def __getitem__(self, key):
        return self.metadata[key]


class BlockSegment:
    """One logical event owns multiple byte blocks and the same commit cursor contract."""
    __slots__ = ('metadata', 'blocks', 'count', 'first_seq', 'last_seq', 'charge',
                 'scalar_charge', 'issued', 'committed')

    def __init__(self, metadata, blocks):
        self.metadata, self.blocks = metadata, blocks
        self.count = 1
        self.first_seq = self.last_seq = json.loads(metadata)['seq']
        self.charge = (sys.getsizeof(self) + sys.getsizeof(metadata) + sys.getsizeof(blocks)
                       + sum(sys.getsizeof(block) for block in blocks) + 128)
        self.scalar_charge = self.charge - sum(len(block) for block in blocks)
        self.issued = self.committed = 0

    def take(self):
        if self.issued:
            return None
        self.issued = 1
        return BlockRow(self)

    def commit(self, row):
        if row.segment is not self or self.committed or not self.issued:
            raise RuntimeError('trace_ram_commit_out_of_order')
        self.committed = 1

    def rewind(self):
        self.issued = self.committed


class BlockRow(PackedRow):
    __slots__ = ()

    def __init__(self, segment):
        self.segment = segment
        self.metadata = json.loads(segment.metadata)
        self.offset, self.end = 0, 1
        self.payload_offset = len(segment.metadata)
        self.payload_size = sum(len(block) for block in segment.blocks)

    def payload_bytes(self):
        raise RuntimeError('trace_block_payload_requires_block_reader')

    def payload_blocks(self):
        return self.segment.blocks


def pack_records(records, *, target_bytes=1024 * 1024,
                 max_metadata_bytes=1024 * 1024, max_payload_bytes=16 * 1024 * 1024):
    """Seal bounded segments without retaining per-event objects or offset indexes."""
    segments = []
    buffer = bytearray(_HEADER.size)
    count, first, last, scalar = 0, 0, 0, _HEADER.size

    def seal():
        nonlocal buffer, count, first, last, scalar
        if count:
            _HEADER.pack_into(buffer, 0, _MAGIC, count, first, last)
            segments.append(Segment(bytes(buffer), scalar))
        buffer = bytearray(_HEADER.size)
        count, first, last, scalar = 0, 0, 0, _HEADER.size

    previous = None
    for record in records:
        metadata = {key: value for key, value in record.items() if key not in _INTERNAL}
        seq = metadata['seq']
        if type(seq) is not int or previous is not None and seq <= previous:
            raise ValueError('trace_ram_sequence_invalid')
        previous = seq
        encoded = json.dumps(metadata, ensure_ascii=False, separators=(',', ':'), default=str).encode('utf-8')
        if len(encoded) > max_metadata_bytes:
            raise OSError('trace_event_too_large')
        if record.get('payload_profile') in {'large-store-input/v1', 'account-context-input/v1'} and 'payload' in record:
            seal()
            segments.append(BlockSegment(encoded, encode_payload_blocks(record['payload'])))
            continue
        payload = (json.dumps(record['payload'], ensure_ascii=False, separators=(',', ':')).encode('utf-8')
                   if 'payload' in record else None)
        if len(encoded) > max_metadata_bytes or payload is not None and len(payload) > max_payload_bytes:
            raise OSError('trace_event_too_large')
        size = _FRAME.size + len(encoded) + (len(payload) if payload is not None else 0)
        if count and len(buffer) + size > target_bytes:
            seal()
        buffer.extend(_FRAME.pack(len(encoded), _ABSENT if payload is None else len(payload)))
        buffer.extend(encoded)
        if payload is not None:
            buffer.extend(payload)
        else:
            scalar += size
        first = seq if not count else first
        last, count = seq, count + 1
    seal()
    return segments
