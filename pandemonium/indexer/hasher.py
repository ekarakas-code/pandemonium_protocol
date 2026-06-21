"""File reading + content hashing for incremental indexing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Tuple

from pandemonium.util import sha256_bytes


def looks_binary(data: bytes) -> bool:
    """Heuristic: a NUL byte in the first 8 KB => treat as binary, skip."""
    return b"\x00" in data[:8192]


def read_file(path: Any) -> Optional[Tuple[bytes, str, str]]:
    """Return (raw_bytes, decoded_text, content_hash) or None if binary/unreadable."""
    try:
        data = Path(path).read_bytes()
    except (OSError, PermissionError):
        return None
    if looks_binary(data):
        return None
    text = data.decode("utf-8", errors="replace")
    return data, text, sha256_bytes(data)
