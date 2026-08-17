"""Sherlock CLI adapter using its supported CSV report output."""

import asyncio
import csv
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List

from rahasya.core.models import Entity, EntityType, SocialProfileEntity, SourceReliability
from rahasya.modules.base import BaseModule
from rahasya.storage.network_audit import record_audit_event


class SherlockModule(BaseModule):
    name = "Sherlock"
    description = "Hunt down social media accounts by username"
    version = "1.0.0"
    accepts = [EntityType.USERNAME, EntityType.EMAIL]
    produces = [EntityType.SOCIAL_PROFILE, EntityType.URL]
    rate_limit = 0.0

    @staticmethod
    def _command(target: str, tmpdir: str) -> List[str]:
        return [
            "sherlock", target,
            "--print-all",
            "--folderoutput", tmpdir,
            "--csv",
            "--no-txt",
            "--no-color",
            "--timeout", "10",
        ]

    @staticmethod
    def _is_claimed(status: str) -> bool:
        normalized = status.casefold()
        return any(marker in normalized for marker in ("claimed", "found", "true"))

    @staticmethod
    def _read_report(tmpdir: str, target: str) -> List[Dict[str, str]]:
        expected = Path(tmpdir) / f"{target}.csv"
        candidates = [expected] if expected.exists() else sorted(Path(tmpdir).glob("*.csv"))
        rows: List[Dict[str, str]] = []
        for report_path in candidates:
            with report_path.open("r", encoding="utf-8-sig", newline="") as stream:
                rows.extend(dict(row) for row in csv.DictReader(stream))
        return rows

    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        results: List[Entity] = []
        target = entity.value.split("@", 1)[0] if entity.entity_type == EntityType.EMAIL else entity.value
        tmpdir = tempfile.mkdtemp(prefix=f"sherlock_{scan_id}_")

        try:
            # Sherlock 0.16's --json option selects an input site database;
            # CSV is its machine-readable result format.
            cmd = self._command(target, tmpdir)
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
                stderr=subprocess.PIPE,
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

            for site_result in self._read_report(tmpdir, target):
                site = site_result.get("name", "Unknown")
                status = site_result.get("exists", "unknown")
                url = site_result.get("url_user") or site_result.get("url_main") or ""
                found = self._is_claimed(status)
                record_audit_event(
                    "provider_site_check",
                    outcome="success" if found else ("failed" if "error" in status.casefold() else "not_found"),
                    url=url or None,
                    provider="sherlock",
                    site=site,
                    provider_status=status,
                    status_code=site_result.get("http_status") or None,
                    duration_seconds=site_result.get("response_time_s") or None,
                )
                if not found or not url:
                    continue
                results.append(SocialProfileEntity(
                    value=url,
                    normalized_value=url.casefold().strip(),
                    source_module=self.name,
                    source_reliability=SourceReliability.MEDIUM,
                    confidence=0.75,
                    metadata={"site": site, "provider_status": status},
                    parent_entity_id=entity.id,
                    depth=entity.depth + 1,
                    url=url,
                    platform=site,
                ))
        except Exception as exc:
            record_audit_event(
                "provider_process_failed",
                outcome="failed",
                provider="sherlock",
                target=target,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self.logger.error(f"Sherlock execution failed: {exc}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        return results

    def is_available(self) -> bool:
        return shutil.which("sherlock") is not None
