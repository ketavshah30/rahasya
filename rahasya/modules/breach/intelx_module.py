import asyncio
import time
from typing import List

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability, DarkWebMention, Entity as LeakRecordFallback
from rahasya.utils.http_client import StealthHTTPClient

class IntelXModule(BaseModule):
    name = "IntelligenceX"
    description = "Search IntelligenceX for data leaks and dark web mentions"
    version = "1.0.0"
    accepts = [EntityType.EMAIL, EntityType.PHONE, EntityType.DOMAIN]
    produces = [EntityType.LEAK_RECORD, EntityType.DARK_WEB_MENTION]
    
    BASE_URL = "https://2.intelx.io"
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.http_client = StealthHTTPClient()
        self.daily_usage = 0
        
    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        results = []
        api_key = getattr(self.config.api_keys, "intelx", None)
        
        if not api_key:
            return results
            
        if self.daily_usage >= 10:
            self.logger.warning("IntelX daily usage limit reached")
            return results
            
        headers = {
            "x-key": api_key,
            "User-Agent": "Rahasya-OSINT-Platform"
        }
        
        payload = {
            "term": entity.value,
            "maxresults": 10,
            "media": 0,
            "sort": 2,
            "terminate": []
        }
        
        try:
            # 1. Init search
            search_url = f"{self.BASE_URL}/intelligent/search"
            search_resp = await self.http_client.post(search_url, headers=headers, json=payload)
            self.daily_usage += 1
            
            if search_resp.status_code == 200:
                data = search_resp.json()
                search_id = data.get("id")
                
                if not search_id:
                    return results
                    
                # Poll results
                for _ in range(3):
                    await asyncio.sleep(2)
                    result_url = f"{self.BASE_URL}/intelligent/search/result?id={search_id}"
                    res_resp = await self.http_client.get(result_url, headers=headers)
                    
                    if res_resp.status_code == 200:
                        res_data = res_resp.json()
                        records = res_data.get("records", [])
                        
                        for rec in records:
                            val = rec.get("name", "")
                            if not val:
                                continue
                            
                            bucket = rec.get("bucket", "")
                            
                            if "darknet" in bucket.lower():
                                mention = DarkWebMention(
                                    entity_type=EntityType.DARK_WEB_MENTION,
                                    value=val,
                                    normalized_value=val.lower().strip(),
                                    source_module=self.name,
                                    source_reliability=SourceReliability.HIGH,
                                    confidence=0.85,
                                    metadata=rec,
                                    parent_entity_id=entity.id,
                                    depth=entity.depth + 1,
                                    source_url=f"https://intelx.io/?did={rec.get('systemid')}",
                                    context_snippet=val,
                                    search_engine="IntelligenceX",
                                    is_onion=True
                                )
                                results.append(mention)
                            else:
                                # LEAK_RECORD fallback using base Entity (assuming LEAK_RECORD is generic)
                                # Wait, we should use a proper class if it exists. The prompt mentions LeakRecord/DarkWebMention
                                # In models.py we saw EntityType.LEAK_RECORD. We can use Entity with this type if no specialized model exists.
                                leak = Entity(
                                    entity_type=EntityType.LEAK_RECORD,
                                    value=val,
                                    normalized_value=val.lower().strip(),
                                    source_module=self.name,
                                    source_reliability=SourceReliability.MEDIUM,
                                    confidence=0.8,
                                    metadata=rec,
                                    parent_entity_id=entity.id,
                                    depth=entity.depth + 1
                                )
                                results.append(leak)
                                
                        if res_data.get("status") in [1, 2]: # Finished
                            break
                            
        except Exception as e:
            self.logger.error(f"IntelX module error: {e}")
            
        return results

    def is_available(self) -> bool:
        return bool(getattr(self.config.api_keys, "intelx", None))
