"""Coherent browser network fingerprints (no browser engine required).

The anti-bot problem Rahasya hits is a *network-layer* fingerprint problem, not a
JavaScript-rendering problem. WAFs such as Cloudflare / Akamai / PerimeterX classify a
client from three signals that are visible before any page renders:

1. **TLS fingerprint (JA3)** and **HTTP/2 settings** — a plain Python ``httpx`` client has a
   recognisably non-browser JA3 and only speaks HTTP/1.1 unless ``h2`` is installed. This is the
   single strongest tell and is handled at the transport layer by ``curl_cffi`` impersonation
   (see :mod:`rahasya.utils.http_client`). This module does not touch TLS.
2. **Header set and ordering** — a real browser sends a specific, internally consistent set of
   headers: ``User-Agent`` plus matching ``Sec-CH-UA*`` client hints, ``Sec-Fetch-*`` metadata,
   a browser-shaped ``Accept``/``Accept-Encoding``/``Accept-Language``. Incoherent hints (a
   Firefox UA that also sends Chromium ``Sec-CH-UA``) are themselves a block signal.
3. **Per-session stability** — a browser does not change its User-Agent between the first request
   and its retry. Rotating the UA mid-conversation looks like a bot.

This module owns concern (2) and (3): it produces a *coherent* header set for a chosen
User-Agent, and a client picks ONE profile and reuses it for its whole lifetime.

No Playwright / Camoufox / real browser is used anywhere.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional


@dataclass(frozen=True)
class BrowserProfile:
    """A coherent set of fingerprint attributes for one virtual browser."""

    user_agent: str
    # curl_cffi impersonation target, e.g. "chrome124". None => let the fallback decide.
    impersonate: Optional[str]
    sec_ch_ua: Optional[str]
    platform: str  # Sec-CH-UA-Platform value, e.g. '"Windows"'
    mobile: bool
    accept_language: str = "en-US,en;q=0.9"

    def base_headers(self) -> Dict[str, str]:
        """Return a browser-coherent default header set for a top-level navigation.

        Callers may override individual values (e.g. an API client overriding ``Accept`` to
        ``application/json`` and ``Sec-Fetch-Dest`` to ``empty``).
        """
        headers: Dict[str, str] = {
            "User-Agent": self.user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": self.accept_language,
            # br/zstd are what current Chrome/Firefox advertise; curl_cffi transparently
            # decompresses, and httpx handles gzip/deflate/br when brotli is present.
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            # Sec-Fetch-* for a user-initiated top-level navigation.
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
        # Client hints are Chromium-only. Firefox / Safari must NOT send them — doing so is
        # itself incoherent and flagged by WAFs.
        if self.sec_ch_ua:
            headers["Sec-CH-UA"] = self.sec_ch_ua
            headers["Sec-CH-UA-Mobile"] = "?1" if self.mobile else "?0"
            headers["Sec-CH-UA-Platform"] = self.platform
        return headers


# --- Chromium client-hint helpers -------------------------------------------------------------

def _chromium_sec_ch_ua(major: str, brand: str = "Google Chrome") -> str:
    """Build a plausible Sec-CH-UA value for a given Chromium major version.

    Mirrors the real format: a greased brand, the Chromium brand, and the vendor brand.
    """
    return (
        f'"Not;A=Brand";v="99", '
        f'"Chromium";v="{major}", '
        f'"{brand}";v="{major}"'
    )


_CHROME_RE = re.compile(r"Chrome/(\d+)")
_EDGE_RE = re.compile(r"Edg/(\d+)")
_OPERA_RE = re.compile(r"OPR/(\d+)")
_FIREFOX_RE = re.compile(r"Firefox/(\d+)")


def _platform_from_ua(ua: str) -> str:
    if "Android" in ua:
        return '"Android"'
    if "iPhone" in ua or "iPad" in ua:
        return '"iOS"'
    if "Macintosh" in ua or "Mac OS X" in ua:
        return '"macOS"'
    if "Linux" in ua:
        return '"Linux"'
    return '"Windows"'


def _impersonate_from_ua(ua: str, available: List[str]) -> Optional[str]:
    """Choose the closest curl_cffi impersonation target for a UA string.

    ``available`` is the set of impersonation targets the installed curl_cffi actually supports;
    we pick the newest Chrome target that is <= the UA's Chrome major so TLS/UA stay coherent, and
    gracefully degrade to the newest available when we cannot match exactly.
    """
    chrome_targets = sorted(
        (int(m.group(1)) for t in available if (m := re.fullmatch(r"chrome(\d+)", t))),
        reverse=True,
    )
    firefox_targets = sorted(
        (int(m.group(1)) for t in available if (m := re.fullmatch(r"firefox(\d+)", t))),
        reverse=True,
    )
    safari_targets = [t for t in available if t.startswith("safari")]

    m = _CHROME_RE.search(ua)
    if m:  # Chrome, Edge, Opera, Brave, Vivaldi all carry Chrome/<n>
        want = int(m.group(1))
        for cand in chrome_targets:
            if cand <= want:
                return f"chrome{cand}"
        return f"chrome{chrome_targets[0]}" if chrome_targets else None

    m = _FIREFOX_RE.search(ua)
    if m and firefox_targets:
        want = int(m.group(1))
        for cand in firefox_targets:
            if cand <= want:
                return f"firefox{cand}"
        return f"firefox{firefox_targets[0]}"

    if ("Safari" in ua) and ("Chrome" not in ua) and safari_targets:
        want_ios = ("iPhone" in ua) or ("iPad" in ua)
        # Keep the TLS profile coherent with the UA's platform: a desktop Safari UA must NOT map
        # to an *_ios target (and vice versa). This previously mis-mapped macOS Safari to an iOS
        # fingerprint, which is itself a block signal.
        def _is_ios(t: str) -> bool:
            return t.endswith("_ios") or t.endswith("ios")

        pool = [t for t in safari_targets if _is_ios(t) == want_ios] or safari_targets
        return sorted(pool)[-1]

    # Unknown engine: fall back to the newest Chrome TLS profile (best general-purpose evasion).
    return f"chrome{chrome_targets[0]}" if chrome_targets else None


def build_profile(ua: str, available_impersonations: Optional[List[str]] = None) -> BrowserProfile:
    """Derive a coherent :class:`BrowserProfile` from a raw User-Agent string.

    The returned profile only advertises Chromium client hints when the UA is Chromium-based, so
    Firefox / Safari agents stay internally consistent.
    """
    available = available_impersonations or []
    mobile = ("Mobile" in ua) or ("Android" in ua) or ("iPhone" in ua)
    platform = _platform_from_ua(ua)
    impersonate = _impersonate_from_ua(ua, available)

    sec_ch_ua: Optional[str] = None
    # Only Chromium family (Chrome/Edge/Opera/Brave/Vivaldi) sends client hints.
    chrome_m = _CHROME_RE.search(ua)
    if chrome_m:
        major = chrome_m.group(1)
        if (edge_m := _EDGE_RE.search(ua)):
            sec_ch_ua = _chromium_sec_ch_ua(edge_m.group(1), brand="Microsoft Edge")
        elif (opera_m := _OPERA_RE.search(ua)):
            sec_ch_ua = _chromium_sec_ch_ua(major, brand="Opera")
        else:
            sec_ch_ua = _chromium_sec_ch_ua(major, brand="Google Chrome")

    return BrowserProfile(
        user_agent=ua,
        impersonate=impersonate,
        sec_ch_ua=sec_ch_ua,
        platform=platform,
        mobile=mobile,
    )


def _rotation_impersonations(available: List[str]) -> List[str]:
    """Return distinct, strong impersonation targets to cycle through when a request is blocked.

    A block is a TLS/JA3 rejection, so each retry needs a genuinely different handshake. We prefer
    a spread of recent Chrome-desktop versions (widest WAF acceptance) plus one Safari-desktop
    profile as a fallback engine, newest-first, de-duplicated and bounded.
    """
    chrome = sorted(
        (int(m.group(1)) for t in available if (m := re.fullmatch(r"chrome(\d+)", t))),
        reverse=True,
    )
    safari_desktop = sorted(
        [t for t in available if t.startswith("safari") and "ios" not in t],
        reverse=True,
    )
    picks: List[str] = []
    # Spread across a few recent Chrome majors (not adjacent) for JA3 diversity.
    for major in chrome[:4]:
        picks.append(f"chrome{major}")
    if safari_desktop:
        picks.append(safari_desktop[0])
    # De-duplicate preserving order, cap the pool.
    seen: set = set()
    ordered: List[str] = []
    for p in picks:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    return ordered[:5]


def _profile_strength(profile: BrowserProfile) -> int:
    """Rank profiles by empirical WAF-acceptance (higher is better).

    Chrome-desktop impersonation is accepted by the widest set of WAFs (Cloudflare/Akamai tune
    their "allow" baselines around it). Safari-desktop is next. Firefox and mobile profiles are
    challenged noticeably more often (observed: Medium/Cloudflare 403s a Firefox JA3 but 200s a
    Chrome JA3 for the same request), so they rank lowest and are chosen last on rotation.
    """
    imp = profile.impersonate or ""
    if imp.startswith("chrome") and not profile.mobile:
        return 100
    if imp.startswith("safari") and not profile.mobile and "ios" not in imp:
        return 80
    if imp.startswith("edge") and not profile.mobile:
        return 70
    if imp.startswith("chrome"):  # chrome mobile / android
        return 50
    if imp.startswith("safari"):  # safari ios
        return 40
    if imp.startswith("firefox"):
        return 20
    return 10  # no impersonation backing (httpx fallback)


@dataclass
class FingerprintManager:
    """Picks and *holds* one coherent browser profile for a client's lifetime.

    Stability is deliberate: a single HTTP client (and therefore a single logical "session"
    against a host) keeps the same User-Agent + client hints across its requests and retries.
    When a request is genuinely blocked (403 / challenge), the caller may call :meth:`rotate` to
    deliberately present as a *different* coherent browser on the next attempt — preferring the
    profiles WAFs accept most readily rather than rotating blindly into a weaker fingerprint.
    """

    user_agents: List[str]
    available_impersonations: List[str] = field(default_factory=list)
    _current: Optional[BrowserProfile] = field(default=None, init=False, repr=False)
    _ranked: List[BrowserProfile] = field(default_factory=list, init=False, repr=False)
    _rank_index: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.user_agents:
            raise ValueError("FingerprintManager requires at least one user agent")
        # Build the coherent profile for every configured UA, then order strongest-first so both
        # the initial pick and every rotation prefer high-acceptance Chrome-desktop fingerprints.
        profiles = [build_profile(ua, self.available_impersonations) for ua in self.user_agents]

        # CRITICAL for rotation to actually help: a block is a *TLS/JA3* rejection, so each
        # rotation must change the impersonation target, not just the UA string. Several configured
        # Chrome UAs collapse to the same impersonation (e.g. all map to chrome124), which would
        # make rotation a no-op at the TLS layer. We therefore synthesise additional Chrome-desktop
        # profiles that reuse the strongest UA but pin *distinct* newer Chrome JA3 targets, and a
        # Safari-desktop profile, so consecutive rotations present genuinely different handshakes.
        best_chrome_ua = next(
            (p.user_agent for p in sorted(profiles, key=_profile_strength, reverse=True)
             if (p.impersonate or "").startswith("chrome") and not p.mobile),
            None,
        )
        synthetic: List[BrowserProfile] = []
        if best_chrome_ua is not None:
            base = build_profile(best_chrome_ua, self.available_impersonations)
            for extra in _rotation_impersonations(self.available_impersonations):
                synthetic.append(replace(base, impersonate=extra))

        # De-duplicate by (ua, impersonate) while preserving the strongest ordering.
        seen = set()
        unique: List[BrowserProfile] = []
        for p in sorted(profiles + synthetic, key=_profile_strength, reverse=True):
            key = (p.user_agent, p.impersonate)
            if key not in seen:
                seen.add(key)
                unique.append(p)

        # Reorder so consecutive rotations change the TLS/JA3 fingerprint: lead with the first
        # profile for each DISTINCT impersonation target (JA3-diverse front), then append the rest.
        # Without this, several UAs that collapse to the same impersonation (e.g. chrome124) would
        # make the first N rotations JA3-identical and useless against a TLS-level block.
        by_impersonation_seen: set = set()
        jd_front: List[BrowserProfile] = []
        remainder: List[BrowserProfile] = []
        for p in unique:
            if p.impersonate not in by_impersonation_seen:
                by_impersonation_seen.add(p.impersonate)
                jd_front.append(p)
            else:
                remainder.append(p)
        self._ranked = jd_front + remainder
        self._rank_index = 0
        self._current = self._ranked[0]

    @property
    def current(self) -> BrowserProfile:
        assert self._current is not None
        return self._current

    def rotate(self) -> BrowserProfile:
        """Advance to the next-strongest coherent profile (used only after a real block)."""
        if len(self._ranked) <= 1:
            return self._current  # nothing stronger/other to switch to
        self._rank_index = (self._rank_index + 1) % len(self._ranked)
        self._current = self._ranked[self._rank_index]
        return self._current
