"""Context pack: structure, relevance, and token-budget discipline."""

from __future__ import annotations

from support import make_packer

from pandemonium.tokens.counter import TokenCounter


def test_pack_has_required_sections(indexed):
    packer = make_packer(indexed)
    try:
        pack = packer.build("multiply two numbers", token_budget=1500)
    finally:
        packer.close()
    assert "## Task" in pack
    assert "## Token Budget" in pack
    assert "## Suggested Inspection Order" in pack
    assert "calculator.py" in pack


def test_pack_respects_token_budget(indexed):
    budget = 1500
    packer = make_packer(indexed)
    try:
        pack = packer.build("multiply two numbers", token_budget=budget)
    finally:
        packer.close()
    used = TokenCounter("cl100k_base").count(pack)
    # greedy fill + cheap trailing sections: allow a small margin over the budget.
    assert used <= int(budget * 1.1)


def test_tiny_budget_still_returns_structure(indexed):
    packer = make_packer(indexed)
    try:
        pack = packer.build("vendor email", token_budget=120)
    finally:
        packer.close()
    assert "## Task" in pack
    assert "vendor email" in pack


def test_single_identifier_task_still_has_code_excerpt(indexed):
    """#7 regression guard: a single-identifier task short-circuits to the exact symbol,
    but the context pack must still carry a code excerpt (the short-circuit populates
    content from the symbol's head chunk, matching the full search path)."""
    packer = make_packer(indexed)
    try:
        pack = packer.build("multiply", token_budget=1500)
    finally:
        packer.close()
    assert "calculator.py" in pack
    assert "def multiply" in pack  # the body made it into the pack, not an empty fence
