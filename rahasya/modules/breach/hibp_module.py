import asyncio
from typing import List
from urllib.parse import quote_plus

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability, BreachRecord

class HIBPModule(BaseModule):
    name = "HIBP"
    description = "HaveIBeenPwned API integration for breach data"
    version = "1.0.0"
    accepts = [EntityType.EMAIL]
    produces = [EntityType.BREACH_RECORD]
    
    BASE_URL = "https://haveibeenpwned.com/api/v3"
    
    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        results = []
        email = entity.value
        api_key = self._get_api_key()
        
        if not api_key:
            self.logger.warning("HIBP API key not configured")
            return []
            
        headers = {
            "hibp-api-key": api_key,
            "User-Agent": "Rahasya-OSINT-Platform"
        }
        
        try:
            # 1. Breached accounts
            encoded_email = quote_plus(email)
            breach_url = f"{self.BASE_URL}/breachedaccount/{encoded_email}?truncateResponse=false"
            breach_resp = await self.client.get(breach_url, headers=headers)
            
            if breach_resp.status_code == 200:
                breaches = breach_resp.json()
                for b in breaches:
                    record = BreachRecord(
                        entity_type=EntityType.BREACH_RECORD,
                        value=f"HIBP-{b.get('Name')}",
                        normalized_value=f"hibp-{str(b.get('Name')).lower()}",
                        source_module=self.name,
                        source_reliability=SourceReliability.HIGH,
                        confidence=0.99,
                        metadata=b,
                        parent_entity_id=entity.id,
                        depth=entity.depth + 1,
                        breach_name=b.get("Name"),
                        data_types_leaked=b.get("DataClasses", []),
                        affected_count=b.get("PwnCount"),
                        severity="High" if b.get("IsSensitive") else "Medium",
                        source_name="HaveIBeenPwned"
                    )
                    results.append(record)
            elif breach_resp.status_code == 429:
                self.logger.warning("HIBP API rate limited")
                replacement = self.rotate_api_key()
                if replacement and replacement != api_key:
                    headers["hibp-api-key"] = replacement
            elif breach_resp.status_code == 401:
                self.logger.error("HIBP API key invalid")
                
            # Sleep a bit to respect HIBP rate limits (1.5s per request usually required)
            await asyncio.sleep(2)
            
            # 2. Paste accounts
            paste_url = f"{self.BASE_URL}/pasteaccount/{encoded_email}"
            paste_resp = await self.client.get(paste_url, headers=headers)
            
            if paste_resp.status_code == 200:
                pastes = paste_resp.json()
                for p in pastes:
                    record = BreachRecord(
                        entity_type=EntityType.BREACH_RECORD,
                        value=f"Paste-{p.get('Id')}",
                        normalized_value=f"paste-{str(p.get('Id')).lower()}",
                        source_module=self.name,
                        source_reliability=SourceReliability.HIGH,
                        confidence=0.9,
                        metadata=p,
                        parent_entity_id=entity.id,
                        depth=entity.depth + 1,
                        breach_name=p.get("Source", "Unknown Paste"),
                        data_types_leaked=["Email"],
                        affected_count=p.get("EmailCount"),
                        severity="Medium",
                        source_name="HaveIBeenPwned"
                    )
                    results.append(record)
                    
        except Exception as e:
            self.logger.error(f"HIBP module error: {e}")
            
        return results

    def is_available(self) -> bool:
        """HIBP requires an API key to function."""
        return bool(self._get_api_key())
