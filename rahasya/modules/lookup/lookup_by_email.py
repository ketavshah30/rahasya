"""LookupByEmail — email → account existence probing via holehe.

FIXES_NEW.md §E.1.

Given a ground-truth email, holehe checks 120+ sites' password-reset /
registration endpoints and reports which ones respond "an account already
exists for this email." Every hit is a *directly-attested* fact tying the
email to that platform — this is the correct pivot from an email to a real
identity, replacing the previous "just strip everything before @ and hope
that's the username" hack that Sherlock/Maigret used to do.

Shell-out adapter shape (operator decision #1 in FIXES_NEW.md §5): same
subprocess pattern as SherlockModule / MaigretModule so we do not import
holehe's Python module and drag in its own httpx/aiohttp copies.
"""

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from rahasya.core.models import (
    Entity,
    EntityType,
    SocialProfileEntity,
    SourceReliability,
)
from rahasya.modules.base import BaseModule
from rahasya.storage.network_audit import record_audit_event


class LookupByEmailModule(BaseModule):
    name = "LookupByEmail"
    description = (
        "Reverse-lookup: which sites have an account registered under this "
        "email? Shell-out to holehe."
    )
    version = "1.0.0"
    accepts = [EntityType.EMAIL]
    produces = [EntityType.SOCIAL_PROFILE, EntityType.USERNAME]
    # holehe paces its own per-site requests internally; we still cap our
    # own aggregate rate at 3 rps just in case.
    rate_limit = 3.0

    @staticmethod
    def _binary() -> Optional[str]:
        return shutil.which("holehe")

    def is_available(self) -> bool:
        # FIXES_NEW.md §I.1: this module is part of the free-tier default
        # set — available iff the holehe CLI is on PATH, no API key needed.
        return self._binary() is not None

    @staticmethod
    def _command(target: str, tmpdir: str) -> List[str]:
        # holehe's stable machine-readable output is --only-used + --no-color
        # printed to stdout; some versions also support `--output <dir>` to
        # write JSON. We parse both.
        return [
            "holehe",
            target,
            "--only-used",
            "--no-color",
            "--no-clear",
        ]

    @staticmethod
    def _parse_stdout(stdout: str) -> List[Dict[str, Any]]:
        """Parse holehe's line-oriented stdout when JSON output isn't available.

        Recent holehe versions print lines like:
            [+] instagram.com
            [+] twitter.com [Full account information: ...]
            [-] pinterest.com
        We capture the [+] hits.
        """
        hits: List[Dict[str, Any]] = []
        for raw in stdout.splitlines():
            line = raw.strip()
            if not line:
                continue
            # Try JSON-per-line first (some holehe forks emit that).
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                obj = None
            if isinstance(obj, dict) and obj.get("exists") is True:
                hits.append(obj)
                continue
            # Fall back to `[+] domain [...]` line format.
            match = re.match(r"^\[\+\]\s+([a-zA-Z0-9._-]+\.[a-zA-Z]{2,})", line)
            if match:
                hits.append({
                    "name": match.group(1),
                    "domain": match.group(1),
                    "exists": True,
                    "raw_line": line,
                })
        return hits

    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        if entity.entity_type != EntityType.EMAIL:
            return []
        # Only pivot from a ground-truth email — that is by construction the
        # seed email or a directly-attested email from another provider.
        # Guessed / bio-scraped emails do not warrant a full holehe pass yet.
        if not getattr(entity, "is_ground_truth", False):
            record_audit_event(
                "module_skipped",
                outcome="skipped",
                provider="holehe",
                entity_type=entity.entity_type.value,
                entity_value=entity.value,
                reason="candidate_email_not_corroborated",
            )
            return []

        target = entity.value.strip()
        results: List[Entity] = []
        tmpdir = tempfile.mkdtemp(prefix=f"holehe_{scan_id}_")

        try:
            cmd = self._command(target, tmpdir)
            started = time.monotonic()
            record_audit_event(
                "provider_process_started",
                outcome="started",
                provider="holehe",
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
                provider="holehe",
                return_code=process.returncode,
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                stdout=stdout.decode(errors="replace")[-500:] if stdout else None,
                error=stderr.decode(errors="replace")[-500:] if stderr else None,
            )

            hits = self._parse_stdout(stdout.decode(errors="replace"))
            for hit in hits:
                site = str(hit.get("name") or hit.get("domain") or "unknown")
                url = f"https://{site.strip('/')}"
                # The email→platform edge is directly attested (holehe
                # verified this platform's own reset endpoint acknowledges
                # the address). The *handle* on that platform is NOT
                # attested — holehe rarely returns one.
                results.append(SocialProfileEntity(
                    value=url,
                    normalized_value=url.casefold().strip(),
                    source_module=self.name,
                    source_reliability=SourceReliability.HIGH,
                    confidence=0.9,
                    metadata={
                        "site": site,
                        "raw": {k: v for k, v in hit.items() if k != "raw_line"},
                        "attested_via": "holehe_reset_endpoint",
                    },
                    parent_entity_id=entity.id,
                    depth=entity.depth + 1,
                    url=url,
                    platform=site,
                    # Ground truth of the fact "an account with this email
                    # exists on this platform" — the platform's own reset
                    # flow attested it.
                    is_ground_truth=True,
                    evidence_urls=[url],
                    attests_platform=site,
                ))
        except Exception as exc:
            record_audit_event(
                "provider_process_failed",
                outcome="failed",
                provider="holehe",
                target=target,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self.logger.error(f"holehe execution failed: {exc}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        return results
