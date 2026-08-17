import asyncio
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability, DarkWebMention

class IntelXModule(BaseModule):
    name = "IntelligenceX"
    description = "Search IntelligenceX for data leaks and dark web mentions"
    version = "1.0.0"
    accepts = [EntityType.EMAIL, EntityType.PHONE, EntityType.DOMAIN]
    produces = [EntityType.LEAK_RECORD, EntityType.DARK_WEB_MENTION]
    
    DAILY_LIMIT = 10
    _usage_lock = threading.RLock()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.usage_path = Path(self.config.storage.state_dir) / "intelx_usage.json"

    def _load_daily_usage(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            with self.usage_path.open("r", encoding="utf-8") as stream:
                payload = json.load(stream)
            if payload.get("date") == today:
                return max(0, int(payload.get("count", 0)))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return 0

    def _save_daily_usage(self, count: int) -> None:
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "date": datetime.now(timezone.utc).date().isoformat(),
            "count": count,
        }
        with self._usage_lock:
            handle, temp_name = tempfile.mkstemp(
                prefix=".intelx_usage.", suffix=".tmp", dir=self.usage_path.parent
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    json.dump(payload, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, self.usage_path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)

    def _increment_daily_usage(self) -> int:
        with self._usage_lock:
            count = self._load_daily_usage() + 1
            self._save_daily_usage(count)
            return count
        
    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        results: List[Entity] = []
        api_key = self._get_api_key()
        
        if not api_key:
            return results
            
        if self._load_daily_usage() >= self.DAILY_LIMIT:
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
            search_url = f"{self.config.intelx.base_url}/intelligent/search"
            search_resp = await self.client.post(search_url, headers=headers, json=payload)
            self._increment_daily_usage()
            
            if search_resp.status_code == 200:
                data = search_resp.json()
                search_id = data.get("id")
                
                if not search_id:
                    return results
                    
                # Poll results
                for _ in range(3):
                    await asyncio.sleep(2)
                    result_url = (
                        f"{self.config.intelx.base_url}/intelligent/search/result?id={search_id}"
                    )
                    res_resp = await self.client.get(result_url, headers=headers)
                    
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
        return bool(self._get_api_key())
