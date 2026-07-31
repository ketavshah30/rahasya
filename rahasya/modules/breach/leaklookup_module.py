import asyncio
from typing import List

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability, BreachRecord
from rahasya.utils.http_client import StealthHTTPClient

class LeakLookupModule(BaseModule):
    name = "LeakLookup"
    description = "Search Leak-Lookup for data breaches"
    version = "1.0.0"
    accepts = [EntityType.EMAIL]
    produces = [EntityType.BREACH_RECORD, EntityType.LEAK_RECORD]
    
    BASE_URL = "https://leak-lookup.com/api/search"
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.http_client = StealthHTTPClient()
        
    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        results = []
        email = entity.value
        api_key = getattr(self.config.api_keys, "leaklookup", None)
        
        if not api_key:
            return results
            
        data = {
            "key": api_key,
            "type": "email_address",
            "query": email
        }
        
        try:
            resp = await self.http_client.post(self.BASE_URL, data=data)
            if resp.status_code == 200:
                resp_json = resp.json()
                error = resp_json.get("error", "false")
                
                if str(error).lower() != "false" and str(error).lower() != "0":
                    return results
                    
                message = resp_json.get("message", {})
                if isinstance(message, dict):
                    for breach, data_list in message.items():
                        record = BreachRecord(
                            entity_type=EntityType.BREACH_RECORD,
                            value=f"LeakLookup-{breach}",
                            normalized_value=f"leaklookup-{str(breach).lower()}",
                            source_module=self.name,
                            source_reliability=SourceReliability.MEDIUM,
                            confidence=0.8,
                            metadata={"breach_name": breach, "records": data_list},
                            parent_entity_id=entity.id,
                            depth=entity.depth + 1,
                            breach_name=breach,
                            data_types_leaked=[],
                            affected_count=len(data_list) if isinstance(data_list, list) else None,
                            severity="Medium",
                            source_name="Leak-Lookup"
                        )
                        results.append(record)
        except Exception as e:
            self.logger.error(f"LeakLookup module error: {e}")
            
        return results

    def is_available(self) -> bool:
        return bool(getattr(self.config.api_keys, "leaklookup", None))
