"""Provider health probe (FIXES_NEW.md §I.4).

Runs a cheap, well-behaved probe against each configured provider so the
operator can see *why* a keyed module isn't running instead of watching it
silently self-skip. Results are logged, returned as a dict, and persisted
to `data/state/provider_health.json` under STORAGE__STATE_DIR.
"""

import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from rahasya.config import Settings, settings
from rahasya.utils.logging import get_logger

logger = get_logger("provider_health")


@dataclass
class ProviderStatus:
    provider: str
    status: str  # "green" | "yellow" | "red"
    reason: str
    detail: Optional[str] = None
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def _cli_status(cmd: str, purpose: str) -> ProviderStatus:
    path = shutil.which(cmd)
    if path:
        return ProviderStatus(
            provider=cmd,
            status="green",
            reason="cli_available",
            detail=path,
        )
    return ProviderStatus(
        provider=cmd,
        status="red",
        reason="cli_not_found",
        detail=f"'{cmd}' is not on PATH (needed for {purpose}).",
    )


def _key_status(name: str, value: Optional[str], purpose: str) -> ProviderStatus:
    if value:
        return ProviderStatus(
            provider=name,
            status="green",
            reason="api_key_configured",
            detail=f"Adds: {purpose}",
        )
    return ProviderStatus(
        provider=name,
        status="yellow",
        reason="api_key_not_configured",
        detail=f"Optional. Without a key: {purpose}",
    )


async def probe_provider_health(config: Optional[Settings] = None) -> Dict[str, ProviderStatus]:
    """Return provider name → ProviderStatus for every module family.

    Intentionally does not hit the network (cheap by design). Network
    liveness for HIBP/IntelX is left to the module-level audit trail.
    """
    config = config or settings
    matrix: Dict[str, ProviderStatus] = {}

    # Shell-out reverse-lookup CLIs (§E.1, §E.2, §F).
    for cmd, purpose in (
        ("holehe", "reverse-lookup: email → accounts"),
        ("ignorant", "reverse-lookup: phone → accounts"),
        ("sherlock", "username enumerator"),
        ("maigret", "username enumerator"),
    ):
        matrix[cmd] = _cli_status(cmd, purpose)
    # WhatsMyName is a pure-Python module (no CLI) — record its "always
    # available" status so the operator does not think it is missing.
    matrix["whatsmyname"] = ProviderStatus(
        provider="whatsmyname",
        status="green",
        reason="module_available",
        detail="Pure-Python username enumerator (no CLI required).",
    )

    # Keyed / optional API modules (§I.2, §I.3).
    matrix["hibp"] = _key_status(
        "hibp",
        (config.api_keys.hibp or (config.api_keys.hibp_keys[0] if config.api_keys.hibp_keys else None)),
        "named-breach attribution — without a key, holehe still detects the accounts but without named-breach names.",
    )
    matrix["intelx"] = _key_status(
        "intelx",
        (config.api_keys.intelx or (config.api_keys.intelx_keys[0] if config.api_keys.intelx_keys else None)),
        "additional paste / leak coverage.",
    )
    matrix["leaklookup"] = _key_status(
        "leaklookup",
        config.api_keys.leaklookup,
        "extra leak dataset coverage.",
    )
    matrix["hunter"] = _key_status(
        "hunter",
        getattr(config.api_keys, "hunter", None),
        "email → company / co-worker candidates (feeds §G related-person expansion).",
    )

    # GitHub — keyless but rate-limit differs.
    import os
    github_token = os.getenv("GITHUB_TOKEN")
    matrix["github"] = ProviderStatus(
        provider="github",
        status="green" if github_token else "yellow",
        reason="github_token_configured" if github_token else "github_token_not_configured",
        detail=(
            "30 req/min authenticated" if github_token
            else "10 req/min unauthenticated (recommended for multi-target scans: export GITHUB_TOKEN)"
        ),
    )

    # Tor (§H.2 gating).
    matrix["tor"] = ProviderStatus(
        provider="tor",
        status="green" if config.tor.enabled else "yellow",
        reason="tor_enabled" if config.tor.enabled else "tor_disabled",
        detail=(
            f"SOCKS at {config.tor.socks_host}:{config.tor.socks_port}"
            if config.tor.enabled
            else "TOR__ENABLED=false — OnionSearch will self-skip."
        ),
    )

    # Persist for the dashboard to render (§I.4 badge).
    try:
        state_dir = Path(config.storage.state_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "provider_health.json").write_text(
            json.dumps({name: asdict(status) for name, status in matrix.items()}, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Could not persist provider_health.json: {exc}")

    return matrix


def format_health_matrix(matrix: Dict[str, ProviderStatus]) -> str:
    """Render the matrix as a compact table for CLI display."""
    rows = ["provider           status  reason                              detail"]
    for name, status in sorted(matrix.items()):
        rows.append(
            f"{name:<18} {status.status:<6}  {status.reason:<34}  {status.detail or ''}"
        )
    return "\n".join(rows)
