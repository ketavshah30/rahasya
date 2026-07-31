import asyncio
import json
import os
import subprocess
import tempfile
from typing import List

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability, SocialProfileEntity

class SherlockModule(BaseModule):
    name = "Sherlock"
    description = "Hunt down social media accounts by username"
    version = "1.0.0"
    accepts = [EntityType.USERNAME, EntityType.EMAIL]
    produces = [EntityType.SOCIAL_PROFILE, EntityType.URL]
    
    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        results = []
        target = entity.value
        
        if entity.entity_type == EntityType.EMAIL:
            target = target.split("@")[0]
            
        temp_dir = tempfile.gettempdir()
        temp_file = os.path.join(temp_dir, f"sherlock_report_{scan_id}.json")
        
        try:
            cmd = [
                "sherlock", target,
                "--print-all",
                "--output", temp_file,
                "--json", temp_file
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            await process.communicate()
            
            if os.path.exists(temp_file):
                with open(temp_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                # data is typically a dict with the username as key or directly site -> url
                target_data = data.get(target, data)
                
                for site, url in target_data.items():
                    if isinstance(url, dict):
                        url = url.get("url_main", "") or url.get("url_user", "")
                    
                    if url and isinstance(url, str):
                        profile = SocialProfileEntity(
                            entity_type=EntityType.SOCIAL_PROFILE,
                            value=url,
                            normalized_value=url.lower().strip(),
                            source_module=self.name,
                            source_reliability=SourceReliability.MEDIUM,
                            confidence=0.75,
                            metadata={"site": site},
                            parent_entity_id=entity.id,
                            depth=entity.depth + 1,
                            url=url,
                            platform=site
                        )
                        results.append(profile)
                        
                os.remove(temp_file)
                
        except Exception as e:
            self.logger.error(f"Sherlock execution failed: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass
            
        return results

    def is_available(self) -> bool:
        import shutil
        return shutil.which('sherlock') is not None
