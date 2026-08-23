"""Shared preconditions for dark-web modules (FIXES_NEW.md §H.2).

Modules do not have direct access to the orchestrator's scan state (that
would create a circular dependency: orchestrator imports ModuleRegistry).
Instead the orchestrator registers into a process-local scoreboard, and
dark-web modules read from it.

The scoreboard is intentionally tiny — only the boolean "has this scan
registered at least one confirmed SOCIAL_PROFILE?" — because that is the
precondition FIXES_NEW.md §H.2 specifies. Later workstreams (G/J) will
replace it with a full PersonProfile object.
"""

from threading import Lock
from typing import Dict, Set

_LOCK = Lock()
_SCANS_WITH_CONFIRMED_PROFILE: Set[str] = set()
_SCAN_CONFIRMED_PLATFORMS: Dict[str, Set[str]] = {}


def note_confirmed_social_profile(scan_id: str, platform: str = "") -> None:
    """Register that this scan has at least one confirmed SOCIAL_PROFILE.

    Called from the orchestrator whenever a ground-truth SOCIAL_PROFILE is
    registered into a scan's state.
    """
    if not scan_id:
        return
    with _LOCK:
        _SCANS_WITH_CONFIRMED_PROFILE.add(scan_id)
        if platform:
            _SCAN_CONFIRMED_PLATFORMS.setdefault(scan_id, set()).add(platform.lower())


def scan_has_confirmed_social_profile(scan_id: str) -> bool:
    """True iff the scan has previously registered a ground-truth SOCIAL_PROFILE."""
    if not scan_id:
        return False
    with _LOCK:
        return scan_id in _SCANS_WITH_CONFIRMED_PROFILE


def confirmed_platforms(scan_id: str) -> Set[str]:
    """Return the set of platform slugs confirmed on this scan (lowercased)."""
    with _LOCK:
        return set(_SCAN_CONFIRMED_PLATFORMS.get(scan_id, ()))


def clear_scan(scan_id: str) -> None:
    """Drop scoreboard state for a completed scan (memory hygiene)."""
    with _LOCK:
        _SCANS_WITH_CONFIRMED_PROFILE.discard(scan_id)
        _SCAN_CONFIRMED_PLATFORMS.pop(scan_id, None)
