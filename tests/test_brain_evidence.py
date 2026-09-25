import json

import pytest

from rahasya.brain.evidence import fact_view, identity_key, rank_evidence, shortlist, working_copy
from rahasya.core.models import BreachRecord, DarkWebMention, Entity, EntityType, SocialProfileEntity
from rahasya.storage.evidence_store import EvidenceStore
from rahasya.storage.scan_store import ScanStore


def candidate(index, **kwargs):
    return Entity(id=f"candidate-{index}", entity_type=EntityType.USERNAME, value=f"user{index}",
                  normalized_value=f"user{index}", source_module="GitHubEmailSearch", **kwargs)


def test_ten_thousand_results_rank_late_evidence_without_inference():
    subject = candidate("subject")
    results = [candidate(i, confidence=0.99) for i in range(9999)]
    strongest = candidate(9999, confidence=0.8, source_reliability="high", is_ground_truth=True,
                          evidence_urls=["https://example.org/evidence"])
    results.append(strongest)
    selected, counts = shortlist(results, [subject], 20, subject)
    assert len(selected) == 20 and selected[0] is strongest
    assert counts == {"received": 10000, "duplicates": 0, "already_known": 0, "excluded": 0,
                      "unique_new": 10000, "selected": 20, "deferred": 9980}
    assert rank_evidence(selected, 5, subject)[0] is strongest


def test_duplicate_and_known_records_do_not_spend_review_slots():
    first = candidate(1, confidence=0.4)
    better = candidate(1, confidence=0.9)
    other_source = candidate(1)
    other_source.source_module = "GravatarLookup"
    known = candidate(2)
    secret = Entity(entity_type="password_hash", value="secret", normalized_value="secret", source_module="test")
    selected, counts = shortlist([first, better, other_source, known, secret], [known], 20, known)
    assert better in selected and other_source in selected and first not in selected
    assert counts["duplicates"] == counts["already_known"] == counts["excluded"] == 1
    assert better.confidence == 0.9 and not better.is_ground_truth


def test_ranking_keeps_evidence_type_diversity_without_claiming_identity():
    handles = [candidate(i, is_ground_truth=True, evidence_urls=["https://example.org"]) for i in range(50)]
    breach = BreachRecord(value="dataset", normalized_value="dataset", source_module="HIBP",
                          breach_name="Dataset", source_name="HIBP", confidence=0.8)
    assert breach in rank_evidence(handles + [breach], 5)
    assert not breach.is_ground_truth


def test_unified_cards_for_different_tool_formats_omit_bulk_and_secrets():
    profile = SocialProfileEntity(
        id="profile", value="https://example.org/alex", normalized_value="https://example.org/alex",
        url="https://example.org/alex", platform="example", source_module="Maigret",
        bio="Developer " * 2000, metadata={"raw_data": {"password": "NEVER_SEND", "html": "x" * 100000}},
        evidence_urls=["https://example.org/proof?token=NEVER_SEND"],
    )
    breach = BreachRecord(value="HIBP-Test", normalized_value="hibp-test", source_module="HIBP",
                          breach_name="Test", source_name="HIBP", data_types_leaked=["Email", "Phone"],
                          metadata={"Description": "x" * 100000})
    mention = DarkWebMention(value="mention", normalized_value="mention", source_module="Ahmia",
                             source_url="http://example.onion", context_snippet="Context " * 10000,
                             search_engine="Ahmia")
    cards = [fact_view(e) for e in (profile, breach, mention)]
    assert all(set(card) == set(cards[0]) for card in cards)
    assert cards[0]["details"]["platform"] == "example"
    assert cards[1]["details"]["data_types_leaked"] == "Email, Phone"
    assert len(cards[2]["details"]["context_snippet"]) == 100
    encoded = json.dumps(cards)
    assert "NEVER_SEND" not in encoded and "raw_data" not in encoded
    assert len(encoded) < 2500


def test_url_keys_preserve_distinct_paths_queries_and_collapse_host_case():
    def url(value):
        return Entity(entity_type="url", value=value, normalized_value=value.lower(), source_module="test")

    assert identity_key(url("https://EXAMPLE.org/User#bio")) == identity_key(url("https://example.org/User"))
    assert identity_key(url("https://example.org/User")) != identity_key(url("https://example.org/user"))
    assert identity_key(url("https://example.org/?id=1")) != identity_key(url("https://example.org/?id=2"))


def test_archive_retains_unselected_records_and_working_copy_stays_small(tmp_path):
    bulk = "provider details\n" * 10000
    selected = candidate(1, metadata={"raw_data": bulk, "preserve": True, "live_status": "404"})
    deferred = candidate(2, metadata={"raw_data": bulk})
    store = EvidenceStore(tmp_path)
    refs = store.append_batch("scan", 1, "GitHubEmailSearch", "subject", [selected, deferred], [selected])
    copied = working_copy(selected, refs[id(selected)])
    assert len(copied.model_dump_json()) < 2000
    assert copied.metadata["preserve"] is True and copied.metadata["live_status"] == "404"
    assert copied.metadata["agent_review"] == "pending"
    assert selected.metadata["raw_data"] == bulk  # Original observation unchanged.
    restored = store.read("scan", copied.metadata["raw_evidence"]["offset"])
    assert restored["entity"]["metadata"]["raw_data"] == bulk
    assert len(store.path("scan").read_text().splitlines()) == 2
    second_refs = store.append_batch("scan", 2, "Other", "subject", [deferred], [deferred])
    assert store.read("scan", second_refs[id(deferred)]["offset"])["action"] == 2
    assert ScanStore(tmp_path).delete("scan")
    assert not store.path("scan").exists()


def test_archive_rejects_path_traversal_and_negative_offsets(tmp_path):
    store = EvidenceStore(tmp_path)
    with pytest.raises(ValueError):
        store.path("../outside")
    with pytest.raises(ValueError):
        store.read("scan", -1)


def test_oversized_identifiers_are_archived_only_not_truncated_into_tool_inputs():
    item = candidate(1)
    item.value = "x" * 5000
    selected, counts = shortlist([item], [], 20, candidate(0))
    assert selected == [] and counts["excluded"] == 1


def test_working_copy_preserves_typed_fields_through_snapshot(tmp_path):
    from rahasya.core.models import ScanResult

    breach = BreachRecord(value="Test", normalized_value="test", source_module="HIBP",
                          breach_name="Test", source_name="HIBP", data_types_leaked=["Email"])
    copied = working_copy(breach, {"file": "scan.evidence.jsonl", "offset": 0})
    store = ScanStore(tmp_path)
    store.save(ScanResult(scan_id="scan", entities=[copied]))
    loaded = store.load("scan").entities[0]
    assert fact_view(loaded)["details"]["data_types_leaked"] == "Email"
