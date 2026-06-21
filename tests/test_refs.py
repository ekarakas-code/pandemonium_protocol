"""Phase 1: stable refs + repo_get resolver (incl. edit-stability)."""

from __future__ import annotations

from support import reindex

from pandemonium import refs, service


def test_ref_roundtrip():
    assert refs.build_ref("a.py", "symbol", "C.m") == "a.py::C.m"
    assert refs.build_ref("a.py", "file") == "a.py"
    assert refs.build_ref("a.py", "code", start_line=10, end_line=20) == "a.py:10-20"
    assert refs.parse_ref("a.py::C.m") == ("a.py", "C.m", None)
    assert refs.parse_ref("a.py:10-20") == ("a.py", None, (10, 20))
    assert refs.parse_ref("a.py") == ("a.py", None, None)


def test_get_exact_resolves_symbol(indexed):
    r = service.get(indexed, "pkg/calculator.py::multiply", expand="exact")
    assert r is not None and not r.stale
    assert r.scope == "symbol"
    assert "def multiply" in r.code


def test_get_is_edit_stable(settings):
    """A ref obtained before an edit must still resolve after the lines shift —
    without re-indexing. This is the difference between a toy and a real navigator."""
    reindex(settings, incremental=False)
    f = settings.repo_root / "pkg" / "calculator.py"
    before = service.get(settings, "pkg/calculator.py::multiply", expand="exact")
    # Prepend 4 lines to shift every symbol down; do NOT re-index.
    f.write_text("# h\n# h\n# h\n\n" + f.read_text(encoding="utf-8"), encoding="utf-8")
    after = service.get(settings, "pkg/calculator.py::multiply", expand="exact")
    assert after is not None and not after.stale
    assert after.start_line == before.start_line + 4  # re-found at the new location
    assert "def multiply" in after.code


def test_expand_modes(indexed):
    exact = service.get(indexed, "pkg/calculator.py::Calculator.add", expand="exact")
    whole = service.get(indexed, "pkg/calculator.py::Calculator.add", expand="file")
    parent = service.get(indexed, "pkg/calculator.py::Calculator.add", expand="parent")
    assert exact and whole and parent
    assert "def add" in exact.code
    assert (whole.end_line - whole.start_line) >= (exact.end_line - exact.start_line)
    assert "class Calculator" in parent.code  # parent of a method is its class


def test_unresolvable_symbol_returns_none(indexed):
    assert service.get(indexed, "pkg/calculator.py::DoesNotExist", expand="exact") is None


def test_view_signature_returns_declaration_head_only(tmp_path):
    """#2: view=signature returns just the def line(s), not the whole body — the
    token-efficiency lever (a body is often 10x the signature an agent needs)."""
    (tmp_path / "m.py").write_text(
        "def compute(a, b):\n    x = a + b\n    y = x * 2\n    return y\n", encoding="utf-8")
    full = refs.resolve(tmp_path, "m.py::compute", view="full")
    sig = refs.resolve(tmp_path, "m.py::compute", view="signature")
    assert "return y" in full.code                 # full body present
    assert sig.code.strip() == "def compute(a, b):"  # signature only (def line ends ':')
    assert sig.start_line == 1 and sig.end_line == 1
    assert sig.view == "signature"
    assert "return y" not in sig.code


def test_view_signature_cpp_stops_at_brace(tmp_path):
    """For brace languages the declaration head runs through the first '{'."""
    (tmp_path / "calc.cpp").write_text(
        "int add(int a, int b) {\n    return a + b;\n}\n", encoding="utf-8")
    sig = refs.resolve(tmp_path, "calc.cpp::add", view="signature")
    assert sig is not None
    assert "int add(int a, int b) {" in sig.code
    assert "return a + b" not in sig.code


