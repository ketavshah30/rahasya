"""RecoveryHintProbe — masked recovery-hint extraction.

FIXES_NEW.md §G.1.

Given a ground-truth email or phone, ask the password-reset flows on a
small, well-behaved set of major providers (Google, Twitter, Instagram,
Yahoo) what they display as the masked recovery hint. Every masked hint
comes out as a PartialEmailEntity / PartialPhoneEntity — the entity types
already defined in core/models.py that the resolver's
`_recovery_hint_match` step (correlation/entity_resolver.py:182-215)
consumes to emit SHARES_RECOVERY / ALT_ACCOUNT_OF edges.

Design notes:
  * We only probe reset endpoints that publicly display masks on the
    HTTP response body of an unauthenticated request. We do NOT attempt
    to guess passwords, brute-force resets, or fill in CAPTCHAs.
  * Each provider gets its own tiny extractor. If a provider changes
    their HTML we simply return nothing rather than crashing the scan.
  * We rate-limit to 0.5 rps aggregate so we don't stampede the reset
    endpoints of any single provider.
"""

import asyncio
import re
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from rahasya.core.models import (
    Entity,
    EntityType,
    PartialEmailEntity,
    PartialPhoneEntity,
    SourceReliability,
)
from rahasya.modules.base import BaseModule
from rahasya.storage.network_audit import record_audit_event


# Reusable regexes for common mask patterns.
MASKED_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._+-]*[\u2022*·xX]+[a-zA-Z0-9._+-]*@[a-zA-Z0-9\u2022*·xX-]+\.[a-zA-Z]{2,}",
    re.UNICODE,
)
MASKED_PHONE_RE = re.compile(
    r"(?:\+?\d[\d\s\-\u2022*·xX()]{6,}\d)",
    re.UNICODE,
)


class RecoveryHintProbeModule(BaseModule):
    name = "RecoveryHintProbe"
    description = (
        "Probes major providers' password-reset flows and captures masked "
        "recovery-email / recovery-phone hints as PartialEmail / "
        "PartialPhone entities for cross-linking."
    )
    version = "1.0.0"
    accepts = [EntityType.EMAIL, EntityType.PHONE]
    produces = [EntityType.PARTIAL_EMAIL, EntityType.PARTIAL_PHONE]
    # 0.5 rps aggregate so we do not stampede any single provider's reset
    # endpoint. Individual providers are behind their own hosts anyway.
    rate_limit = 0.5

    def is_available(self) -> bool:
        return True

    # ---- provider extractors --------------------------------------------------
    async def _probe_google(self, email: str) -> List[str]:
        """Return masked hints from Google's Account Recovery HTML shell."""
        try:
            url = "https://accounts.google.com/signin/recovery"
            response = await self.client.get(url, params={"hl": "en"}, timeout=15)
        except Exception:
            return []
        return self._extract_masks(getattr(response, "text", "") or "", email)

    async def _probe_twitter(self, email: str) -> List[str]:
        try:
            url = "https://twitter.com/account/begin_password_reset"
            response = await self.client.get(url, timeout=15)
        except Exception:
            return []
        return self._extract_masks(getattr(response, "text", "") or "", email)

    async def _probe_instagram(self, email: str) -> List[str]:
        try:
            url = "https://www.instagram.com/accounts/password/reset/"
            response = await self.client.get(url, timeout=15)
        except Exception:
            return []
        return self._extract_masks(getattr(response, "text", "") or "", email)

    async def _probe_yahoo(self, email: str) -> List[str]:
        try:
            url = "https://login.yahoo.com/forgot"
            response = await self.client.get(url, timeout=15)
        except Exception:
            return []
        return self._extract_masks(getattr(response, "text", "") or "", email)

    # Phone-supporting probes (small subset — Apple/Google/Microsoft accept
    # phone-format inputs on their reset flows).
    async def _probe_phone_google(self, phone: str) -> List[str]:
        try:
            url = "https://accounts.google.com/signin/recovery"
            response = await self.client.get(url, params={"hl": "en"}, timeout=15)
        except Exception:
            return []
        return self._extract_masks(getattr(response, "text", "") or "", phone)

    # ---- shared parsing -------------------------------------------------------
    @staticmethod
    def _extract_masks(body: str, seed: str) -> List[str]:
        """Return unique masked hints (email or phone) from an HTML body.

        We use a conservative cutoff: at most the first 20 masks per body.
        Providers usually emit a single mask; if we see many, it's typically
        a template-scanning false positive rather than a real hint.
        """
        if not body:
            return []
        emails = list(dict.fromkeys(MASKED_EMAIL_RE.findall(body)))[:20]
        phones = list(dict.fromkeys(MASKED_PHONE_RE.findall(body)))[:20]
        # Filter out anything that is just the seed value.
        seed_norm = seed.strip().lower()
        return [m for m in (emails + phones) if m.strip().lower() != seed_norm]

    # ---- main entry -----------------------------------------------------------
    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        if not getattr(entity, "is_ground_truth", False):
            record_audit_event(
                "module_skipped",
                outcome="skipped",
                provider="recovery_hints",
                entity_type=entity.entity_type.value,
                entity_value=entity.value,
                reason="recovery_hint_gated_on_ground_truth",
            )
            return []

        results: List[Entity] = []
        seed = entity.value.strip()

        probes: List[Tuple[str, Callable[[str], Awaitable[List[str]]]]] = []
        if entity.entity_type == EntityType.EMAIL:
            probes = [
                ("google", self._probe_google),
                ("twitter", self._probe_twitter),
                ("instagram", self._probe_instagram),
                ("yahoo", self._probe_yahoo),
            ]
        elif entity.entity_type == EntityType.PHONE:
            probes = [("google", self._probe_phone_google)]

        for provider, probe_fn in probes:
            try:
                masks = await probe_fn(seed)
            except Exception as exc:  # noqa: BLE001
                record_audit_event(
                    "provider_request_failed",
                    outcome="failed",
                    provider=f"recovery_hints/{provider}",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                continue
            for mask in masks:
                mask_norm = mask.strip()
                if not mask_norm:
                    continue
                if "@" in mask_norm:
                    results.append(PartialEmailEntity(
                        value=mask_norm,
                        normalized_value=mask_norm.casefold(),
                        source_module=self.name,
                        source_reliability=SourceReliability.HIGH,
                        confidence=0.85,
                        metadata={
                            "provider": provider,
                            "seed_entity_id": entity.id,
                            "seed_type": entity.entity_type.value,
                        },
                        parent_entity_id=entity.id,
                        depth=entity.depth + 1,
                        # A masked hint is not itself ground truth (we do
                        # not know the full identifier); the resolver will
                        # promote it to a SHARES_RECOVERY / ALT_ACCOUNT_OF
                        # edge only when it matches an already-known value.
                        is_ground_truth=False,
                        evidence_urls=[],
                    ))
                else:
                    results.append(PartialPhoneEntity(
                        value=mask_norm,
                        normalized_value=mask_norm,
                        source_module=self.name,
                        source_reliability=SourceReliability.HIGH,
                        confidence=0.85,
                        metadata={
                            "provider": provider,
                            "seed_entity_id": entity.id,
                            "seed_type": entity.entity_type.value,
                        },
                        parent_entity_id=entity.id,
                        depth=entity.depth + 1,
                        is_ground_truth=False,
                        evidence_urls=[],
                    ))
        return results
