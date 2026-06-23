"""`.pandemoniumignore` matching + always-on secret/self exclusions.

A pragmatic gitignore subset: directory rules (`foo/`), globs (`*.pem`), and plain
names. A hard-coded SECRET/SELF set is enforced regardless of the file's contents so
secrets and the `.pandemonium/` store can never be indexed even if a user edits the
ignore file.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import Any, List

# Written verbatim by `pandemonium init` and used as a built-in safety net.
DEFAULT_IGNORE = """\
# PandemoniumProtocol ignore rules (gitignore-style).
.pandemonium/
.git/
.venv/
venv/
env/
node_modules/
bin/
obj/
dist/
build/
target/
out/
# Flutter/Dart build + vendored native deps (CocoaPods can be thousands of .h/.cc files).
.dart_tool/
.symlinks/
.plugin_symlinks/
.fvm/
ephemeral/
Pods/
Carthage/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.egg-info/
.vs/
.idea/
.vscode/
.env
.env.*
*.pem
*.key
*.pfx
*.cer
*.p12
secrets/
credentials/
*.zip
*.rar
*.7z
*.tar
*.gz
*.dll
*.exe
*.so
*.dylib
*.pyc
*.pyd
*.bin
*.png
*.jpg
*.jpeg
*.gif
*.ico
*.pdf
"""

# Enforced even if absent from the ignore file (defense in depth).
HARD_EXCLUDE_DIRS = {".pandemonium", ".git", "secrets", "credentials"}
HARD_EXCLUDE_GLOBS = [".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.cer", "*.p12"]


class IgnoreMatcher:
    def __init__(self, patterns: List[str]):
        self.dir_patterns: list[str] = []
        self.glob_patterns: list[str] = []
        self.plain: list[str] = []
        for raw in patterns:
            p = raw.strip()
            if not p or p.startswith("#"):
                continue
            p = p.lstrip("/")
            if p.endswith("/"):
                self.dir_patterns.append(p.rstrip("/"))
            elif any(ch in p for ch in "*?[]"):
                self.glob_patterns.append(p)
            else:
                self.plain.append(p)

    @classmethod
    def load(cls, repo_root: Any) -> "IgnoreMatcher":
        patterns = DEFAULT_IGNORE.splitlines()
        root = Path(repo_root)
        f = root / ".pandemoniumignore"
        if f.exists():
            patterns = patterns + f.read_text(encoding="utf-8", errors="replace").splitlines()
        else:
            # No project ignore file -> fall back to .gitignore so a repo's existing excludes
            # (build dirs, vendored deps, generated code) are respected out of the box. This
            # is a pragmatic gitignore subset, so negations (`!foo`) are simply not honored.
            gi = root / ".gitignore"
            if gi.exists():
                patterns = patterns + gi.read_text(encoding="utf-8",
                                                   errors="replace").splitlines()
        return cls(patterns)

    def matches(self, rel_path: str) -> bool:
        parts = rel_path.split("/")
        base = parts[-1]

        # Hard exclusions first.
        if HARD_EXCLUDE_DIRS.intersection(parts):
            return True
        for g in HARD_EXCLUDE_GLOBS:
            if fnmatch(base, g):
                return True

        for d in self.dir_patterns:
            if d in parts or rel_path == d or rel_path.startswith(d + "/"):
                return True
        for g in self.glob_patterns:
            if fnmatch(base, g) or fnmatch(rel_path, g):
                return True
        for pl in self.plain:
            if pl == base or pl == rel_path or pl in parts:
                return True
        return False
