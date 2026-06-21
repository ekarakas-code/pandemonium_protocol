"""Coding tasks for the Claude-Code-vs-Pandemonium A/B (evals/ab_runner.py).

Each task seeds a realistic BUG into a real, test-covered function and describes it by
SYMPTOM only (never naming the file/function) — so the agent must LOCATE the cause, which
is exactly where retrieval (Arm B) may or may not beat grep+read (Arm A). Grading is
objective: after the fix the full test suite must be green again (tests are restored from
pristine before grading, so the agent can't pass by editing tests).

A mutation = (relative_path, find, replace); `find` must be a unique substring of the
current file. The runner auto-derives each task's "target tests" = the tests that go red
after the mutation, and validates len(target) >= 1 (else the task is malformed/skipped).
"""

TASKS = [
    {
        "id": "is_test_path_wordboundary",
        "prompt": (
            "Bug report: when PandemoniumProtocol lists the tests related to a symbol, it "
            "incorrectly includes non-test source files such as `contest.cpp` and "
            "`latest.cpp` — the token 'test' is being matched as a substring instead of a "
            "whole word. Locate the cause and fix it so a file counts as a test only when "
            "'test', 'tests' or 'spec' appears as a whole word / camelCase token: "
            "`contest.cpp` and `latest.cpp` must NOT be tests, while `foo_test.cpp`, "
            "`FooTests.cs` and `bar.spec.ts` must be."
        ),
        "mutations": [(
            "pandemonium/retrieval/tests_finder.py",
            "    return bool(_name_tokens(base) & _TEST_TOKENS)",
            "    return any(tok in base.lower() for tok in _TEST_TOKENS)",
        )],
    },
    {
        "id": "fingerprint_drop_signature",
        "prompt": (
            "Bug report: PandemoniumProtocol can normally re-find a symbol by a structural "
            "fingerprint of its body even after the symbol has been renamed. Right now, "
            "renaming a symbol changes its fingerprint, so renamed symbols can no longer be "
            "re-found. Find the body-fingerprinting helper and fix it so the fingerprint is "
            "computed over the body EXCLUDING the first (signature/definition) line — "
            "renaming the symbol must not change the fingerprint."
        ),
        "mutations": [(
            "pandemonium/util.py",
            '    inner = "\\n".join(span.splitlines()[1:])  # drop the signature line',
            "    inner = span",
        )],
    },
    {
        "id": "signature_hash_whitespace",
        "prompt": (
            "Bug report: two function signatures that differ only in whitespace (e.g. "
            "`def f(a, b)` vs `def f(a,  b)`) should produce the SAME signature hash, but "
            "they currently hash differently. Find the signature-hashing helper and fix it "
            "so whitespace is collapsed/normalized before hashing."
        ),
        "mutations": [(
            "pandemonium/util.py",
            '    return sha256_text(" ".join(signature.split()))',
            "    return sha256_text(signature)",
        )],
    },
    {
        "id": "normalize_code_blanklines",
        "prompt": (
            "Bug report: PandemoniumProtocol builds a blank-line-insensitive view of a code "
            "span for structural hashing, but blank lines inside a span currently change the "
            "output (so the same code with different blank lines hashes differently). Find "
            "the normalization helper and fix it so blank lines are ignored."
        ),
        "mutations": [(
            "pandemonium/util.py",
            '    return "\\n".join(ln for ln in lines if ln)',
            '    return "\\n".join(lines)',
        )],
    },
    {
        "id": "confidence_overfire",
        "prompt": (
            "Bug report: the retrieval low-confidence detector is firing too often — it "
            "marks result sets as LOW confidence even when they look fine. It must flag LOW "
            "only when BOTH hold: the top results cluster on a single symbol name AND at "
            "least one query term is covered by none of the top results. Find the detector "
            "and fix the over-eager condition."
        ),
        "mutations": [(
            "pandemonium/retrieval/confidence.py",
            "    if name_cluster and missing:",
            "    if name_cluster or missing:",
        )],
    },
    {
        "id": "confidence_short_token_noise",
        "prompt": (
            "Bug report: the retrieval term-coverage logic is polluted by tiny noise tokens "
            "(1–2 characters such as 'id' or 'x'). Word-parts shorter than 3 characters "
            "should be dropped when extracting the terms a query or result carries. Find the "
            "term-extraction helper and fix the length threshold."
        ),
        "mutations": [(
            "pandemonium/retrieval/confidence.py",
            "            if len(w) >= 3:",
            "            if len(w) >= 1:",
        )],
    },
]

# Arm A gets a short, neutral coding-guidance prompt so it isn't penalized by the mere
# ABSENCE of a system prompt. Arm B's prompt is the ACTUAL pandemonium skill text (loaded
# from .claude/skills/pandemonium/SKILL.md by the runner) — not a hand-optimized one — so
# we test the protocol, not our prompt-writing.
ARMA_SYSTEM = (
    "You are fixing a bug in a Python code repository. Investigate efficiently using the "
    "file tools available to you (Read, Grep, Glob), locate the root cause, make the "
    "minimal correct edit, and run the relevant tests to confirm. Do not edit test files."
)
