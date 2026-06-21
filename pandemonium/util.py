"""Small shared helpers: deterministic IDs, hashing, timestamps, path normalization."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, Path]


def now_iso() -> str:
    """UTC timestamp, ISO-8601, second-stable."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def normalize_code(text: str) -> str:
    """Whitespace/blank-line-insensitive view of a code span, for structural hashing.
    Used so a fingerprint survives reformatting and line shifts but changes with the
    actual structure."""
    lines = (ln.strip() for ln in text.splitlines())
    return "\n".join(ln for ln in lines if ln)


def signature_hash_for(signature: Optional[str]) -> Optional[str]:
    """Stable hash of a symbol's signature (whitespace-collapsed). Discriminates
    same-qualified-name candidates (overloads, nested redefinitions) by their shape."""
    if not signature:
        return None
    return sha256_text(" ".join(signature.split()))


def fingerprint_for(span: str) -> Optional[str]:
    """Structural hash of a symbol's *body* — survives a *rename* (the body is unchanged),
    so a stale `path::OldName` ref can still be re-found after the symbol is renamed. The
    first line (the signature/def line, which carries the name) is dropped, so renaming the
    symbol doesn't change the fingerprint. Returns None for a body-less one-liner."""
    inner = "\n".join(span.splitlines()[1:])  # drop the signature line
    norm = normalize_code(inner)
    return sha256_text(norm) if norm else None


def _short(*parts: str, n: int = 16) -> str:
    digest = hashlib.sha1("\x00".join(parts).encode("utf-8", "replace")).hexdigest()
    return digest[:n]


# Deterministic IDs: stable for the same (repo, path, structure) so incremental
# reindex of an unchanged file is a no-op and tests are reproducible.
def repo_id_for(root_path: PathLike) -> str:
    return "repo_" + _short(str(Path(root_path).resolve()).lower(), n=12)


def file_id_for(repo_id: str, rel_path: str) -> str:
    return "file_" + _short(repo_id, rel_path, n=16)


def symbol_id_for(file_id: str, qualified_name: str, start_line: int) -> str:
    return "sym_" + _short(file_id, qualified_name, str(start_line), n=16)


def chunk_id_for(file_id: str, start_line: int, end_line: int, chunk_type: str) -> str:
    return "chunk_" + _short(file_id, str(start_line), str(end_line), chunk_type, n=16)


def to_posix(p: PathLike) -> str:
    """Repo-relative POSIX path (consistent keys across OSes)."""
    return Path(p).as_posix()


def rel_posix(path: PathLike, root: PathLike) -> str:
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
