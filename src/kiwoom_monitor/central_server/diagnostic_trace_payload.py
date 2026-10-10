"""Immutable offline references to checksummed block payloads, not producer inputs."""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class BlockPayloadReference:
    trace_id: str
    digest: str
    descriptor: bytes

    def load(self):
        # Pin the descriptor, not mutable manifest dictionaries or arbitrary paths.
        # Every use rechecks the physical files and logical digest before decoding.
        from .diagnostic_trace import _payload_blocks_from_manifest
        root = json.loads(self.descriptor)
        blocks = _payload_blocks_from_manifest(
            self.trace_id, self.digest, {'schema_version': 4, 'blobs': {self.digest: root}})
        try:
            return json.loads(b''.join(blocks))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError('recorded_payload_json_invalid') from error


def block_reference(trace_id, digest, root):
    reference = BlockPayloadReference(
        trace_id, digest, json.dumps(root, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    # Validate before returning a reader result. No decoded large payload is retained.
    reference.load()
    return reference
