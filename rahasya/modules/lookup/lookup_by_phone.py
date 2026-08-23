"""LookupByPhone — phone → account existence probing via ignorant.

FIXES_NEW.md §E.2.

ignorant is the phone counterpart to holehe (same author, same shell-out
shape). Given a country code and local number it queries a small set of
sites' registration / reset endpoints that expose "yes there's an account
with this phone" tells.

We also emit a LocationEntity carrying phonenumbers-derived metadata
(carrier, country) whenever `phonenumbers` is installed — a low-cost,
zero-network way to enrich a phone seed.
"""

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from rahasya.core.models import (
    Entity,
    EntityType,
    LocationEntity,
    SocialProfileEntity,
    SourceReliability,
)
from rahasya.modules.base import BaseModule
from rahasya.storage.network_audit import record_audit_event

try:  # optional dep
    import phonenumbers  # type: ignore
    from phonenumbers import carrier as _pn_carrier  # type: ignore
    from phonenumbers import geocoder as _pn_geocoder  # type: ignore
except Exception:  # noqa: BLE001
    phonenumbers = None  # type: ignore
    _pn_carrier = None  # type: ignore
    _pn_geocoder = None  # type: ignore


def _split_phone(raw: str) -> Optional[Tuple[str, str]]:
    """Split '+CC nnnn...' into (country_code, local_number).

    Returns None if we cannot parse. Prefers `phonenumbers` if available;
    falls back to a naive '+' regex otherwise.
    """
    stripped = re.sub(r"[^\d+]", "", raw or "")
    if not stripped:
        return None
    if phonenumbers is not None:
        try:
            parsed = phonenumbers.parse(stripped, None)
            if phonenumbers.is_valid_number(parsed):
                return (str(parsed.country_code), str(parsed.national_number))
        except Exception:  # noqa: BLE001
            pass
    match = re.match(r"^\+(\d{1,3})(\d{6,})$", stripped)
    if match:
        return (match.group(1), match.group(2))
    return None


class LookupByPhoneModule(BaseModule):
    name = "LookupByPhone"
    description = "Reverse-lookup: which platforms have this phone registered? Shell-out to ignorant."
    version = "1.0.0"
    accepts = [EntityType.PHONE]
    produces = [EntityType.SOCIAL_PROFILE, EntityType.LOCATION]
    rate_limit = 2.0

    @staticmethod
    def _binary() -> Optional[str]:
        return shutil.which("ignorant")

    def is_available(self) -> bool:
        return self._binary() is not None

    @staticmethod
    def _command(country_code: str, local_number: str) -> List[str]:
        return [
            "ignorant",
            country_code,
            local_number,
            "--no-color",
        ]

    @staticmethod
    def _parse_stdout(stdout: str) -> List[Dict[str, Any]]:
        """Parse ignorant stdout — mirrors holehe's `[+] name` lines."""
        hits: List[Dict[str, Any]] = []
        for raw in stdout.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                obj = None
            if isinstance(obj, dict) and obj.get("exists") is True:
                hits.append(obj)
                continue
            match = re.match(r"^\[\+\]\s+([a-zA-Z0-9._-]+\.[a-zA-Z]{2,})", line)
            if match:
                hits.append({
                    "name": match.group(1),
                    "domain": match.group(1),
                    "exists": True,
                })
        return hits

    def _phone_metadata(self, raw: str) -> Optional[LocationEntity]:
        if phonenumbers is None:
            return None
        try:
            parsed = phonenumbers.parse(re.sub(r"[^\d+]", "", raw), None)
        except Exception:  # noqa: BLE001
            return None
        if not phonenumbers.is_valid_number(parsed):
            return None
        country = _pn_geocoder.description_for_number(parsed, "en") if _pn_geocoder else None
        carrier_name = _pn_carrier.name_for_number(parsed, "en") if _pn_carrier else None
        meta_value = f"{country or 'unknown-country'}::{carrier_name or 'unknown-carrier'}"
        return LocationEntity(
            value=meta_value,
            normalized_value=meta_value.casefold(),
            source_module=self.name,
            source_reliability=SourceReliability.HIGH,
            confidence=0.9,
            metadata={
                "carrier": carrier_name,
                "country": country,
                "raw_phone": raw,
            },
            depth=1,
            country=country,
            source_type="phone_metadata",
            is_ground_truth=True,
            evidence_urls=[],
        )

    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        if entity.entity_type != EntityType.PHONE:
            return []
        if not getattr(entity, "is_ground_truth", False):
            record_audit_event(
                "module_skipped",
                outcome="skipped",
                provider="ignorant",
                entity_type=entity.entity_type.value,
                entity_value=entity.value,
                reason="candidate_phone_not_corroborated",
            )
            return []

        split = _split_phone(entity.value)
        if not split:
            record_audit_event(
                "module_skipped",
                outcome="skipped",
                provider="ignorant",
                entity_type=entity.entity_type.value,
                entity_value=entity.value,
                reason="phone_parse_failed",
            )
            return []
        country_code, local_number = split

        results: List[Entity] = []
        loc = self._phone_metadata(entity.value)
        if loc:
            loc.parent_entity_id = entity.id
            loc.depth = entity.depth + 1
            results.append(loc)

        try:
            cmd = self._command(country_code, local_number)
            started = time.monotonic()
            record_audit_event(
                "provider_process_started",
                outcome="started",
                provider="ignorant",
                target=entity.value,
            )
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            record_audit_event(
                "provider_process_completed",
                outcome="success" if process.returncode == 0 else "failed",
                provider="ignorant",
                return_code=process.returncode,
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                stdout=stdout.decode(errors="replace")[-500:] if stdout else None,
                error=stderr.decode(errors="replace")[-500:] if stderr else None,
            )

            for hit in self._parse_stdout(stdout.decode(errors="replace")):
                site = str(hit.get("name") or hit.get("domain") or "unknown")
                url = f"https://{site.strip('/')}"
                results.append(SocialProfileEntity(
                    value=url,
                    normalized_value=url.casefold().strip(),
                    source_module=self.name,
                    source_reliability=SourceReliability.HIGH,
                    confidence=0.88,
                    metadata={
                        "site": site,
                        "attested_via": "ignorant_reset_endpoint",
                    },
                    parent_entity_id=entity.id,
                    depth=entity.depth + 1,
                    url=url,
                    platform=site,
                    is_ground_truth=True,
                    evidence_urls=[url],
                    attests_platform=site,
                ))
        except Exception as exc:
            record_audit_event(
                "provider_process_failed",
                outcome="failed",
                provider="ignorant",
                target=entity.value,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self.logger.error(f"ignorant execution failed: {exc}")

        return results
