"""Module-body / complement card (gated: indexing.complement_card): the residue BETWEEN symbol
spans becomes findable, never overlapping a symbol, flagged partial-unsafe."""

from pandemonium.indexer.chunker import build_chunks
from pandemonium.models import Symbol
from pandemonium.storage.sqlite_store import SqliteStore
from pandemonium.util import repo_id_for
from tests.support import make_settings, reindex


def _sym(name, start, end, stype="function"):
    return Symbol(id=f"s_{name}", repo_id="r", file_id="f", symbol_type=stype, name=name,
                  qualified_name=name, signature=f"def {name}():", start_line=start, end_line=end)


WIRING = (
    "import os\n"                              # 1
    "import sys\n"                             # 2
    "from app.core import run\n"              # 3
    "from app.config import load_settings\n"  # 4
    "\n"                                       # 5
    "def build_parser():\n"                   # 6  SYMBOL
    "    return None\n"                        # 7
    "\n"                                       # 8
    "if __name__ == '__main__':\n"            # 9
    "    parser = build_parser()\n"           # 10
    "    settings = load_settings()\n"        # 11
    "    args = parser.parse_args()\n"        # 12
    "    run(args, settings)\n"               # 13
)
SYMS = [_sym("build_parser", 6, 7)]


def test_off_is_noop():
    chunks = build_chunks("r", "f", "cli.py", "python", WIRING, SYMS,
                          scopes=["symbol"], complement=False)
    assert not any(c.chunk_type == "module_body" for c in chunks)


def test_on_emits_residue_not_overlapping_symbols():
    chunks = build_chunks("r", "f", "cli.py", "python", WIRING, SYMS,
                          scopes=["symbol"], complement=True, complement_min_lines=4)
    mb = [c for c in chunks if c.chunk_type == "module_body"]
    assert mb, "expected a module_body residue card"
    for c in mb:  # never overlaps the symbol span [6,7]
        assert c.end_line < 6 or c.start_line > 7
    assert any(c.start_line <= 9 and c.end_line >= 13 for c in mb)  # the __main__ block is covered


def test_threshold_drops_tiny_gaps():
    text = ("x = 1\n"                       # 1  (1-line top gap)
            "def f():\n"                    # 2  SYMBOL
            "    return 1\n"                # 3
            "if __name__ == '__main__':\n"  # 4  (2-line gap)
            "    f()\n")                    # 5
    chunks = build_chunks("r", "f", "m.py", "python", text, [_sym("f", 2, 3)],
                          scopes=["symbol"], complement=True, complement_min_lines=4)
    assert not any(c.chunk_type == "module_body" for c in chunks)  # every gap < 4 non-blank


def test_never_overlaps_class_body():
    text = ("import os\n"        # 1
            "import sys\n"       # 2
            "from a import b\n"  # 3
            "import json\n"      # 4
            "\n"                 # 5
            "class C:\n"         # 6  class span 6..10
            "    def m(self):\n"  # 7
            "        return 1\n"  # 8
            "    def n(self):\n"  # 9
            "        return 2\n")  # 10
    syms = [_sym("C", 6, 10, "class"), _sym("C.m", 7, 8, "method"), _sym("C.n", 9, 10, "method")]
    chunks = build_chunks("r", "f", "c.py", "python", text, syms,
                          scopes=["symbol"], complement=True, complement_min_lines=4)
    mb = [c for c in chunks if c.chunk_type == "module_body"]
    assert mb  # the top import block qualifies
    for c in mb:  # no residue card intersects the class body
        assert c.end_line < 6 or c.start_line > 10


def test_indexed_card_is_flagged_partial(tmp_path):
    (tmp_path / "cli.py").write_text(WIRING, encoding="utf-8")
    settings = make_settings(tmp_path)
    settings.data["indexing"]["complement_card"] = True
    reindex(settings, incremental=False)
    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    try:
        rows = store.conn.execute(
            "SELECT * FROM chunks WHERE repo_id=? AND chunk_type='module_body'",
            (repo_id_for(settings.repo_root),)).fetchall()
        assert rows, "module_body chunk should be indexed when the flag is on"
        assert not dict(rows[0])["is_complete_unit"]  # partial — not safe to reason from alone
    finally:
        store.close()
