"""HunterIOReverse — email ↔ company domain + name via hunter.io.

FIXES_NEW.md §E.5 (optional keyed module).

When API_KEYS__HUNTER is set, this module pivots an email into:
  * organization / company domain
  * verified full name (when present)
  * additional co-worker candidates (feeds §G related-person expansion)

Without the key, is_available() returns False and the module self-skips.
"""

from typing import Any, Dict, List, Optional

from rahasya.core.models import (
    CompanyEntity,
    Entity,
    EntityType,
    PersonEntity,
    SourceReliability,
)
from rahasya.modules.base import BaseModule
from rahasya.storage.network_audit import record_audit_event


class HunterIOReverseModule(BaseModule):
    name = "HunterIOReverse"
    description = "Email → company / name / co-worker candidates via Hunter.io."
    version = "1.0.0"
    accepts = [EntityType.EMAIL]
    produces = [EntityType.COMPANY, EntityType.PERSON, EntityType.EMAIL]
    requires_api_key = True
    rate_limit = 1.0

    BASE_URL = "https://api.hunter.io/v2"

    def is_available(self) -> bool:
        if not super().is_available():
            return False
        return bool(getattr(self.config.api_keys, "hunter", None))

    def _api_key(self) -> Optional[str]:
        return getattr(self.config.api_keys, "hunter", None)

    async def _get(self, path: str, params: Dict[str, str]) -> Optional[Dict[str, Any]]:
        key = self._api_key()
        if not key:
            return None
        full_params = dict(params)
        full_params["api_key"] = key
        url = f"{self.BASE_URL}{path}"
        try:
            response = await self.client.get(url, params=full_params, timeout=15)
        except Exception as exc:  # noqa: BLE001
            record_audit_event(
                "provider_request_failed",
                outcome="failed",
                provider="hunter",
                url=url,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return None
        status = getattr(response, "status_code", None)
        record_audit_event(
            "provider_request_completed",
            outcome="success" if status == 200 else "failed",
            provider="hunter",
            url=url,
            status_code=status,
        )
        if status != 200:
            return None
        try:
            return response.json()
        except Exception:  # noqa: BLE001
            return None

    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        if entity.entity_type != EntityType.EMAIL:
            return []
        if not getattr(entity, "is_ground_truth", False):
            record_audit_event(
                "module_skipped",
                outcome="skipped",
                provider="hunter",
                entity_type=entity.entity_type.value,
                entity_value=entity.value,
                reason="candidate_email_not_corroborated",
            )
            return []

        email = entity.value.strip().lower()
        results: List[Entity] = []

        # 1) email-finder — pivot email → org + verified name.
        finder = await self._get("/email-verifier", {"email": email})
        data = (finder or {}).get("data") if isinstance(finder, dict) else None
        if isinstance(data, dict):
            org = data.get("organization") if isinstance(data.get("organization"), str) else None
            domain = data.get("domain") if isinstance(data.get("domain"), str) else None
            first = str(data.get("first_name") or "").strip()
            last = str(data.get("last_name") or "").strip()
            full = f"{first} {last}".strip()

            if org or domain:
                company_value = org or domain or ""
                results.append(CompanyEntity(
                    value=company_value,
                    normalized_value=company_value.casefold(),
                    source_module=self.name,
                    source_reliability=SourceReliability.HIGH,
                    confidence=0.9,
                    metadata={
                        "hunter_domain": domain,
                        "attested_via": "hunter_email_verifier",
                    },
                    parent_entity_id=entity.id,
                    depth=entity.depth + 1,
                    name=org,
                    domain=domain,
                    is_ground_truth=True,
                    evidence_urls=[f"{self.BASE_URL}/email-verifier"],
                ))

            if full:
                results.append(PersonEntity(
                    value=full,
                    normalized_value=full.casefold(),
                    source_module=self.name,
                    source_reliability=SourceReliability.HIGH,
                    confidence=0.85,
                    metadata={
                        "attested_via": "hunter_email_verifier",
                        "queried_email": email,
                    },
                    parent_entity_id=entity.id,
                    depth=entity.depth + 1,
                    name=full,
                    is_ground_truth=True,
                    evidence_urls=[f"{self.BASE_URL}/email-verifier"],
                ))

        # 2) domain-search — surface co-workers as related-person candidates.
        domain_part = email.split("@", 1)[1] if "@" in email else ""
        if domain_part:
            people = await self._get(
                "/domain-search",
                {"domain": domain_part, "limit": "10"},
            )
            emails_data = (people or {}).get("data") if isinstance(people, dict) else None
            hits = emails_data.get("emails") if isinstance(emails_data, dict) else None
            if isinstance(hits, list):
                for hit in hits:
                    if not isinstance(hit, dict):
                        continue
                    co_email = str(hit.get("value") or "").strip().lower()
                    co_first = str(hit.get("first_name") or "").strip()
                    co_last = str(hit.get("last_name") or "").strip()
                    co_full = f"{co_first} {co_last}".strip()
                    if not co_full or co_email == email:
                        continue
                    # Co-worker candidates are NOT ground truth for the
                    # primary target — they are hypotheses that feed the
                    # related-person selector (§G.2).
                    results.append(PersonEntity(
                        value=co_full,
                        normalized_value=co_full.casefold(),
                        source_module=self.name,
                        source_reliability=SourceReliability.MEDIUM,
                        confidence=0.55,
                        metadata={
                            "attested_via": "hunter_domain_search",
                            "co_worker_of": email,
                            "co_worker_email": co_email,
                            "domain": domain_part,
                        },
                        parent_entity_id=entity.id,
                        depth=entity.depth + 1,
                        name=co_full,
                        is_ground_truth=False,
                        evidence_urls=[f"{self.BASE_URL}/domain-search"],
                    ))

        return results
