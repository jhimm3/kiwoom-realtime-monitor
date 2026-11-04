"""Write a user-selected JSON backup without truncating an existing backup."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class ImmutableContentConflict(FileExistsError):
    """The destination was read successfully and contains different immutable bytes."""


def write_immutable_bytes(path: Path, payload: bytes) -> None:
    """Atomically publish one immutable artifact; identical competing bytes are a no-op."""
    if path.exists():
        if path.read_bytes() != payload:
            raise ImmutableContentConflict("immutable artifact has different bytes")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ImmutableContentConflict("immutable artifact has different bytes") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json_backup(
    path: Path, document: Any, *, indent: int | None = 2,
    separators: tuple[str, str] | None = None,
) -> None:
    payload = json.dumps(document, ensure_ascii=False, indent=indent, separators=separators).encode("utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
