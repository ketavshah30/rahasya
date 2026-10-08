"""Deterministic, explicitly synthetic presentation data. No network or model calls."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from rahasya.brain.contracts import AgentEvent, AgentReport, Assessment, BrainState, ReportClaim
from rahasya.core.models import Entity, Relationship, ScanRequest, ScanResult, ScanStats, ScanStatus
from rahasya.storage.network_audit import NetworkAuditStore

DEMO_ID = "demo-faculty-v1"
DEMO_DATE = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)

def build_demo_result():
    def entity(key, kind, value, source, review="supported", confidence=.9, **extra):
        metadata = {"synthetic_demo": True, "agent_review": review}
        metadata.update(extra.pop("metadata", {}))
        return Entity(id=key, entity_type=kind, value=value, normalized_value=value.casefold(),
                      source_module=source, confidence=confidence, metadata=metadata,
                      scan_id=DEMO_ID, discovered_at=DEMO_DATE, **extra)
    entities = [
        entity("demo-person", "person", "Alex Morgan", "seed", is_ground_truth=True),
        entity("demo-email", "email", "alex.morgan@example.org", "seed", is_ground_truth=True),
        entity("demo-handle", "username", "alex-builds", "Profile fixture", is_ground_truth=True,
               evidence_urls=["https://code.example/alex-builds"], attests_platform="demo-code"),
        entity("demo-code", "social_profile", "code.example/alex-builds", "Profile fixture",
               platform="Demo Code", url="https://code.example/alex-builds", is_ground_truth=True,
               created_at=datetime(2024, 4, 12, tzinfo=timezone.utc),
               evidence_urls=["https://code.example/alex-builds"], bio="Fictional student building open-source projects."),
        entity("demo-site", "url", "portfolio.example/alex", "Public-page fixture",
               evidence_urls=["https://portfolio.example/alex"], url="https://portfolio.example/alex"),
        entity("demo-design", "social_profile", "design.example/alex-builds", "Profile fixture",
               platform="Demo Design", url="https://design.example/alex-builds",
               created_at=datetime(2025, 2, 10, tzinfo=timezone.utc),
               evidence_urls=["https://design.example/alex-builds"]),
        entity("demo-ambiguous", "social_profile", "forum.example/alex-builds", "Handle-match fixture",
               review="uncertain", confidence=.35, platform="Demo Forum", url="https://forum.example/alex-builds",
               bio="Same handle; no independent link to the fictional subject."),
        entity("demo-breach", "breach_record", "Simulated learning-platform incident", "Breach fixture",
               breach_name="Demo Learning", source_name="Synthetic dataset", severity="medium",
               data_types_leaked=["Email", "Username"], breach_date=datetime(2025, 8, 8, tzinfo=timezone.utc),
               evidence_urls=["https://incident.example/demo-learning"]),
        entity("demo-archive", "timeline_event", "Archived portfolio snapshot", "Archive fixture",
               event_type="archive_snapshot", occurred_at=datetime(2025, 11, 16, tzinfo=timezone.utc),
               source_url="https://archive.example/portfolio",
               evidence_urls=["https://archive.example/portfolio"]),
        entity("demo-mention", "dark_web_mention", "Simulated unverified mention", "Mention fixture",
               review="uncertain", confidence=.25, context_snippet="Synthetic record for demonstrating uncertain attribution.",
               source_url="https://mentions.example/fixture", search_engine="Synthetic source"),
    ]
    links = [
        ("demo-person", "demo-email", "HAS_EMAIL", 1.0),
        ("demo-person", "demo-handle", "USES_USERNAME", .95),
        ("demo-handle", "demo-code", "HAS_PROFILE", .95),
        ("demo-code", "demo-site", "ASSOCIATED_WITH", .9),
        ("demo-handle", "demo-design", "HAS_PROFILE", .8),
        ("demo-handle", "demo-ambiguous", "ASSOCIATED_WITH", .35),
        ("demo-email", "demo-breach", "APPEARED_IN_BREACH", .9),
        ("demo-site", "demo-archive", "ASSOCIATED_WITH", .9),
        ("demo-email", "demo-mention", "MENTIONED_ON", .25),
    ]
    relationships = [Relationship(id=f"demo-edge-{i}", source_id=a, target_id=b,
                                  relationship_type=kind, confidence=confidence,
                                  source_module="Synthetic fixture", discovered_at=DEMO_DATE,
                                  metadata={"synthetic_demo": True, "observation_link": True})
                     for i, (a, b, kind, confidence) in enumerate(links)]
    decisions = [
        ("coordinator", "delegate", "identity", "Illustration: investigate the supplied identifier."),
        ("identity", "select_tool", "Profile fixture", "Illustration: choose a source that can link an identifier to a profile."),
        ("reviewer", "review", "uncertain", "Illustration: a matching username alone does not establish identity."),
        ("reporter", "summarize", "prepared", "Illustration: cite observations and separate unresolved associations."),
    ]
    brain = BrainState(
        stop_reason="synthetic_demo",
        models={role: "Illustrative role · no model invoked" for role in
                ("coordinator", "identity", "social", "exposure", "darkweb", "media_archive", "reviewer", "reporter")},
        events=[AgentEvent(timestamp=DEMO_DATE + timedelta(seconds=i), kind=kind, role=role, outcome=outcome,
                           reason=reason) for i, (role, kind, outcome, reason) in enumerate(decisions)],
        assessments=[Assessment(entity_id=e.id, disposition=e.metadata["agent_review"],
                                reason="Prepared synthetic assessment; not a live model judgment.")
                     for e in entities if e.source_module != "seed"],
        report=AgentReport(claims=[
            ReportClaim(text="The fictional subject has a linked project profile and portfolio.",
                        entity_ids=["demo-code", "demo-site"]),
            ReportClaim(text="A synthetic incident illustrates exposure of an email and username.",
                        entity_ids=["demo-breach"]),
        ], limitations=["All records and assessments are synthetic.",
                        "The forum profile and mention remain unresolved and must not be treated as identity matches."]),
        report_entity_ids=["demo-code", "demo-site", "demo-breach"],
    )
    return ScanResult(scan_id=DEMO_ID, status=ScanStatus.COMPLETED, started_at=DEMO_DATE, completed_at=DEMO_DATE,
                      request=ScanRequest(name="Alex Morgan · fictional demo", email="alex.morgan@example.org", agentic=False),
                      entities=entities, relationships=relationships, brain=brain,
                      stats=ScanStats(total_entities=len(entities), total_relationships=len(relationships),
                                      by_type=dict(Counter(e.entity_type.value for e in entities)),
                                      converged_by="synthetic_demo"))

def load_demo(store):
    """Idempotent fixture installation. It never submits a background investigation."""
    existing = store.load(DEMO_ID)
    if existing is None:
        existing = build_demo_result()
        store.save(existing)
        store.save_status(DEMO_ID, status="COMPLETED", entity_count=len(existing.entities),
                          relationship_count=len(existing.relationships), synthetic_demo=True)
        audit = NetworkAuditStore(store.root)
        for module, outcome, message in [
            ("Profile fixture", "success", "SIMULATED: profile evidence loaded from a local fixture."),
            ("Handle-match fixture", "success", "SIMULATED: an ambiguous handle is retained for review."),
            ("Unavailable-source fixture", "timeout", "SIMULATED timeout; no external request was attempted."),
        ]:
            audit.record(DEMO_ID, {"event_type": "synthetic_fixture", "source_module": module,
                                  "outcome": outcome, "message": message, "synthetic_demo": True})
    return existing
