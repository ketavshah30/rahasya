import asyncio
from typing import List
from urllib.parse import quote_plus

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability, DarkWebMention

class AhmiaModule(BaseModule):
    name = "Ahmia"
    description = "Search Ahmia clearnet API for Tor hidden services"
    version = "1.0.0"
    accepts = [EntityType.PERSON, EntityType.EMAIL, EntityType.USERNAME, EntityType.PHONE, EntityType.DOMAIN]
    produces = [EntityType.DARK_WEB_MENTION]
    
    BASE_URL = "https://ahmia.fi/api/search/"
    
    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        results = []
        query = entity.value
        
        try:
            url = f"{self.BASE_URL}?q={quote_plus(query)}"
            resp = await self.http_client.get(url)
            
            if resp.status_code == 200:
                # The API returns a list of items
                data = resp.json()
                for item in data.get("results", [])[:10]:
                    title = item.get("title", "")
                    link = item.get("url", "")
                    desc = item.get("description", "")
                    
                    if link:
                        mention = DarkWebMention(
                            entity_type=EntityType.DARK_WEB_MENTION,
                            value=title or link,
                            normalized_value=(title or link).lower().strip(),
                            source_module=self.name,
                            source_reliability=SourceReliability.MEDIUM,
                            confidence=0.7,
                            metadata={"domain": item.get("domain")},
                            parent_entity_id=entity.id,
                            depth=entity.depth + 1,
                            source_url=link,
                            context_snippet=desc,
                            search_engine="Ahmia",
                            is_onion=True
                        )
                        results.append(mention)
        except Exception as e:
            self.logger.error(f"Ahmia API search failed: {e}")
            
        return results

    def is_available(self) -> bool:
        return True
