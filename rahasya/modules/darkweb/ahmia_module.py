"""Best-effort Ahmia HTML search adapter."""

from typing import Dict, List, Set
from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit

from bs4 import BeautifulSoup

from rahasya.core.models import DarkWebMention, Entity, EntityType, SourceReliability
from rahasya.modules.base import BaseModule
from rahasya.storage.network_audit import record_audit_event


class AhmiaModule(BaseModule):
    name = "Ahmia"
    description = "Search Ahmia's clearnet HTML interface for Tor hidden services"
    version = "1.1.0"
    accepts = [EntityType.PERSON, EntityType.EMAIL, EntityType.USERNAME, EntityType.PHONE, EntityType.DOMAIN]
    produces = [EntityType.DARK_WEB_MENTION]
    BASE_URL = "https://ahmia.fi/search/"
    MAX_DEGRADED_RESPONSES = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._degraded_counts: Dict[str, int] = {}
        self._disabled_scans: Set[str] = set()

    def _mark_degraded(self, scan_id: str, reason: str) -> None:
        count = self._degraded_counts.get(scan_id, 0) + 1
        self._degraded_counts[scan_id] = count
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
            self._degraded_counts[scan_id] = 0
        return results

    def is_available(self) -> bool:
        return True
