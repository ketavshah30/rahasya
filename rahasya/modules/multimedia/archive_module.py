import asyncio
from typing import List

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability
from rahasya.utils.http_client import StealthHTTPClient

class ArchiveModule(BaseModule):
    name = "WaybackMachine"
    description = "Search Internet Archive for historical snapshots"
    version = "1.0.0"
    accepts = [EntityType.URL, EntityType.DOMAIN]
    produces = [EntityType.URL]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.http_client = StealthHTTPClient()
        
    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        results = []
        url = entity.value
        
        # 1. Availability API
        try:
            avail_url = f"https://archive.org/wayback/available?url={url}"
            avail_resp = await self.http_client.get(avail_url)
            
            if avail_resp.status_code == 200:
                data = avail_resp.json()
                snapshots = data.get("archived_snapshots", {})
                closest = snapshots.get("closest")
                
                if closest and closest.get("available"):
                    archive_url = closest.get("url")
                    timestamp = closest.get("timestamp")
                    
                    arch_entity = Entity(
                        entity_type=EntityType.URL,
                        value=archive_url,
                        normalized_value=archive_url.lower().strip(),
                        source_module=self.name,
                        source_reliability=SourceReliability.HIGH,
                        confidence=1.0,
                        metadata={"timestamp": timestamp, "type": "closest_snapshot"},
                        parent_entity_id=entity.id,
                        depth=entity.depth + 1
                    )
                    results.append(arch_entity)
        except Exception as e:
            self.logger.error(f"Archive availability failed: {e}")
            
        # 2. CDX API (last 20)
        try:
            cdx_url = f"https://web.archive.org/cdx/search/cdx?url={url}&output=json&limit=20"
            cdx_resp = await self.http_client.get(cdx_url)
            
            if cdx_resp.status_code == 200:
                data = cdx_resp.json()
                if isinstance(data, list) and len(data) > 1:
                    headers = data[0]
                    rows = data[1:]
                    
                    for row in rows:
                        item = dict(zip(headers, row))
                        timestamp = item.get("timestamp", "")
                        status = item.get("statuscode", "")
                        if timestamp:
                            archive_url = f"https://web.archive.org/web/{timestamp}/{url}"
                            
                            cdx_ent = Entity(
                                entity_type=EntityType.URL,
                                value=archive_url,
                                normalized_value=archive_url.lower().strip(),
                                source_module=self.name,
                                source_reliability=SourceReliability.HIGH,
                                confidence=0.9,
                                metadata={"timestamp": timestamp, "status_code": status, "type": "cdx_snapshot"},
                                parent_entity_id=entity.id,
                                depth=entity.depth + 1
                            )
                            results.append(cdx_ent)
        except Exception as e:
            self.logger.error(f"CDX API failed: {e}")
            
        return results

    def is_available(self) -> bool:
        return True
