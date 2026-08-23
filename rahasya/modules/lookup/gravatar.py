"""GravatarLookup — email → verified accounts + display name + photo.

FIXES_NEW.md §E.3.

Gravatar hashes the lowercase-trimmed email with SHA-256 and returns a
public profile JSON when the user has one, containing:
  - displayName
  - preferredUsername
  - verified accounts[] (Twitter/X, GitHub, StackOverflow, WordPress,
    LinkedIn, Mastodon, ...)
  - profile photo URL

Because Gravatar requires the user to prove ownership of each linked
account, every entry in `accounts[]` is *ground-truth attestation* of the
form "email → verified handle on platform".

No API key. Highest-precision email→identity pivot on the public internet.
"""

import asyncio
import hashlib
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from rahasya.core.models import (
    Entity,
    EntityType,
    PersonEntity,
    PhotoEntity,
    SocialProfileEntity,
    SourceReliability,
    UsernameEntity,
)
from rahasya.modules.base import BaseModule
from rahasya.storage.network_audit import record_audit_event


def _sha256(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _domain_to_platform(url_or_domain: str) -> str:
    """Extract a compact platform slug from an account URL or shortname."""
    if not url_or_domain:
        return "unknown"
    text = url_or_domain.strip()
    if "://" in text:
        host = urlsplit(text).hostname or text
    else:
        host = text
    host = host.lower().lstrip(".").split(":", 1)[0]
    # Take the second-to-last label as the platform slug (github.com→github).
    labels = [label for label in host.split(".") if label]
    if len(labels) >= 2 and labels[-1] in {"com", "net", "org", "io", "co", "app"}:
        return labels[-2]
    return labels[0] if labels else host


class GravatarLookupModule(BaseModule):
    name = "GravatarLookup"
    description = "Email → Gravatar public profile (verified linked accounts)."
    version = "1.0.0"
    accepts = [EntityType.EMAIL]
    produces = [
        EntityType.PERSON,
        EntityType.USERNAME,
        EntityType.SOCIAL_PROFILE,
        EntityType.PHOTO,
    ]
    rate_limit = 2.0

    def is_available(self) -> bool:
        # FIXES_NEW.md §I.1: keyless, always available.
        return True

    def _profile_urls(self, email: str) -> List[str]:
        digest = _sha256(email)
        return [
            f"https://gravatar.com/{digest}.json",
            f"https://www.gravatar.com/{digest}.json",
        ]

    async def _fetch_profile(self, email: str) -> Optional[Dict[str, Any]]:
        for url in self._profile_urls(email):
            try:
                response = await self.client.get(url, timeout=15)
            except Exception as exc:  # noqa: BLE001
                record_audit_event(
                    "provider_request_failed",
                    outcome="failed",
                    provider="gravatar",
                    url=url,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                continue
            status = getattr(response, "status_code", None)
            record_audit_event(
                "provider_request_completed",
                outcome="success" if status == 200 else "failed",
                provider="gravatar",
                url=url,
                status_code=status,
            )
            if status != 200:
                continue
            try:
                data = response.json()
            except Exception:  # noqa: BLE001
                continue
            entries = data.get("entry") if isinstance(data, dict) else None
            if isinstance(entries, list) and entries:
                return entries[0]
        return None

    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        if entity.entity_type != EntityType.EMAIL:
            return []
        if not getattr(entity, "is_ground_truth", False):
            record_audit_event(
                "module_skipped",
                outcome="skipped",
                provider="gravatar",
                entity_type=entity.entity_type.value,
                entity_value=entity.value,
                reason="candidate_email_not_corroborated",
            )
            return []

        email = entity.value.strip().lower()
        profile = await self._fetch_profile(email)
        if not profile:
            return []

        results: List[Entity] = []
        source_url = profile.get("profileUrl") or f"https://gravatar.com/{_sha256(email)}"

        display_name = profile.get("displayName") or profile.get("name", {}).get("formatted")
        if display_name and isinstance(display_name, str):
            name_clean = display_name.strip()
            if name_clean:
                results.append(PersonEntity(
                    value=name_clean,
                    normalized_value=name_clean.casefold(),
                    source_module=self.name,
                    source_reliability=SourceReliability.HIGH,
                    confidence=0.9,
                    metadata={"gravatar_email_hash": _sha256(email)},
                    parent_entity_id=entity.id,
                    depth=entity.depth + 1,
                    name=name_clean,
                    is_ground_truth=True,
                    evidence_urls=[source_url],
                ))

        # Gravatar's own preferredUsername — attested on gravatar.com itself.
        preferred = profile.get("preferredUsername")
        if isinstance(preferred, str) and preferred.strip():
            handle = preferred.strip()
            results.append(UsernameEntity(
                value=handle,
                normalized_value=handle.lower(),
                source_module=self.name,
                source_reliability=SourceReliability.HIGH,
                confidence=0.9,
                metadata={"profile_url": source_url},
                parent_entity_id=entity.id,
                depth=entity.depth + 1,
                handle=handle,
                is_ground_truth=True,
                evidence_urls=[source_url],
                attests_platform="gravatar",
            ))

        # Verified linked accounts.
        accounts = profile.get("accounts") if isinstance(profile.get("accounts"), list) else []
        for account in accounts:
            if not isinstance(account, dict):
                continue
            handle = str(account.get("username") or account.get("display") or "").strip()
            url = str(account.get("url") or "").strip()
            shortname = str(account.get("shortname") or "").strip()
            platform = shortname or _domain_to_platform(url)
            if not url and not handle:
                continue
            if url:
                results.append(SocialProfileEntity(
                    value=url,
                    normalized_value=url.casefold().strip(),
                    source_module=self.name,
                    source_reliability=SourceReliability.HIGH,
                    confidence=0.95,
                    metadata={
                        "platform": platform,
                        "attested_via": "gravatar_verified_link",
                        "raw": account,
                    },
                    parent_entity_id=entity.id,
                    depth=entity.depth + 1,
                    url=url,
                    platform=platform,
                    is_verified=bool(account.get("verified", True)),
                    is_ground_truth=True,
                    evidence_urls=[source_url, url],
                    attests_platform=platform,
                ))
            if handle:
                results.append(UsernameEntity(
                    value=handle,
                    normalized_value=handle.lower(),
                    source_module=self.name,
                    source_reliability=SourceReliability.HIGH,
                    confidence=0.95,
                    metadata={"platform": platform},
                    parent_entity_id=entity.id,
                    depth=entity.depth + 1,
                    handle=handle,
                    is_ground_truth=True,
                    evidence_urls=[source_url] + ([url] if url else []),
                    attests_platform=platform,
                ))

        # Gravatar photo URL.
        thumb = profile.get("thumbnailUrl") or profile.get("avatarUrl")
        photos = profile.get("photos") if isinstance(profile.get("photos"), list) else []
        photo_url = None
        if isinstance(thumb, str) and thumb.strip():
            photo_url = thumb.strip()
        elif photos:
            first = photos[0]
            if isinstance(first, dict):
                photo_url = str(first.get("value") or "").strip() or None
        if photo_url:
            results.append(PhotoEntity(
                value=photo_url,
                normalized_value=photo_url.casefold(),
                source_module=self.name,
                source_reliability=SourceReliability.HIGH,
                confidence=0.9,
                metadata={"source": "gravatar"},
                parent_entity_id=entity.id,
                depth=entity.depth + 1,
                file_path=photo_url,
                is_ground_truth=True,
                evidence_urls=[source_url],
            ))

        return results
