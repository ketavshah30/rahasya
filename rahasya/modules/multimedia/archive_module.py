"""WaybackMachine — evidence-preservation of dead/private social profiles.

FIXES_NEW.md §H.1.

Before this rewrite, ArchiveModule accepted every URL/SOCIAL_PROFILE and
asked CDX for the last 20 snapshots per profile — a de-facto crawler. On a
target with 30 live profiles that's 600+ CDX requests, plus the
availability endpoint's slow responses, which was the source of the
"Wayback takes forever / too many failures" complaint.

The new contract:

  * accepts: SOCIAL_PROFILE, DOMAIN (URL dropped)
  * Trigger rule: only fire if the entity's metadata["live_status"] is in
    {"404", "410", "private", "suspended"} OR the entity was explicitly
    flagged with metadata["preserve"] == True by another module.
    LiveProbe (§H.3) is the module that sets live_status.
  * Snapshot budget: oldest 2 + newest 2 = at most 4 CDX-derived rows per
    input. No firehose.
  * rate_limit = 0.5 (one request every 2s to archive.org, as they ask).
  * http_max_retries = 2 (was 5 — three of those retries were wasted on
    every temporary Archive.org degradation).
"""

from datetime import datetime, timezone
from typing import Any, List
from urllib.parse import quote_plus

from rahasya.core.models import Entity, EntityType, SourceReliability, TimelineEvent
from rahasya.modules.base import BaseModule
from rahasya.storage.network_audit import record_audit_event


