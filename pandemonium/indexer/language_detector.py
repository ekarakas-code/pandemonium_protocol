"""Map file extensions to language labels.

`PARSEABLE` are languages with a tree-sitter symbol extractor (Python for the MVP).
Other known-textual languages are still indexed via line-window fallback chunks, so
docs/config/SQL/etc. remain searchable. Unknown extensions are skipped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

EXT_LANG = {
    ".py": "python", ".pyi": "python",
    ".cs": "c_sharp",
    # .NET project / markup / view files — no tree-sitter symbols, but text-indexed so a
    # .NET solution's build config, Razor/Blazor views, and XAML UI are searchable.
    ".csproj": "xml", ".props": "xml", ".targets": "xml", ".sln": "text",
    ".razor": "html", ".cshtml": "html", ".xaml": "xml",
    ".vb": "text", ".fs": "text", ".fsx": "text",  # VB.NET / F#: searchable (no grammar)
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".dart": "dart",
    ".java": "java", ".go": "go", ".rb": "ruby", ".rs": "rust", ".php": "php",
    ".c": "c", ".h": "cpp", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".md": "markdown", ".markdown": "markdown", ".rst": "text", ".txt": "text",
    ".json": "json", ".jsonc": "json",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".xml": "xml", ".html": "html", ".htm": "html", ".css": "css", ".scss": "css",
    ".sql": "sql", ".sh": "bash", ".bash": "bash", ".ps1": "text",
    ".ini": "ini", ".cfg": "ini", ".conf": "ini",
}

# Languages we can structurally parse into symbols (Phase 6).
# dart/html/css use custom extractors (see tree_sitter_parser._CUSTOM_PARSERS); only Dart
# also contributes graph edges (see graph.EDGE_LANGUAGES) — html/css are symbols-only.
PARSEABLE = {"python", "cpp", "c_sharp", "javascript", "typescript", "tsx",
             "dart", "html", "css"}


def detect(path) -> Optional[str]:
    ext = Path(path).suffix.lower()
    return EXT_LANG.get(ext)


def is_parseable(language: Optional[str]) -> bool:
    return language in PARSEABLE
