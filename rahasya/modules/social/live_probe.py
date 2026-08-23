"""LiveProbe — annotate a SocialProfileEntity with its current HTTP status.

FIXES_NEW.md §H.3.

Produces zero new entities. Its only job is to `HEAD` (with GET/Range
fallback) each profile URL through the shared StealthHTTPClient and set

    entity.metadata["live_status"] ∈
        {"200", "404", "410", "private", "suspended", "cf-challenge", "timeout"}

This annotation is the *trigger* that gates Workstream H.1 (Wayback).
Wayback fires only when live_status is non-2xx — i.e. only when a snapshot
is actually needed to preserve evidence of a profile that no longer serves
its content. This turns Wayback from a broad, slow "index everything"
crawler into a targeted, fast recovery step.
"""

import asyncio
from typing import List, Optional

import httpx

from rahasya.core.models import Entity, EntityType, SourceReliability
from rahasya.modules.base import BaseModule
from rahasya.storage.network_audit import record_audit_event


PRIVATE_MARKERS = (
    "this account is private",
    "log in to see",
    "sign in to see",
    "protected tweets",
)
SUSPENDED_MARKERS = (
    "account suspended",
    "this account has been suspended",
    "user has been banned",
    "account has been terminated",
)
CF_MARKERS = (
    "cf-chl-",
    "attention required | cloudflare",
    "checking your browser",
)


class LiveProbeModule(BaseModule):
    name = "LiveProbe"
    description = (
        "Annotates a SocialProfileEntity with its current HTTP status "
        "(200 / 404 / 410 / private / suspended / cf-challenge / timeout). "
        "Used as the trigger gate for Wayback (§H.1)."
    )
    version = "1.0.0"
    accepts = [EntityType.SOCIAL_PROFILE]
    # LiveProbe does not emit new entities — it just annotates the input.
    produces: List[EntityType] = []
    rate_limit = 2.0

    def is_available(self) -> bool:
        return True

    async def _probe(self, url: str) -> str:
        """Return one of: 200, 404, 410, private, suspended, cf-challenge, timeout."""
        try:
            head = await self.client.head(url, timeout=10)
        except httpx.TimeoutException:
            return "timeout"
        except Exception:  # noqa: BLE001
            head = None

        status: Optional[int] = getattr(head, "status_code", None) if head is not None else None
        # Some sites reject HEAD; retry with a small ranged GET.
        if status in (None, 405, 501):
            try:
                response = await self.client.get(
                    url,
                    timeout=15,
                    headers={"Range": "bytes=0-4096"},
                )
            except httpx.TimeoutException:
                return "timeout"
            except Exception:  # noqa: BLE001
                return "timeout"
            status = getattr(response, "status_code", 0)
            body = (getattr(response, "text", "") or "").lower()
        else:
            body = ""

        if status == 404:
            return "404"
        if status == 410:
            return "410"
        if status in (401, 403):
            # Peek at the body if we have one from the GET fallback.
            if any(marker in body for marker in CF_MARKERS):
                return "cf-challenge"
            if any(marker in body for marker in PRIVATE_MARKERS):
                return "private"
            return "private"
        if 200 <= (status or 0) < 300:
            if any(marker in body for marker in SUSPENDED_MARKERS):
                return "suspended"
            if any(marker in body for marker in PRIVATE_MARKERS):
                return "private"
            if any(marker in body for marker in CF_MARKERS):
                return "cf-challenge"
            return "200"
        # 5xx or anything else we couldn't classify — treat as timeout for
        # gating purposes so Wayback is still considered.
        if status and 500 <= status < 600:
            return "timeout"
        return "timeout"

    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        if entity.entity_type != EntityType.SOCIAL_PROFILE:
            return []
        url = getattr(entity, "url", None) or entity.value
        if not url or not isinstance(url, str):
            return []

        live_status = await self._probe(url)
        # Mutate in-place: the orchestrator persists metadata on each pass.
        try:
            entity.metadata["live_status"] = live_status
            entity.metadata["live_status_source"] = self.name
        except Exception:  # noqa: BLE001
            # Entity might reject setitem in unusual configurations; ignore.
            pass

        record_audit_event(
            "live_status_probed",
            outcome="success",
            provider="live_probe",
            url=url,
            live_status=live_status,
            entity_id=entity.id,
        )
        return []