def test_view_head_and_lines(tmp_path):
    """head:N returns the first N lines of the span; lines:a-b clamps within the span."""
    body = "def f():\n" + "".join(f"    s{i} = {i}\n" for i in range(1, 8)) + "    return 0\n"
    (tmp_path / "m.py").write_text(body, encoding="utf-8")
    head = refs.resolve(tmp_path, "m.py::f", view="head:3")
    assert head.start_line == 1 and head.end_line == 3
    assert head.code.count("\n") == 2  # exactly 3 lines

    sub = refs.resolve(tmp_path, "m.py::f", view="lines:2-4")
    assert sub.start_line == 2 and sub.end_line == 4

    # lines outside the span are clamped to it (never escapes the symbol).
    clamped = refs.resolve(tmp_path, "m.py::f", view="lines:1-999")
    full = refs.resolve(tmp_path, "m.py::f", view="full")
    assert clamped.end_line == full.end_line


def test_view_unknown_falls_back_to_full(indexed):
    """An unrecognized view is a no-op (full span) — never a surprise truncation."""
    full = service.get(indexed, "pkg/calculator.py::multiply", view="full")
    weird = service.get(indexed, "pkg/calculator.py::multiply", view="bogus")
    assert weird is not None
    assert weird.code == full.code


def test_get_class_with_members_not_stale(indexed):
    """Regression: a class chunk stores only its header span; staleness must compare the
    symbol's FULL span, not the header-chunk hash — else every class reads as stale."""
    r = service.get(indexed, "pkg/calculator.py::Calculator", expand="exact")
    assert r is not None and not r.stale
    assert "class Calculator" in r.code


def test_resolve_disambiguates_same_qname_by_signature(tmp_path):
    """Two nested defs share the qname outer.helper; signature_hash picks the right one,
    a name-only resolve flags ambiguity instead of silently grabbing the first."""
    from pandemonium.util import signature_hash_for
    (tmp_path / "m.py").write_text(
        "def outer():\n"
        "    def helper(a):\n"
        "        return a\n"
        "    def helper(a, b):\n"
        "        return a + b\n",
        encoding="utf-8")
    r = refs.resolve(tmp_path, "m.py::outer.helper",
                     signature_hash=signature_hash_for("def helper(a, b):"))
    assert r is not None and not r.ambiguous and r.resolved_by == "signature"
    assert "a + b" in r.code  # picked the 2-arg variant by signature

    amb = refs.resolve(tmp_path, "m.py::outer.helper", fallback_lines=(2, 3))
    assert amb is not None and amb.ambiguous and amb.resolved_by == "qname"


def test_resolve_survives_rename_via_fingerprint(tmp_path):
    """A stale path::OldName ref still resolves after the symbol is renamed (same body)."""
    from pandemonium.util import fingerprint_for
    f = tmp_path / "m.py"
    span = ("def alpha():\n    total = 0\n    for i in range(3):\n"
            "        total += i\n    return total")
    f.write_text(span + "\n", encoding="utf-8")
    fp = fingerprint_for(span)
    f.write_text((span + "\n").replace("def alpha():", "def compute_total():"),
                 encoding="utf-8")  # rename, identical body, no reindex
    r = refs.resolve(tmp_path, "m.py::alpha", fingerprint=fp)
    assert r is not None and r.resolved_by == "fingerprint"
    assert "return total" in r.code


def test_content_hash_detects_body_change(tmp_path):
    """With the indexed content_hash, resolve() reports stale on a body change — not just
    when re-find by name fails."""
    from pandemonium.util import sha256_text
    f = tmp_path / "m.py"
    f.write_text("def f():\n    return 1\n", encoding="utf-8")
    indexed_hash = sha256_text("def f():\n    return 1")
    f.write_text("def f():\n    return 999\n", encoding="utf-8")  # body changed, no reindex
    r = refs.resolve(tmp_path, "m.py::f", content_hash=indexed_hash)
    assert r is not None and r.stale
    f.write_text("def f():\n    return 1\n", encoding="utf-8")  # restore
    r2 = refs.resolve(tmp_path, "m.py::f", content_hash=indexed_hash)
    assert r2 is not None and not r2.stale
