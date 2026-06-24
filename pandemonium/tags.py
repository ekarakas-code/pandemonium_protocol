"""Fixed-schema tags.

Six fields (from the review): responsibilities, depends_on, domain, search_terms,
side_effects, entrypoints. Phase 1 fills the locally-derivable ones heuristically
(search_terms, domain, side_effects, entrypoints); `responsibilities`/`depends_on`
are left for the Phase 3 LLM enricher. The schema is fixed so filtering stays
consistent across providers; a normalization layer is deferred.
"""

from __future__ import annotations

import re
from typing import List, Optional

TAG_FIELDS = ["responsibilities", "depends_on", "domain", "search_terms",
              "side_effects", "entrypoints"]

_SIDE_EFFECT_HINTS = {
    "writes_disk": ["open(", ".write(", "write_text", "write_bytes", "to_csv",
                    "savefig", "mkdir(", "makedirs"],
    "deletes": ["unlink(", "rmtree", "os.remove", "drop table", "delete from"],
    "network": ["requests.", "httpx", "urllib", "socket.", "http://", "https://",
                "fetch("],
    "database": ["execute(", ".cursor(", "insert into", "update ", "commit("],
    "subprocess": ["subprocess", "os.system", "popen"],
    "mutates_global": ["global ", "os.environ"],
}
_ENTRYPOINT_HINTS = {
    "cli_command": ["@app.command", "typer.", "argparse", "click.command"],
    "api_route": ["@app.route", "@router.", "@app.get", "@app.post", "fastapi"],
    "mcp_tool": ["@mcp.tool"],
    "main": ["__main__"],
    "test": ["def test_", "pytest", "unittest"],
}

_CAMEL = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+")


def empty_tags() -> dict:
    return {f: [] for f in TAG_FIELDS}


def _identifier_words(*names: Optional[str]) -> List[str]:
    words: list[str] = []
    for name in names:
        if not name:
            continue
        for part in re.split(r"[._]", name):
            for w in _CAMEL.findall(part):
                lw = w.lower()
                if len(lw) > 2 and lw not in words:
                    words.append(lw)
    return words[:12]


def heuristic_tags(path: str, qualified_name: Optional[str], name: Optional[str],
                   content: str) -> dict:
    tags = empty_tags()
    tags["search_terms"] = _identifier_words(qualified_name, name)
    tags["domain"] = [p for p in path.split("/")[:-1] if p][-4:]
    low = content.lower()
    tags["side_effects"] = [label for label, hints in _SIDE_EFFECT_HINTS.items()
                            if any(h in low for h in hints)]
    tags["entrypoints"] = [label for label, hints in _ENTRYPOINT_HINTS.items()
                           if any(h in low for h in hints)]
    return tags


_SCOPE_BY_CHUNK_TYPE = {
    "class": "symbol", "method": "symbol", "function": "symbol",
    "ast_block": "code", "block": "code", "window": "code", "file": "file",
    "module_body": "code",  # between-symbol residue card (line-based, code scope)
}


def scope_for(chunk_type: str) -> str:
    return _SCOPE_BY_CHUNK_TYPE.get(chunk_type, "code")
