import asyncio
import json
import os
from typing import List, Dict, Any, Optional
from urllib.parse import quote_plus, urlsplit

import httpx

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability, SocialProfileEntity
from rahasya.storage.network_audit import record_audit_event

class WhatsMyNameModule(BaseModule):
    name = "WhatsMyName"
    description = "Username enumeration using WhatsMyName data"
    version = "1.0.0"
    accepts = [EntityType.USERNAME, EntityType.EMAIL]
    produces = [EntityType.SOCIAL_PROFILE, EntityType.URL]
    
    DATA_URL = "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json"
    CACHE_PATH = "data/cache/whatsmyname_data.json"
    request_jitter = None
    host_failure_limit = 3
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sites_data = None
        
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
                headers = {}
                github_token = os.getenv("GITHUB_TOKEN")
                if github_token:
                    headers["Authorization"] = f"Bearer {github_token}"
                response = await self.client.get(self.DATA_URL, headers=headers)
                if response.status_code == 200:
                    self.sites_data = response.json()
                    with open(self.CACHE_PATH, "w", encoding="utf-8") as f:
                        json.dump(self.sites_data, f)
            except Exception as e:
                self.logger.error(f"Failed to fetch WhatsMyName data: {e}")
                
    async def check_site(self, site: Dict[str, Any], target: str) -> Optional[SocialProfileEntity]:
        encoded_target = quote_plus(target)
        url = site.get("uri_check", "").replace("{account}", encoded_target)
        if not url:
            return None
            
        try:
            response = await self.client.get(url, timeout=10)
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
                profile_url = site.get("uri_pretty", url).replace("{account}", encoded_target)
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
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise
        except Exception:
            pass
        return None
        
    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        if not self.sites_data:
            await self.initialize()
            
        if not self.sites_data:
            return []
            
        results: List[Entity] = []
        target = entity.value
        if entity.entity_type == EntityType.EMAIL:
            target = target.split("@")[0]
            
        sites = self.sites_data.get("sites", [])
        
        semaphore = asyncio.Semaphore(150)
        host_failures: Dict[str, int] = {}
        blocked_hosts = set()
        circuit_lock = asyncio.Lock()
        host_locks: Dict[str, asyncio.Lock] = {}
        
        async def bound_check(site):
            check_url = site.get("uri_check", "").replace("{account}", quote_plus(target))
            host = urlsplit(check_url).hostname or "unknown"
            host_lock = host_locks.setdefault(host, asyncio.Lock())
            async with semaphore:
                async with host_lock:
                    async with circuit_lock:
                        if host in blocked_hosts:
                            record_audit_event(
                                "source_skipped",
                                outcome="skipped",
                                url=check_url or None,
                                source_name=site.get("name", "unknown"),
                                skip_reason="host_circuit_open",
                                message=f"Skipped after {self.host_failure_limit} consecutive connection failures",
                            )
                            return None
                    try:
                        res = await self.check_site(site, target)
                    except (httpx.ConnectError, httpx.ConnectTimeout):
                        async with circuit_lock:
                            host_failures[host] = host_failures.get(host, 0) + 1
                            if host_failures[host] >= self.host_failure_limit:
                                blocked_hosts.add(host)
                        return None
                    async with circuit_lock:
                        host_failures[host] = 0
                    if res:
                        res.parent_entity_id = entity.id
                        res.depth = entity.depth + 1
                    return res
                
        tasks = [bound_check(site) for site in sites]
        completed = await asyncio.gather(*tasks, return_exceptions=True)
        
        for res in completed:
            if isinstance(res, SocialProfileEntity):
                results.append(res)
                
        return results

    def is_available(self) -> bool:
        return True
