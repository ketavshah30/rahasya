"""Best-effort Ahmia HTML search adapter.

FIXES_NEW.md §H.2:
  * Only fires on ground-truth identifiers (candidates emit nothing).
  * Precondition: at least one confirmed SOCIAL_PROFILE already exists on
    the scan. Dark-web sources have very low precision when queried with a
    raw seed; their real signal is in paste dumps and marketplace listings
    tied to *already-verified* identifiers.
  * Degraded-response circuit breaker raised from 2 → 4 and time-reset
    every 5 minutes so a single Cloudflare hiccup does not blackball Ahmia
    for the whole scan.
"""

import time
from typing import Dict, List, Set, Tuple
from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit

from bs4 import BeautifulSoup

from rahasya.core.models import DarkWebMention, Entity, EntityType, SourceReliability
from rahasya.modules.base import BaseModule
from rahasya.modules.darkweb._gating import scan_has_confirmed_social_profile
from rahasya.storage.network_audit import record_audit_event


class AhmiaModule(BaseModule):
    name = "Ahmia"
    description = "Search Ahmia's clearnet HTML interface for Tor hidden services"
    version = "1.2.0"
    # FIXES_NEW.md §H.2: dark web is a *late-stage* signal keyed off
    # already-verified identifiers, not a general-purpose seed enumerator.
    accepts = [EntityType.EMAIL, EntityType.USERNAME, EntityType.PHONE]
    produces = [EntityType.DARK_WEB_MENTION]
    BASE_URL = "https://ahmia.fi/search/"
    # FIXES_NEW.md §H.2: raise from 2 → 4, reset after 5 minutes so we don't
    # blackball Ahmia for the whole scan on a temporary hiccup.
    MAX_DEGRADED_RESPONSES = 4
    DEGRADED_RESET_SECONDS = 300

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # scan_id -> (count, last_ts)
        self._degraded_state: Dict[str, Tuple[int, float]] = {}
        self._disabled_scans: Set[str] = set()

    def _mark_degraded(self, scan_id: str, reason: str) -> None:
        now = time.monotonic()
        prev_count, prev_ts = self._degraded_state.get(scan_id, (0, 0.0))
        if now - prev_ts > self.DEGRADED_RESET_SECONDS:
            prev_count = 0
        count = prev_count + 1
        self._degraded_state[scan_id] = (count, now)
        record_audit_event(
            "source_degraded",
            outcome="degraded",
            url=self.BASE_URL,
            reason=reason,
            degraded_count=count,
        )
        if count > self.MAX_DEGRADED_RESPONSES:
            self._disabled_scans.add(scan_id)
            record_audit_event(
                "module_skipped",
                outcome="degraded",
                url=self.BASE_URL,
                skip_reason="repeated_empty_or_redirected_responses",
                message="Ahmia disabled for the remainder of this scan after repeated degraded responses",
            )

    @staticmethod
    def _result_url(href: str) -> str:
        absolute = urljoin("https://ahmia.fi", href)
        redirect_url = parse_qs(urlsplit(absolute).query).get("redirect_url", [])
        return redirect_url[0] if redirect_url else absolute

    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        # FIXES_NEW.md §H.2: refuse to fire on non-ground-truth entities.
        if not getattr(entity, "is_ground_truth", False):
            record_audit_event(
                "module_skipped",
                outcome="skipped",
                provider="ahmia",
                entity_type=entity.entity_type.value,
                entity_value=entity.value,
                reason="dark_web_gated_on_ground_truth",
            )
            return []
        # FIXES_NEW.md §H.2: require at least one confirmed SOCIAL_PROFILE
        # already registered on the scan before dark-web sources fire.
        if not scan_has_confirmed_social_profile(scan_id):
            record_audit_event(
                "module_skipped",
                outcome="skipped",
                provider="ahmia",
                entity_type=entity.entity_type.value,
                entity_value=entity.value,
                reason="dark_web_gated_on_prior_confirmation",
                message="No confirmed SOCIAL_PROFILE has been registered on this scan yet",
            )
            return []
        if scan_id in self._disabled_scans:
            record_audit_event(
                "module_skipped",
                outcome="degraded",
                url=self.BASE_URL,
                skip_reason="disabled_for_scan",
                message="Ahmia was disabled after repeated degraded responses",
            )
            return []

        url = f"{self.BASE_URL}?q={quote_plus(entity.value)}"
        try:
            response = await self.client.get(
                url,
                headers={
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                    ),
                },
            )
        except Exception as exc:
            self.logger.error(f"Ahmia HTML search failed: {exc}")
            return []

        location = response.headers.get("location", "")
        if response.status_code in {301, 302, 303, 307, 308} and urlsplit(location).path in {"", "/"}:
            self._mark_degraded(scan_id, "redirected_to_homepage")
            return []
        if not response.text.strip():
            self._mark_degraded(scan_id, "empty_response")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        result_items = soup.select("li.result")
        if not result_items:
            self._mark_degraded(scan_id, "result_markup_missing")
            return []

        results: List[Entity] = []
        for item in result_items[:10]:
            anchor = item.select_one("h4 a, a")
            if anchor is None or not anchor.get("href"):
                continue
            result_url = self._result_url(str(anchor.get("href")))
            title = anchor.get_text(" ", strip=True) or result_url
            description_node = item.select_one("p")
            description = description_node.get_text(" ", strip=True) if description_node else ""
            results.append(DarkWebMention(
                value=title,
                normalized_value=title.casefold().strip(),
                source_module=self.name,
                source_reliability=SourceReliability.MEDIUM,
                confidence=0.7,
                metadata={"domain": urlsplit(result_url).hostname},
                parent_entity_id=entity.id,
                depth=entity.depth + 1,
                source_url=result_url,
                context_snippet=description,
                search_engine="Ahmia",
                is_onion=".onion" in (urlsplit(result_url).hostname or ""),
            ))
        if results:
            # Reset the degraded counter on a good response.
            self._degraded_state.pop(scan_id, None)
        return results

    def is_available(self) -> bool:
        return True
