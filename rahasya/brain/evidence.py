"""Deterministic evidence reduction. No model calls or provider I/O."""

from heapq import nlargest
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict

from rahasya.core.models import Entity, EntityType
from rahasya.storage.network_audit import redact_text_urls, redact_url


# The modules already adapt provider JSON/CSV/HTML into Entity subclasses.
# Only these typed fields become model context; arbitrary metadata never does.
DETAIL_FIELDS = {
    EntityType.SOCIAL_PROFILE: ("platform", "bio", "is_verified"),
    EntityType.BREACH_RECORD: ("breach_name", "data_types_leaked", "severity"),
    EntityType.DARK_WEB_MENTION: ("context_snippet", "search_engine"),
    EntityType.PHOTO: ("phash", "gps_coords"),
    EntityType.LOCATION: ("city", "country", "source_type"),
    EntityType.TIMELINE_EVENT: ("event_type", "occurred_at"),
}
WORKING_FIELDS = {
    "url", "platform", "file_path", "handle", "address", "domain", "number",
    "source_url", "source_name", "breach_name", "breach_date", "data_types_leaked",
    "severity", "context_snippet", "search_engine", "bio", "is_verified",
    "phash", "gps_coords", "latitude", "longitude", "city", "country", "source_type",
    "name", "event_type", "occurred_at",
}


class EvidenceCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: str
    value: str
    source: str
    confidence: float
    source_attested: bool
    evidence_urls: list[str]
    live_status: str
    details: dict[str, str]


def short(value, limit=100):
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(item)[:40] for item in value[:5])
    # Bound work on huge text before URL parsing/redaction; cut only afterwards
    # for ordinary fields, so query credentials are redacted before display.
    return redact_text_urls(str(value)[:2048])[:limit]


def fact_view(entity):
    details = {}
    for field in DETAIL_FIELDS.get(entity.entity_type, ()):
        value = getattr(entity, field, None)
        if value is not None and value != "":
            details[field] = short(value)
    return EvidenceCard(
        id=entity.id, type=entity.entity_type.value,
        value="[REDACTED]" if entity.entity_type == EntityType.PASSWORD_HASH else short(entity.value, 180),
        source=entity.source_module[:80], confidence=entity.confidence,
        source_attested=entity.is_ground_truth,
        evidence_urls=[redact_url(url[:2048])[:250] for url in entity.evidence_urls[:2]],
        live_status=short(entity.metadata.get("live_status", ""), 40), details=details,
    ).model_dump()


def identity_key(entity):
    value = entity.normalized_value.strip()
    # Preserve case-sensitive paths and queries. Prefer the original URL because
    # some legacy modules casefold normalized_value indiscriminately.
    if entity.entity_type in {EntityType.URL, EntityType.SOCIAL_PROFILE}:
        try:
            parts = urlsplit(entity.value.strip())
            if parts.scheme and parts.netloc:
                value = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))
        except ValueError:
            pass
    return entity.entity_type.value, value


def priority(entity, subject=None):
    # This is ordering, never a probability of identity. No name substring match.
    exact_identifier = bool(subject and entity.entity_type in {
        EntityType.EMAIL, EntityType.PHONE,
    } and identity_key(entity) == identity_key(subject))
    return (
        entity.is_ground_truth and bool(entity.evidence_urls), exact_identifier,
        {"high": 3, "medium": 2, "low": 1, "unverified": 0}[entity.source_reliability.value],
        bool(entity.evidence_urls), entity.confidence,
    )


def rank_evidence(entities, limit, subject=None):
    """Reserve coverage for different evidence types, then fill by priority."""
    groups = {}
    for entity in entities:
        if entity.entity_type != EntityType.PASSWORD_HASH:
            groups.setdefault(entity.entity_type, []).append(entity)
    heads = [max(group, key=lambda e: priority(e, subject)) for group in groups.values()]
    chosen = nlargest(limit, heads, key=lambda e: priority(e, subject))
    chosen_ids = {id(e) for e in chosen}
    remaining = (e for group in groups.values() for e in group if id(e) not in chosen_ids)
    chosen.extend(nlargest(max(0, limit - len(chosen)), remaining, key=lambda e: priority(e, subject)))
    return chosen


def shortlist(results, known, limit, subject):
    existing = {identity_key(e) for e in known}
    unique = {}
    duplicates = already_known = excluded = 0
    for entity in results:
        if entity.entity_type == EntityType.PASSWORD_HASH or any(
            len(value) > limit for value, limit in (
                (entity.id, 128), (entity.value, 4096), (entity.normalized_value, 4096),
                (entity.source_module, 80),
            )
        ):
            excluded += 1
            continue
        key = identity_key(entity)
        if key in existing:
            already_known += 1
            continue
        # Keep source attribution separate; full variants are also in the archive.
        key = (*key, entity.source_module)
        if key in unique:
            duplicates += 1
            if priority(entity, subject) > priority(unique[key], subject):
                unique[key] = entity
        else:
            unique[key] = entity
    selected = rank_evidence(unique.values(), limit, subject)
    return selected, {
        "received": len(results), "duplicates": duplicates, "already_known": already_known,
        "excluded": excluded, "unique_new": len(unique), "selected": len(selected),
        "deferred": len(unique) - len(selected),
    }


def working_copy(entity, raw_ref):
    """Keep tool inputs intact; move bulk metadata out of repeatedly saved state."""
    fields = {name: getattr(entity, name) for name in Entity.model_fields if name != "metadata"}
    fields["evidence_urls"] = entity.evidence_urls[:5]
    # Identity values and operational URLs are not truncated: that could execute
    # a later tool against the wrong identifier. Model cards truncate separately.
    for name in WORKING_FIELDS:
        value = getattr(entity, name, None)
        if value is not None:
            if name in {"bio", "context_snippet"}:
                value = str(value)[:500]
            elif isinstance(value, list):
                value = [str(item)[:100] for item in value[:10]]
            fields[name] = value
    fields["metadata"] = {
        key: entity.metadata[key][:80] if isinstance(entity.metadata[key], str) else entity.metadata[key]
        for key in ("live_status", "live_status_source", "preserve")
        if key in entity.metadata and isinstance(entity.metadata[key], (bool, int, str))
    }
    fields["metadata"].update(agent_review="pending", raw_evidence=raw_ref)
    return Entity(**fields)
