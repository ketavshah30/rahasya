import asyncio
import json
import os
import subprocess
import tempfile
import time
from typing import List

from rahasya.modules.base import BaseModule
from rahasya.core.models import Entity, EntityType, SourceReliability, SocialProfileEntity
from rahasya.storage.network_audit import record_audit_event

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
            
        temp = tempfile.NamedTemporaryFile(
            prefix=f"sherlock_report_{scan_id}_", suffix=".json", delete=False
        )
        temp_file = temp.name
        temp.close()
        
        try:
            cmd = [
                "sherlock", target,
                "--print-all",
                "--output", temp_file,
                "--json", temp_file
            ]
            process_started = time.monotonic()
            record_audit_event(
                "provider_process_started",
                outcome="started",
                provider="sherlock",
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
                provider="sherlock",
                return_code=process.returncode,
                duration_ms=round((time.monotonic() - process_started) * 1000, 2),
                stdout=stdout.decode(errors="replace")[-500:] if stdout else None,
                error=stderr.decode(errors="replace")[-500:] if stderr else None,
            )
            
            if os.path.exists(temp_file):
                with open(temp_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    
                # data is typically a dict with the username as key or directly site -> url
                target_data = data.get(target, data)
                
                for site, site_result in target_data.items():
                    status = "found"
                    url = site_result
                    if isinstance(site_result, dict):
                        status_value = site_result.get("status", site_result.get("exists", "unknown"))
                        if isinstance(status_value, dict):
                            status_value = status_value.get("status", status_value)
                        status = str(status_value).casefold()
                        url = site_result.get("url_user", "") or site_result.get("url_main", "")
                    found = bool(url and isinstance(url, str))
                    if any(marker in status for marker in ("available", "not found", "not_found", "false")):
                        found = False
                    elif any(marker in status for marker in ("claimed", "found", "true")):
                        found = True
                    record_audit_event(
                        "provider_site_check",
                        outcome="success" if found else ("failed" if "error" in status else "not_found"),
                        url=url if isinstance(url, str) else None,
                        provider="sherlock",
                        site=site,
                        provider_status=status,
                    )
                    
                    if found and url and isinstance(url, str):
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
                        
        except Exception as e:
            record_audit_event(
                "provider_process_failed",
                outcome="failed",
                provider="sherlock",
                target=target,
                error_type=type(e).__name__,
                error=str(e),
            )
            self.logger.error(f"Sherlock execution failed: {e}")
        finally:
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
            
        return results

    def is_available(self) -> bool:
        import shutil
        return shutil.which('sherlock') is not None
