import asyncio
import json
import os
import time
from typing import List
from urllib.parse import quote_plus
from bs4 import BeautifulSoup

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability, DarkWebMention
from rahasya.modules.darkweb.tor_manager import TorManager
from rahasya.storage.network_audit import record_audit_event

class OnionSearchModule(BaseModule):
    name = "OnionSearch"
    description = "Search multiple dark web engines via Tor"
    version = "1.0.0"
    accepts = [EntityType.PERSON, EntityType.EMAIL, EntityType.USERNAME, EntityType.PHONE, EntityType.DOMAIN]
    produces = [EntityType.DARK_WEB_MENTION]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.tor = TorManager(
            socks_host=self.config.tor.socks_host,
            socks_port=self.config.tor.socks_port,
            control_port=self.config.tor.control_port,
            password=self.config.tor.password or "",
        )
        self.engines = []
        
    async def initialize(self):
        config_path = "data/config/onion_engines.json"
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                self.engines = json.load(f)

    async def setup(self):
        await super().setup()
        if not self.engines:
            await self.initialize()
                
    async def search_engine(self, engine, query, entity):
        results = []
        if not engine.get("enabled", True):
            record_audit_event(
                "source_skipped",
                outcome="skipped",
                url=engine.get("url"),
                source_name=engine.get("name", "unknown"),
                message="Search engine is disabled in onion_engines.json",
            )
            return results
            
        url = engine["url"].replace("{query}", query)
        started = time.monotonic()
        response_recorded = False
        try:
            client = self.tor.get_async_client()
            try:
                resp = await client.get(url)
            finally:
                await client.aclose()
            record_audit_event(
                "network_request",
                outcome="success" if resp.status_code == 200 else "http_error",
                url=url,
                method="GET",
                status_code=resp.status_code,
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                via_proxy=True,
                source_name=engine.get("name", "unknown"),
                purpose="dark_web_search",
            )
            response_recorded = True
            
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, 'html.parser')
                engine_type = engine.get("parser", engine.get("type", "generic"))
                
                # Simple parsing logic depending on engine type
                items = []
                if engine_type == "ahmia":
                    items = soup.find_all("li", class_="result")
                elif engine_type == "haystak":
                    items = soup.find_all("div", class_="result")
                elif engine_type == "torch":
                    items = soup.find_all("div", class_="result")
                else:
                    items = soup.find_all("a")
                    
                for idx, item in enumerate(items[:5]): # limit per engine
                    link = item.find("a")
                    if not link:
                        continue
                    
                    href = link.get("href", "")
                    title = link.text.strip()
                    
                    if href:
                        mention = DarkWebMention(
                            entity_type=EntityType.DARK_WEB_MENTION,
                            value=title or href,
                            normalized_value=(title or href).lower().strip(),
                            source_module=self.name,
                            source_reliability=SourceReliability.MEDIUM,
                            confidence=0.7,
                            metadata={"engine": engine["name"]},
                            parent_entity_id=entity.id,
                            depth=entity.depth + 1,
                            source_url=href,
                            context_snippet=item.text.strip()[:200],
                            search_engine=engine["name"],
                            is_onion=".onion" in href
                        )
                        results.append(mention)
        except Exception as e:
            record_audit_event(
                "source_parse_failed" if response_recorded else "network_request",
                outcome="failed",
                url=url,
                method="GET",
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                via_proxy=True,
                source_name=engine.get("name", "unknown"),
                purpose="dark_web_search",
                error_type=type(e).__name__,
                error=str(e),
            )
            self.logger.error(f"Error searching {engine['name']}: {e}")
            
        return results

    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        if not self.engines:
            await self.initialize()
            
        if not await self.tor.check_tor_running():
            self.logger.warning("Tor is not running. Using fallback.")
            return []
            
        query = quote_plus(entity.value)
        tasks = [self.search_engine(eng, query, entity) for eng in self.engines]
        completed = await asyncio.gather(*tasks, return_exceptions=True)
        
        results = []
        for res in completed:
            if isinstance(res, list):
                results.extend(res)
                
        return results

    def is_available(self) -> bool:
        return self.config.tor.enabled
