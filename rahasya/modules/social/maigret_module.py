import asyncio
import json
import os
import re
import subprocess
import tempfile
from typing import List

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability, SocialProfileEntity, EmailEntity

class MaigretModule(BaseModule):
    name = "Maigret"
    description = "Username enumerator across 3000+ sites"
    version = "1.0.0"
    accepts = [EntityType.USERNAME, EntityType.EMAIL, EntityType.PERSON]
    produces = [EntityType.SOCIAL_PROFILE, EntityType.URL, EntityType.EMAIL, EntityType.USERNAME]
    
    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        results = []
        target = entity.value
        
        # We might have an email, we need to extract the username part if it's an email
        if entity.entity_type == EntityType.EMAIL:
            target = target.split("@")[0]
            
        temp_dir = tempfile.gettempdir()
        temp_file = os.path.join(temp_dir, f"maigret_report_{scan_id}.json")
        
        try:
            # Try to run maigret via subprocess
            cmd = [
                "maigret", target,
                "--json", temp_file,
                "--no-color",
                "--timeout", "10"
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if os.path.exists(temp_file):
                with open(temp_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                report = data.get("report", {}).get(target, {})
                for site, info in report.items():
                    if info.get("status") == "found":
                        url = info.get("url_user", "")
                        if not url:
                            continue
                            
                        # Extract tags/data
                        tags = info.get("tags", [])
                        bio = info.get("about", "")
                        
                        profile_entity = SocialProfileEntity(
                            entity_type=EntityType.SOCIAL_PROFILE,
                            value=url,
                            normalized_value=url.lower().strip(),
                            source_module=self.name,
                            source_reliability=SourceReliability.HIGH,
                            confidence=0.85,
                            metadata={"site": site, "tags": tags, "raw_data": info},
                            parent_entity_id=entity.id,
                            depth=entity.depth + 1,
                            url=url,
                            platform=site,
                            bio=bio
                        )
                        results.append(profile_entity)
                        
                        # Extract emails from bio
                        if bio:
                            email_matches = re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', bio)
                            for email in email_matches:
                                email_val = email.strip()
                                email_entity = EmailEntity(
                                    entity_type=EntityType.EMAIL,
                                    value=email_val,
                                    normalized_value=email_val.lower().strip(),
                                    source_module=self.name,
                                    source_reliability=SourceReliability.MEDIUM,
                                    confidence=0.7,
                                    metadata={"found_in_bio": site},
                                    parent_entity_id=profile_entity.id,
                                    depth=profile_entity.depth + 1,
                                    address=email_val,
                                    domain=email_val.split("@")[-1] if "@" in email_val else ""
                                )
                                results.append(email_entity)
                                
                # Cleanup
                os.remove(temp_file)
                
        except Exception as e:
            self.logger.error(f"Maigret execution failed: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except:
                    pass
            
        return results

    def is_available(self) -> bool:
        """Check if maigret CLI is installed on the system."""
        import shutil
        return shutil.which("maigret") is not None
