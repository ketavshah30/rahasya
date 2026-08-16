import asyncio
import json
import os
import re
import subprocess
import tempfile
import time
from typing import List

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability, SocialProfileEntity, EmailEntity
from rahasya.storage.network_audit import record_audit_event

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
            
        temp = tempfile.NamedTemporaryFile(
            prefix=f"maigret_report_{scan_id}_", suffix=".json", delete=False
        )
        temp_file = temp.name
        temp.close()
        
        try:
            # Try to run maigret via subprocess
            cmd = [
                "maigret", target,
                "--json", temp_file,
                "--no-color",
                "--timeout", "10"
            ]
            process_started = time.monotonic()
            record_audit_event(
                "provider_process_started",
                outcome="started",
                provider="maigret",
                target=target,
            )
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            record_audit_event(
                "provider_process_completed",
                outcome="success" if process.returncode == 0 else "failed",
                provider="maigret",
                return_code=process.returncode,
                duration_ms=round((time.monotonic() - process_started) * 1000, 2),
                stdout=stdout.decode(errors="replace")[-500:] if stdout else None,
                error=stderr.decode(errors="replace")[-500:] if stderr else None,
            )
            
            if os.path.exists(temp_file):
                with open(temp_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                report = data.get("report", {}).get(target, {})
                for site, info in report.items():
                    status = str(info.get("status", "unknown")).casefold()
                    url = info.get("url_user", "") or info.get("url_main", "")
                    found = status == "found" or "claimed" in status
                    record_audit_event(
                        "provider_site_check",
                        outcome="success" if found else ("failed" if "error" in status else "not_found"),
                        url=url or None,
                        provider="maigret",
                        site=site,
                        provider_status=status,
                    )
                    if found:
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
                                
        except Exception as e:
            record_audit_event(
                "provider_process_failed",
                outcome="failed",
                provider="maigret",
                target=target,
                error_type=type(e).__name__,
                error=str(e),
            )
            self.logger.error(f"Maigret execution failed: {e}")
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
            
        return results

    def is_available(self) -> bool:
        """Check if maigret CLI is installed on the system."""
        import shutil
        return shutil.which("maigret") is not None
