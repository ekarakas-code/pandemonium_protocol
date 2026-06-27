"""Direct, re-runnable validation of the protocol's working principle (NO paid agents).

Does repo_impact return the grep-verified callers on the 5 SomeStrategyGame C++ targets?
This calls the protocol's OWN impact resolver against an INDEPENDENT grep-verified gold and
asserts an EXACT match at the fully-qualified (file, leaf) level — so a same-leaf phantom
(e.g. a transitive `FormationOverlay::draw` standing in for a real `HitFlashOverlay::draw`)
is caught, which a leaf-only check cannot see.

Anti-circular: the gold below was derived by the `verify-impact-gold` workflow — 2 independent
grep/Read-only verifiers per target, FORBIDDEN from repo_impact — which agreed 5/5. The
protocol resolves via the tree-sitter call graph: two independent methods, exact agreement.

Hard C++ cases this locks (all present in FQ_GOLD):
  - lambda/parallel_for-nested callers  : runMovementScalar (MovementSystem.cpp), drawOrderLines (SelectionOverlay.cpp)
  - UNQUALIFIED intra-namespace call    : computeForwardDesire calls it bare -> grep-by-name MISSES, resolver gets it
  - anon-namespace free-fn helpers      : submitBatched / drawScaledString / BarSink::flush / drawFacingArrow / flushBucketed
  - 3-4-way `draw` leaf collisions      : every physically-distinct draw method must appear (not just one)
  - `review/` duplicate tree            : excluded by .pandemoniumignore -> must NOT appear

CAVEAT (honest scope): these 5 targets are all non-virtual DIRECT calls (the `possible`
bucket stays empty); this validates the common-case resolution path, NOT virtual/fnptr/
template/macro indirection. And it feeds repo_impact a ref resolved straight from the graph
store, so it proves the resolver is correct GIVEN the ref — not that an agent reliably gets
that ref via repo_search (moot in the bare [NO-MCP] harness; the skill drives search->impact
in real Claude Code).
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from pandemonium.config.settings import Settings
from pandemonium.graph import GraphIndex, repo_impact, _sym_ref
from pandemonium.storage.sqlite_store import SqliteStore
from pandemonium.util import repo_id_for

REPO = "D:/SomeStrategyGame"
TASKS = json.loads(Path("D:/PandemoniumProtocol/evals/qa_impact_tasks_sg.json").read_text("utf-8"))

# Fully-qualified gold = {task_id: set of (file_basename, leaf_lower)} from the independent
# grep verifiers (verify-impact-gold workflow, anti-circular). One tuple per physically-
# distinct direct caller. file basename disambiguates the same-leaf collisions/phantoms.
FQ_GOLD = {
    "sg_compute_step": {
        ("MovementSystem.cpp", "runmovementscalar"),
        ("SimdMovement.cpp", "stepscalar"),
        ("SimdMovement.cpp", "stepsimd4"),
    },
    "sg_stamina_factor": {
        ("TeamConfig.hpp", "computeforwarddesire"),
        ("MovementSystem.cpp", "runmovementscalar"),
        ("SimdMovement.cpp", "stepscalar"),
        ("SimdMovement.cpp", "stepsimd4"),
    },
    "sg_draw_filled_rects": {
        ("FormationOverlay.cpp", "submitbatched"),
        ("HitFlashOverlay.cpp", "draw"),
        ("HudRenderer.cpp", "drawscaledstring"),
        ("MiniMap.cpp", "draw"),
        ("OrderMarkerOverlay.cpp", "draw"),
        ("PerfOverlay.cpp", "flush"),
        ("SquadNameplate.cpp", "draw"),
    },
    "sg_draw_outlined_rects": {
        ("CrowdingCueOverlay.cpp", "draw"),
        ("EntityRenderer.cpp", "draw"),
        ("FormationOverlay.cpp", "submitbatched"),
        ("SelectionOverlay.cpp", "drawrings"),
        ("SquadNameplate.cpp", "draw"),
    },
    "sg_draw_lines": {
        ("BrushPreviewOverlay.cpp", "draw"),
        ("EngagementOverlay.cpp", "draw"),
        ("FormationOverlay.cpp", "drawfacingarrow"),
        ("OrderLineOverlay.cpp", "flushbucketed"),
        ("SelectionOverlay.cpp", "draworderlines"),
        ("TargetLineOverlay.cpp", "draw"),
    },
}


def leaf(s: str) -> str:
    return re.split(r"[:.]", s.strip())[-1].lower()


def caller_key(ref: str):
    return (Path(ref.split("::", 1)[0]).name, leaf(ref))


settings = Settings.load(REPO)
store = SqliteStore(settings.sqlite_path)
store.create_schema()
repo_id = repo_id_for(settings.repo_root)
idx = GraphIndex(store, repo_id)

by_leaf = defaultdict(list)
for s in idx.by_id.values():
    by_leaf[leaf(s["qualified_name"])].append(s)

print(f"repo={REPO}  indexed symbols={len(idx.by_id)}\n")
all_pass = True
tot_gold = tot_hit = 0
for t in TASKS:
    target = t["target"]
    fq_gold = FQ_GOLD[t["id"]]
    cands = by_leaf.get(target.lower(), [])
    direct_refs, possible_refs = set(), set()
    for c in cands:
        g = repo_impact(settings, _sym_ref(c), graph=idx)
        if g:
            direct_refs |= set(g.get("direct", []))
            possible_refs |= set(g.get("possible", []))
    direct_keys = {caller_key(r) for r in direct_refs}
    possible_keys = {caller_key(r) for r in possible_refs}
    missing = fq_gold - direct_keys - possible_keys              # gold caller never surfaced
    missing_from_direct = fq_gold - direct_keys                  # gold caller not CONFIDENT
    extra = direct_keys - fq_gold                                # FQ phantom / new real caller / FP
    exact_direct = (direct_keys == fq_gold)
    # `review/` exclusion check must test a PATH SEGMENT, not a basename substring
    # (else "BrushPreviewOverlay.cpp" false-matches on the "review" inside "Preview").
    review_phantoms = {r for r in direct_refs | possible_refs
                       if any(p.lower() == "review" for p in Path(r.split("::", 1)[0]).parts)}
    ok = exact_direct and not review_phantoms
    all_pass &= ok
    tot_gold += len(fq_gold)
    tot_hit += len(fq_gold & direct_keys)
    print(f"[{'PASS' if ok else 'FAIL'}] {t['id']:22s} "
          f"direct {len(fq_gold & direct_keys)}/{len(fq_gold)} FQ"
          f"  exact_direct={exact_direct}  possible={len(possible_keys)}"
          f"  missing={sorted(missing)}  missing_from_direct_only={sorted(missing_from_direct - missing)}"
          f"  extra={sorted(extra)}  review_phantoms={sorted(review_phantoms)}")
store.close()

print(f"\n===== FQ-LEVEL VALIDATION =====")
print(f"distinct fully-qualified callers: {tot_hit}/{tot_gold} resolved as CONFIDENT direct, "
      f"exact per-task match: {'ALL PASS' if all_pass else 'FAILURES ABOVE'}")
sys.exit(0 if all_pass else 1)
