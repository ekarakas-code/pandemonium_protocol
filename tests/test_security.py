"""Wave-6 hardening: repo_get path-traversal containment, content-blind secret redaction,
the size hint on cards, and `init` writing .pandemonium/ into .gitignore."""

import os

from pandemonium import refs
from pandemonium.secret_filter import redact_secrets


# --- path-traversal containment (refs._read_lines) ------------------------------------------

def test_read_lines_reads_inside_repo(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    assert refs._read_lines(tmp_path, "a.py") == ["x = 1", "y = 2"]


def test_read_lines_blocks_parent_escape(tmp_path):
    # An outside file the ref tries to reach via `../`.
    (tmp_path.parent / "outside.txt").write_text("TOPSECRET", encoding="utf-8")
    assert refs._read_lines(tmp_path, "../outside.txt") is None
    assert refs._read_lines(tmp_path, "../../../../../../etc/passwd") is None


def test_read_lines_blocks_absolute_path(tmp_path):
    # An absolute ref join replaces repo_root in pathlib, escaping containment.
    abspath = os.path.join(os.path.abspath(os.sep), "etc", "passwd")
    assert refs._read_lines(tmp_path, abspath) is None


# --- content-blind secret redaction ---------------------------------------------------------

def test_redact_aws_key_and_named_assignment():
    text = 'KEY = "AKIAIOSFODNN7EXAMPLE"\napi_key = "abcd1234efgh5678"\nx = 1\n'
    out, n = redact_secrets(text)
    assert n == 2
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "abcd1234efgh5678" not in out
    assert 'api_key = "***REDACTED***"' in out
    assert "x = 1" in out  # ordinary code untouched


def test_redact_private_key_block():
    text = ("-----BEGIN RSA PRIVATE KEY-----\nMIIsecretbytes\nmore\n"
            "-----END RSA PRIVATE KEY-----\n")
    out, n = redact_secrets(text)
    assert n == 1
    assert "MIIsecretbytes" not in out
    assert "***REDACTED***" in out


def test_redact_leaves_normal_code_unchanged():
    text = "def add(a, b):\n    return a + b  # token of appreciation\n"
    out, n = redact_secrets(text)
    assert n == 0
    assert out == text


# --- card size hint (#7.4) ------------------------------------------------------------------

def test_search_card_shows_size_hint():
    from pandemonium.mcp.tools import _format_results
    from pandemonium.models import SearchResult
    r = SearchResult(chunk_id="c1", path="a.py", start_line=10, end_line=42, score=0.9,
                     ref="a.py::foo", scope="symbol", summary="does foo")
    assert "~33L" in _format_results([r])


# --- init writes .pandemonium/ into .gitignore ----------------------------------------------

def test_init_adds_pandemonium_to_gitignore(tmp_path):
    from pandemonium.cli.main import init
    init(str(tmp_path))
    gi = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".pandemonium/" in gi.splitlines()
    # idempotent: a second init does not duplicate the entry
    init(str(tmp_path))
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8").count(".pandemonium/") == 1


def test_init_appends_to_existing_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    from pandemonium.cli.main import init
    init(str(tmp_path))
    lines = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "node_modules/" in lines and ".pandemonium/" in lines
