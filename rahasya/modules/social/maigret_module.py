"""Maigret CLI adapter using its current NDJSON report format."""

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

from rahasya.core.models import (
    EmailEntity,
    Entity,
    EntityType,
    SocialProfileEntity,
    SourceReliability,
)
from rahasya.modules.base import BaseModule
from rahasya.storage.network_audit import record_audit_event


class MaigretModule(BaseModule):
    name = "Maigret"
    description = "Username enumerator across 3000+ sites"
    version = "1.0.0"
    accepts = [EntityType.USERNAME, EntityType.EMAIL, EntityType.PERSON]
    produces = [EntityType.SOCIAL_PROFILE, EntityType.URL, EntityType.EMAIL, EntityType.USERNAME]
    rate_limit = 0.0

    @staticmethod
    def _command(target: str, tmpdir: str) -> List[str]:
        return [
            "maigret", target,
            "--json", "ndjson",
            "--folderoutput", tmpdir,
            "--no-color",
            "--timeout", "10",
            "--retries", "1",
        ]

    @staticmethod
    def _report_files(tmpdir: str) -> Iterable[Path]:
        root = Path(tmpdir)
        return sorted({*root.glob("report_*_ndjson.json"), *root.glob("report_*.ndjson")})

    @staticmethod
    def _status_text(info: Dict[str, Any]) -> str:
        status = info.get("status", "claimed")
        if isinstance(status, dict):
            status = status.get("status", status.get("value", "claimed"))
        return str(status).casefold()

    @classmethod
    def _read_ndjson(cls, tmpdir: str) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for report_path in cls._report_files(tmpdir):
            with report_path.open("r", encoding="utf-8") as stream:
                for line_number, line in enumerate(stream, start=1):
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError as exc:
                        record_audit_event(
                            "provider_report_parse_failed",
                            outcome="failed",
                            provider="maigret",
                            report_file=report_path.name,
                            line_number=line_number,
                            error=str(exc),
                        )
                        continue
                    if isinstance(item, dict):
                        records.append(item)
        return records

    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        results: List[Entity] = []
        target = entity.value.split("@", 1)[0] if entity.entity_type == EntityType.EMAIL else entity.value
        tmpdir = tempfile.mkdtemp(prefix=f"maigret_{scan_id}_")

        try:
            # Maigret 0.6 calls the directory switch --folderoutput and
            # treats --json as a format selector (simple or ndjson).
            cmd = self._command(target, tmpdir)
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
                stderr=subprocess.PIPE,
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

            for info in self._read_ndjson(tmpdir):
                site_data: Dict[str, Any] = info["site"] if isinstance(info.get("site"), dict) else {}
                site = str(info.get("sitename") or site_data.get("name") or "Unknown")
                status = self._status_text(info)
                url = str(info.get("url_user") or site_data.get("url") or info.get("url") or "")
                found = any(marker in status for marker in ("claimed", "found", "true"))
                record_audit_event(
                    "provider_site_check",
                    outcome="success" if found else ("failed" if "error" in status else "not_found"),
                    url=url or None,
                    provider="maigret",
                    site=site,
                    provider_status=status,
                )
                if not found or not url:
                    continue

                tags = info.get("tags") or site_data.get("tags") or []
                bio = str(info.get("about") or info.get("bio") or "")
                profile_entity = SocialProfileEntity(
                    value=url,
                    normalized_value=url.casefold().strip(),
                    source_module=self.name,
                    source_reliability=SourceReliability.HIGH,
                    confidence=0.85,
                    metadata={"site": site, "tags": tags, "raw_data": info},
                    parent_entity_id=entity.id,
                    depth=entity.depth + 1,
                    url=url,
                    platform=site,
                    bio=bio,
                )
                results.append(profile_entity)

                for email in re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", bio):
                    email_value = email.strip()
                    results.append(EmailEntity(
                        value=email_value,
                        normalized_value=email_value.casefold(),
                        source_module=self.name,
                        source_reliability=SourceReliability.MEDIUM,
                        confidence=0.7,
                        metadata={"found_in_bio": site},
                        parent_entity_id=profile_entity.id,
                        depth=profile_entity.depth + 1,
                        address=email_value,
                        domain=email_value.rsplit("@", 1)[-1],
                    ))
        except Exception as exc:
            record_audit_event(
                "provider_process_failed",
                outcome="failed",
                provider="maigret",
                target=target,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self.logger.error(f"Maigret execution failed: {exc}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        return results

    def is_available(self) -> bool:
        return shutil.which("maigret") is not None
