"""GitHubProfileScrape — GitHub profile → all published identifiers.

FIXES_NEW.md §E.6.

Given a SOCIAL_PROFILE entity whose platform is 'github', hit both the
API endpoint and the public profile HTML to extract:
  * name (display)
  * email (only if the user opted to publish it)
  * blog / website (URL → DomainEntity)
  * twitter_username (→ ground-truth USERNAME on Twitter/X)
  * company, location
  * domains linked in pinned-repo READMEs (best-effort HTML scrape)

Every extracted identifier is ground truth for the underlying platform
because GitHub itself is attesting it. The twitter_username in particular
is the *right* pivot to feed Sherlock/Maigret against — a real,
platform-scoped handle, not a guess.
"""

import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from rahasya.core.models import (
    Entity,
    EntityType,
    EmailEntity,
    LocationEntity,
    PersonEntity,
    SourceReliability,
    UsernameEntity,
)
from rahasya.modules.base import BaseModule
from rahasya.storage.network_audit import record_audit_event

try:
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # noqa: BLE001
    BeautifulSoup = None  # type: ignore


class GitHubProfileScrapeModule(BaseModule):
    name = "GitHubProfileScrape"
    description = "Extract published identifiers from a GitHub user profile."
    version = "1.0.0"
    accepts = [EntityType.SOCIAL_PROFILE]
    produces = [
        EntityType.PERSON,
        EntityType.EMAIL,
        EntityType.USERNAME,
        EntityType.LOCATION,
        EntityType.DOMAIN,
    ]
    rate_limit = 1.0

    API_URL = "https://api.github.com/users"
    HTML_URL = "https://github.com"

    def is_available(self) -> bool:
        return True

    def _api_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Rahasya-OSINT/1.0",
        }
        token = os.getenv("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    def _looks_like_github_profile(entity: Entity) -> Optional[str]:
        """Return the login if the entity refers to a github user page."""
        platform = str(getattr(entity, "platform", "") or "").lower()
        url = str(getattr(entity, "url", "") or entity.value)
        if platform not in {"github", "github.com"} and "github.com" not in url.lower():
            return None
        parsed = urlsplit(url)
        parts = [segment for segment in parsed.path.split("/") if segment]
        if not parts:
            return None
        # Ignore known non-user paths.
        candidate = parts[0]
        if candidate.lower() in {"orgs", "topics", "features", "marketplace", "explore"}:
            return None
        # A github user login: 1-39 chars, alnum + '-'.
        if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", candidate):
            return None
        return candidate

    async def _fetch_api(self, login: str) -> Optional[Dict[str, Any]]:
        url = f"{self.API_URL}/{login}"
        try:
            response = await self.client.get(url, headers=self._api_headers(), timeout=15)
        except Exception as exc:  # noqa: BLE001
            record_audit_event(
                "provider_request_failed",
                outcome="failed",
                provider="github_profile",
                url=url,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return None
        status = getattr(response, "status_code", None)
        record_audit_event(
            "provider_request_completed",
            outcome="success" if status == 200 else "failed",
            provider="github_profile",
            url=url,
            status_code=status,
        )
        if status != 200:
            return None
        try:
            return response.json()
        except Exception:  # noqa: BLE001
            return None

    async def _fetch_html(self, login: str) -> str:
        url = f"{self.HTML_URL}/{login}"
        try:
            response = await self.client.get(url, timeout=15)
        except Exception:  # noqa: BLE001
            return ""
        if getattr(response, "status_code", 0) != 200:
            return ""
        return getattr(response, "text", "") or ""

    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        if entity.entity_type != EntityType.SOCIAL_PROFILE:
            return []
        login = self._looks_like_github_profile(entity)
        if not login:
            return []
        # We do NOT require is_ground_truth here — a profile URL we found
        # via Sherlock/Maigret is fair game to scrape for published
        # fields, because we're reading what GitHub itself displays, not
        # amplifying a guess.
        api = await self._fetch_api(login) or {}
        html = await self._fetch_html(login)

        profile_url = f"{self.HTML_URL}/{login}"
        api_evidence = [f"{self.API_URL}/{login}", profile_url]
        results: List[Entity] = []

        # display name → PersonEntity
        name = str(api.get("name") or "").strip()
        if name:
            results.append(PersonEntity(
                value=name,
                normalized_value=name.casefold(),
                source_module=self.name,
                source_reliability=SourceReliability.HIGH,
                confidence=0.9,
                metadata={"github_login": login},
                parent_entity_id=entity.id,
                depth=entity.depth + 1,
                name=name,
                is_ground_truth=True,
                evidence_urls=api_evidence,
            ))

        # Published email (users opt in).
        email = str(api.get("email") or "").strip()
        if email and "@" in email:
            results.append(EmailEntity(
                value=email,
                normalized_value=email.casefold(),
                source_module=self.name,
                source_reliability=SourceReliability.HIGH,
                confidence=0.9,
                metadata={"github_login": login, "attested_via": "github_public_email"},
                parent_entity_id=entity.id,
                depth=entity.depth + 1,
                address=email.casefold(),
                domain=email.rsplit("@", 1)[-1].casefold(),
                is_ground_truth=True,
                evidence_urls=api_evidence,
            ))

        # Twitter handle (ground-truth USERNAME with attests_platform=twitter)
        twitter = str(api.get("twitter_username") or "").strip().lstrip("@")
        if twitter:
            results.append(UsernameEntity(
                value=twitter,
                normalized_value=twitter.lower(),
                source_module=self.name,
                source_reliability=SourceReliability.HIGH,
                confidence=0.95,
                metadata={
                    "github_login": login,
                    "attested_via": "github_public_profile",
                },
                parent_entity_id=entity.id,
                depth=entity.depth + 1,
                handle=twitter,
                is_ground_truth=True,
                evidence_urls=api_evidence,
                attests_platform="twitter",
            ))

        # Location (LocationEntity)
        location = str(api.get("location") or "").strip()
        if location:
            results.append(LocationEntity(
                value=location,
                normalized_value=location.casefold(),
                source_module=self.name,
                source_reliability=SourceReliability.HIGH,
                confidence=0.75,
                metadata={"github_login": login},
                parent_entity_id=entity.id,
                depth=entity.depth + 1,
                source_type="github_profile",
                is_ground_truth=True,
                evidence_urls=api_evidence,
            ))

        # Blog / website (raw URL entity — DomainEntity is not modeled as a
        # subclass in the current codebase; the plain Entity carries a
        # DOMAIN type which downstream code already handles.)
        blog = str(api.get("blog") or "").strip()
        if blog:
            if not blog.startswith(("http://", "https://")):
                blog = f"https://{blog}"
            host = urlsplit(blog).hostname or blog
            results.append(Entity(
                entity_type=EntityType.DOMAIN,
                value=host,
                normalized_value=host.casefold(),
                source_module=self.name,
                source_reliability=SourceReliability.HIGH,
                confidence=0.85,
                metadata={
                    "github_login": login,
                    "attested_via": "github_public_blog",
                    "full_url": blog,
                },
                parent_entity_id=entity.id,
                depth=entity.depth + 1,
                is_ground_truth=True,
                evidence_urls=api_evidence,
            ))

        # Company (best-effort — often "@org" style)
        company = str(api.get("company") or "").strip().lstrip("@")
        if company:
            # Emit as a PersonEntity metadata hint via a bespoke entity is
            # overkill here. Skip company entity emission for now — the
            # HunterIOReverse module (§E.5) is the correct producer of
            # company entities.
            pass

        # HTML fallback — pinned-repo README domains, extra links in bio.
        if html and BeautifulSoup is not None:
            try:
                soup = BeautifulSoup(html, "html.parser")
                for anchor in soup.select("a[href^='http']"):
                    href = anchor.get("href", "")
                    if not href:
                        continue
                    host = urlsplit(href).hostname or ""
                    if not host or host.endswith("github.com") or host.endswith("githubusercontent.com"):
                        continue
                    # Cap the domain scrape to keep this cheap.
                    if len(results) > 40:
                        break
                    results.append(Entity(
                        entity_type=EntityType.DOMAIN,
                        value=host,
                        normalized_value=host.casefold(),
                        source_module=self.name,
                        source_reliability=SourceReliability.MEDIUM,
                        confidence=0.55,
                        metadata={
                            "github_login": login,
                            "attested_via": "github_profile_html_link",
                            "full_url": href,
                        },
                        parent_entity_id=entity.id,
                        depth=entity.depth + 1,
                        # Not ground truth — this is a link the user put on
                        # their profile, which is a signal but not a
                        # provider-attested identifier of theirs.
                        is_ground_truth=False,
                        evidence_urls=[profile_url],
                    ))
            except Exception:  # noqa: BLE001
                pass

        return results
