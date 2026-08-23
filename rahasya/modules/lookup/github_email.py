"""GitHubEmailSearch — email → GitHub identity via commit search.

FIXES_NEW.md §E.4.

Given a ground-truth email, ask the GitHub commits API for commits whose
`author-email` matches it. GitHub returns author.login, author.email,
commit.author.name for each hit. Every result is *directly-attested*
(GitHub itself is telling us that this login pushed a commit signed with
this email).

Auth is optional:
  - Unauthenticated: 10 req/min per IP.
  - GITHUB_TOKEN env var set: 30 req/min.

Highest-precision keyless email→handle pivot for developers.
"""

import os
from typing import Any, Dict, List, Optional

from rahasya.core.models import (
    Entity,
    EntityType,
    PersonEntity,
    SocialProfileEntity,
    SourceReliability,
    UsernameEntity,
)
from rahasya.modules.base import BaseModule
from rahasya.storage.network_audit import record_audit_event


class GitHubEmailSearchModule(BaseModule):
    name = "GitHubEmailSearch"
    description = "Email → GitHub login via commit search."
    version = "1.0.0"
    accepts = [EntityType.EMAIL]
    produces = [
        EntityType.USERNAME,
        EntityType.PERSON,
        EntityType.SOCIAL_PROFILE,
    ]
    rate_limit = 1.0

    API_URL = "https://api.github.com/search/commits"

    def is_available(self) -> bool:
        # FIXES_NEW.md §I.1: keyless by default; GITHUB_TOKEN is optional
        # and only raises the rate limit (10→30 req/min).
        return True

    def _headers(self) -> Dict[str, str]:
        headers = {
            # cloak-preview media type is required for the commit-search
            # endpoint. GitHub keeps honoring the header even on stable API
            # versions because a lot of tooling depends on it.
            "Accept": "application/vnd.github.cloak-preview+json",
            "User-Agent": "Rahasya-OSINT/1.0",
        }
        token = os.getenv("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _search(self, email: str) -> List[Dict[str, Any]]:
        params = {
            "q": f"author-email:{email}",
            "per_page": "30",
        }
        try:
            response = await self.client.get(
                self.API_URL,
                params=params,
                headers=self._headers(),
                timeout=20,
            )
        except Exception as exc:  # noqa: BLE001
            record_audit_event(
                "provider_request_failed",
                outcome="failed",
                provider="github_email",
                url=self.API_URL,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            return []

        status = getattr(response, "status_code", None)
        record_audit_event(
            "provider_request_completed",
            outcome="success" if status == 200 else "failed",
            provider="github_email",
            url=self.API_URL,
            status_code=status,
        )
        if status != 200:
            return []
        try:
            data = response.json()
        except Exception:  # noqa: BLE001
            return []
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []

    @staticmethod
    def _first_str(*candidates: Any) -> Optional[str]:
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None

    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        if entity.entity_type != EntityType.EMAIL:
            return []
        if not getattr(entity, "is_ground_truth", False):
            record_audit_event(
                "module_skipped",
                outcome="skipped",
                provider="github_email",
                entity_type=entity.entity_type.value,
                entity_value=entity.value,
                reason="candidate_email_not_corroborated",
            )
            return []

        email = entity.value.strip().lower()
        items = await self._search(email)
        if not items:
            return []

        # Deduplicate on (login, display-name) since a single dev typically
        # produces many commits per (login, email) pair.
        seen_logins: Dict[str, Dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            author = item.get("author") if isinstance(item.get("author"), dict) else {}
            commit = item.get("commit") if isinstance(item.get("commit"), dict) else {}
            commit_author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
            login = self._first_str(author.get("login"))
            if not login:
                continue
            record = seen_logins.setdefault(login, {
                "login": login,
                "html_url": self._first_str(author.get("html_url")),
                "display_name": self._first_str(
                    commit_author.get("name"),
                    author.get("name"),
                ),
                "confirmed_email": self._first_str(commit_author.get("email")),
                "commit_url": self._first_str(item.get("html_url")),
            })
            # Fill any still-missing fields from later hits.
            for key, value in record.items():
                if not value:
                    if key == "display_name":
                        record[key] = self._first_str(
                            commit_author.get("name"), author.get("name")
                        )
                    elif key == "confirmed_email":
                        record[key] = self._first_str(commit_author.get("email"))

        results: List[Entity] = []
        for login, record in seen_logins.items():
            profile_url = record["html_url"] or f"https://github.com/{login}"
            evidence = [url for url in (record["commit_url"], profile_url) if url]

            results.append(UsernameEntity(
                value=login,
                normalized_value=login.lower(),
                source_module=self.name,
                source_reliability=SourceReliability.HIGH,
                confidence=0.95,
                metadata={
                    "attested_via": "github_commit_search",
                    "queried_email": email,
                },
                parent_entity_id=entity.id,
                depth=entity.depth + 1,
                handle=login,
                is_ground_truth=True,
                evidence_urls=evidence,
                attests_platform="github",
            ))

            results.append(SocialProfileEntity(
                value=profile_url,
                normalized_value=profile_url.casefold().strip(),
                source_module=self.name,
                source_reliability=SourceReliability.HIGH,
                confidence=0.95,
                metadata={
                    "attested_via": "github_commit_search",
                    "queried_email": email,
                },
                parent_entity_id=entity.id,
                depth=entity.depth + 1,
                url=profile_url,
                platform="github",
                is_verified=True,
                is_ground_truth=True,
                evidence_urls=evidence,
                attests_platform="github",
            ))

            if record["display_name"]:
                name = record["display_name"]
                results.append(PersonEntity(
                    value=name,
                    normalized_value=name.casefold(),
                    source_module=self.name,
                    source_reliability=SourceReliability.HIGH,
                    confidence=0.85,
                    metadata={
                        "attested_via": "github_commit_search",
                        "queried_email": email,
                        "github_login": login,
                    },
                    parent_entity_id=entity.id,
                    depth=entity.depth + 1,
                    name=name,
                    is_ground_truth=True,
                    evidence_urls=evidence,
                ))

        return results