class ArchiveModule(BaseModule):
    name = "WaybackMachine"
    description = "Evidence-preserve dead/private profiles via Internet Archive"
    version = "2.0.0"
    accepts = [EntityType.SOCIAL_PROFILE, EntityType.DOMAIN]
    produces = [EntityType.URL, EntityType.TIMELINE_EVENT]

    # Archive.org explicitly asks for polite pacing.
    rate_limit = 0.5
    # Cut retry budget from 5 to 2 (see docstring).
    http_max_retries = 2

    REQUEST_HEADERS = {
        "User-Agent": "Rahasya OSINT Platform (contact: maintainer@rahasya.local)",
        "Accept": "application/json",
    }

    # live_status values that mean "content isn't reachable live — snapshot
    # is now the only way to preserve/inspect what used to be there."
    TRIGGER_STATUSES = {"404", "410", "private", "suspended"}

    def is_available(self) -> bool:
        return True

    @staticmethod
    def _snapshot_selection(rows: List[dict]) -> List[dict]:
        """Pick oldest 2 and newest 2 CDX rows (up to 4 total).

        CDX returns oldest-first by default; we clamp to at most 4 entries
        without hitting the network again.
        """
        if not rows:
            return []
        if len(rows) <= 4:
            return rows
        return rows[:2] + rows[-2:]

    def _should_fire(self, entity: Entity) -> bool:
        # Explicit preservation flag from another module wins.
        metadata = getattr(entity, "metadata", {}) or {}
        if metadata.get("preserve") is True:
            return True
        # For DOMAIN entities we always allow one probe — a target-owned
        # domain is worth preserving regardless of live status.
        if entity.entity_type == EntityType.DOMAIN:
            return True
        # For SOCIAL_PROFILE entities we require live_status to indicate the
        # profile is no longer accessible.
        live_status = str(metadata.get("live_status", "")).lower()
        return live_status in self.TRIGGER_STATUSES

    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        if entity.entity_type not in {EntityType.SOCIAL_PROFILE, EntityType.DOMAIN}:
            return []

        if not self._should_fire(entity):
            record_audit_event(
                "module_skipped",
                outcome="skipped",
                provider="wayback",
                entity_type=entity.entity_type.value,
                entity_value=entity.value,
                reason="live_status_not_gated_for_preservation",
                message=(
                    "Wayback only fires when the source profile is 404/410/"
                    "private/suspended, or when a peer module has flagged "
                    "the entity for preservation."
                ),
            )
            return []

        url = entity.value
        encoded_url = quote_plus(url)
        results: List[Entity] = []

        # 1) Availability endpoint — one call, gives the closest snapshot.
        try:
            avail_url = f"https://archive.org/wayback/available?url={encoded_url}"
            avail_resp = await self.client.get(avail_url, headers=self.REQUEST_HEADERS)
            if avail_resp.status_code == 200:
                data = avail_resp.json()
                closest = (data.get("archived_snapshots") or {}).get("closest")
                if closest and closest.get("available"):
                    archive_url = closest.get("url")
                    timestamp = closest.get("timestamp")
                    if archive_url:
                        results.append(Entity(
                            entity_type=EntityType.URL,
                            value=archive_url,
                            normalized_value=archive_url.lower().strip(),
                            source_module=self.name,
                            source_reliability=SourceReliability.HIGH,
                            confidence=1.0,
                            metadata={
                                "timestamp": timestamp,
                                "type": "closest_snapshot",
                                "preserved_for": url,
                                "trigger_live_status": (entity.metadata or {}).get("live_status"),
                            },
                            parent_entity_id=entity.id,
                            depth=entity.depth + 1,
                        ))
        except Exception as exc:
            record_audit_event(
                "provider_request_failed",
                outcome="failed",
                provider="wayback",
                url=avail_url,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self.logger.error(f"Archive availability failed: {exc}")

        # 2) CDX endpoint — oldest 2 + newest 2 snapshots. No firehose.
        try:
            cdx_url = (
                f"https://web.archive.org/cdx/search/cdx"
                f"?url={encoded_url}&output=json&limit=200"
            )
            cdx_resp = await self.client.get(cdx_url, headers=self.REQUEST_HEADERS)
            if cdx_resp.status_code == 200:
                data = cdx_resp.json()
                if isinstance(data, list) and len(data) > 1:
                    headers = data[0]
                    rows = [dict(zip(headers, row)) for row in data[1:]]
                    for item in self._snapshot_selection(rows):
                        timestamp = str(item.get("timestamp") or "")
                        status = item.get("statuscode", "")
                        if not timestamp:
                            continue
                        archive_url = f"https://web.archive.org/web/{timestamp}/{url}"
                        results.append(Entity(
                            entity_type=EntityType.URL,
                            value=archive_url,
                            normalized_value=archive_url.lower().strip(),
                            source_module=self.name,
                            source_reliability=SourceReliability.HIGH,
                            confidence=0.9,
                            metadata={
                                "timestamp": timestamp,
                                "status_code": status,
                                "type": "cdx_snapshot",
                                "preserved_for": url,
                                "trigger_live_status": (entity.metadata or {}).get("live_status"),
                            },
                            parent_entity_id=entity.id,
                            depth=entity.depth + 1,
                        ))
                        try:
                            occurred_at = datetime.strptime(
                                timestamp[:14], "%Y%m%d%H%M%S"
                            ).replace(tzinfo=timezone.utc)
                        except ValueError:
                            continue
                        results.append(TimelineEvent(
                            value=f"Archive snapshot preserved: {url}",
                            normalized_value=f"archive:{url.casefold()}:{timestamp}",
                            source_module=self.name,
                            source_reliability=SourceReliability.HIGH,
                            confidence=0.95,
                            parent_entity_id=entity.id,
                            depth=entity.depth + 1,
                            event_type="preserved_snapshot",
                            occurred_at=occurred_at,
                            subject_entity_id=entity.id,
                            source_url=archive_url,
                            metadata={
                                "timestamp": timestamp,
                                "url": archive_url,
                                "subject": url,
                            },
                        ))
        except Exception as exc:
            record_audit_event(
                "provider_request_failed",
                outcome="failed",
                provider="wayback",
                url=cdx_url,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self.logger.error(f"CDX API failed: {exc}")

        return results
