import asyncio
import json
import os
from typing import List, Dict, Any, Optional

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability, SocialProfileEntity
from rahasya.utils.http_client import StealthHTTPClient

class WhatsMyNameModule(BaseModule):
    name = "WhatsMyName"
    description = "Username enumeration using WhatsMyName data"
    version = "1.0.0"
    accepts = [EntityType.USERNAME, EntityType.EMAIL]
    produces = [EntityType.SOCIAL_PROFILE, EntityType.URL]
    
    DATA_URL = "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json"
    CACHE_PATH = "data/cache/whatsmyname_data.json"
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sites_data = None
        self.http_client = StealthHTTPClient()
        
    async def initialize(self):
        # Setup cache dir
        os.makedirs(os.path.dirname(self.CACHE_PATH), exist_ok=True)
        
        # Try loading from cache first
        if os.path.exists(self.CACHE_PATH):
            try:
                with open(self.CACHE_PATH, "r", encoding="utf-8") as f:
                    self.sites_data = json.load(f)
            except Exception:
                pass
                
        if not self.sites_data:
            try:
                response = await self.http_client.get(self.DATA_URL)
                if response.status_code == 200:
                    self.sites_data = response.json()
                    with open(self.CACHE_PATH, "w", encoding="utf-8") as f:
                        json.dump(self.sites_data, f)
            except Exception as e:
                self.logger.error(f"Failed to fetch WhatsMyName data: {e}")
                
    async def check_site(self, site: Dict[str, Any], target: str) -> Optional[SocialProfileEntity]:
        url = site.get("uri_check", "").replace("{account}", target)
        if not url:
            return None
            
        try:
            response = await self.http_client.get(url, timeout=10)
            text = response.text if hasattr(response, "text") else ""
            
            e_code = site.get("e_code", 200)
            e_string = site.get("e_string", "")
            m_string = site.get("m_string", "")
            
            # Simple heuristic
            is_valid = False
            if response.status_code == e_code:
                is_valid = True
                if e_string and e_string in text:
                    is_valid = True
                elif m_string and m_string in text:
                    is_valid = False
                    
            if is_valid:
                profile_url = site.get("uri_pretty", url).replace("{account}", target)
                return SocialProfileEntity(
                    entity_type=EntityType.SOCIAL_PROFILE,
                    value=profile_url,
                    normalized_value=profile_url.lower().strip(),
                    source_module=self.name,
                    source_reliability=SourceReliability.MEDIUM,
                    confidence=0.8,
                    metadata={"site": site.get("name"), "category": site.get("cat")},
                    parent_entity_id=None, # Will be set by caller
                    depth=0,
                    url=profile_url,
                    platform=site.get("name", "Unknown")
                )
        except Exception:
            pass
        return None
        
    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        if not self.sites_data:
            await self.initialize()
            
        if not self.sites_data:
            return []
            
        results = []
        target = entity.value
        if entity.entity_type == EntityType.EMAIL:
            target = target.split("@")[0]
            
        sites = self.sites_data.get("sites", [])
        
        semaphore = asyncio.Semaphore(30)
        
        async def bound_check(site):
            async with semaphore:
                res = await self.check_site(site, target)
                if res:
                    res.parent_entity_id = entity.id
                    res.depth = entity.depth + 1
                # delay for rate limit roughly
                await asyncio.sleep(0.1)
                return res
                
        tasks = [bound_check(site) for site in sites]
        completed = await asyncio.gather(*tasks, return_exceptions=True)
        
        for res in completed:
            if res and not isinstance(res, Exception):
                results.append(res)
                
        return results

    def is_available(self) -> bool:
        return True
