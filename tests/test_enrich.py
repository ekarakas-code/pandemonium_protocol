"""Phase 3: enrichment provider (override + heuristic fallback)."""

from __future__ import annotations

from pandemonium.enrich import CacheEnricher, Enricher, load_enricher
from pandemonium.tags import TAG_FIELDS


def test_base_enricher_is_no_override():
    assert Enricher().get("a.py::C.m") is None


def test_cache_enricher_override_and_miss():
    cache = {"a.py::C.m": {"summary": "Does X.", "search_terms": ["foo"],
                           "side_effects": ["network"]}}
    enricher = CacheEnricher(cache)
    hit = enricher.get("a.py::C.m")
    assert hit is not None
    assert hit.summary == "Does X."
    assert hit.tags["search_terms"] == ["foo"]
    assert hit.tags["side_effects"] == ["network"]
    assert set(hit.tags) == set(TAG_FIELDS)  # all 6 fields normalized in
    assert enricher.get("missing::ref") is None  # miss -> None -> heuristic stands


def test_load_enricher_defaults_to_heuristic(settings):
    assert type(load_enricher(settings)).__name__ == "Enricher"


def test_load_enricher_cache_missing_file_falls_back(settings):
    settings.data["enrichment"]["provider"] = "cache"
    settings.data["enrichment"]["cache_path"] = ".pandemonium/does_not_exist.json"
    assert type(load_enricher(settings)).__name__ == "Enricher"
