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

    def __iter__(self):
        return iter(self.metadata)

    def __len__(self):
        return len(self.metadata)

    def __getitem__(self, key):
        return self.metadata[key]


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
