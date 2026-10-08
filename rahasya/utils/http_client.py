"""Resilient, browser-fingerprinted async HTTP client for Rahasya.

Design goals (see plan: Network Resilience & Anti-Bot Hardening):

* **Realistic network fingerprint without a browser.** When ``curl_cffi`` is importable the client
  routes requests through a ``curl_cffi.requests.AsyncSession`` with Chrome *impersonation*, which
  presents a genuine browser TLS/JA3 handshake and HTTP/2 settings. This defeats the bulk of
  Cloudflare/Akamai 403s that a plain ``httpx`` client triggers. When ``curl_cffi`` is absent the
  client transparently falls back to an ``httpx.AsyncClient`` with HTTP/2 enabled.
* **Coherent, stable headers.** One :class:`~rahasya.utils.fingerprint.BrowserProfile` is chosen
  per client and reused for its lifetime (and across retries) — the UA no longer changes mid
  conversation. Client hints are only emitted for Chromium UAs.
* **Correct 4xx semantics.** 403/429/503 + challenge bodies are *retryable* (with a rotated
  fingerprint); expected negatives (404/410, and 401/403 "private/forbidden" with no challenge)
  are classified as :data:`OUTCOME_EXPECTED_NEGATIVE` and returned to the caller instead of being
  raised as failures. Authentication failures on keyed APIs are classified
  :data:`OUTCOME_AUTH_ERROR`.
* **``head()`` support** so :mod:`rahasya.modules.social.live_probe` works.

No Playwright / Camoufox / real browser engine is used.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Dict, Optional, Tuple

from loguru import logger

import httpx
from httpx_socks import AsyncProxyTransport

from rahasya.storage.network_audit import record_audit_event
from rahasya.utils.fingerprint import FingerprintManager

try:  # Optional, strongly preferred: browser TLS/JA3 + HTTP/2 impersonation.
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession
    from curl_cffi.requests import BrowserType as _CurlBrowserType

    _CURL_AVAILABLE = True
    _CURL_IMPERSONATIONS = [b for b in dir(_CurlBrowserType) if not b.startswith("_") and any(c.isdigit() for c in b)]
except Exception:  # pragma: no cover - exercised only when curl_cffi missing
    _CurlAsyncSession = None  # type: ignore[assignment]
    _CURL_AVAILABLE = False
    _CURL_IMPERSONATIONS = []


# --- Audit outcome taxonomy -------------------------------------------------------------------
OUTCOME_SUCCESS = "success"
# A terminal, *expected* negative response (404/410, or a private/forbidden profile with no bot
# challenge). This is data, not a failure, and is excluded from failed_requests metrics.
OUTCOME_EXPECTED_NEGATIVE = "expected_negative"
# Authentication / authorization failure on a keyed API (bad or missing credential), distinct from
# bot-protection noise so operators can tell "fix your key" apart from "we got blocked".
OUTCOME_AUTH_ERROR = "auth_error"
OUTCOME_RATE_LIMITED = "rate_limited"
OUTCOME_HTTP_ERROR = "http_error"
OUTCOME_FAILED = "failed"
# A host that persistently refuses/times out the TCP/TLS connection after all retries. This is an
# IP/egress-level block or a dead host — NOT a client defect and NOT fixable by fingerprinting, so
# it is classified distinctly and excluded from the transport-failure reliability metric. The
# per-host circuit breaker in high-fanout modules uses this to stop hammering a dead host.
OUTCOME_UNREACHABLE = "unreachable"


# Legacy static list kept ONLY as a last-resort fallback if config cannot be imported. The real,
# current UA pool lives in rahasya.config.HTTPSettings.user_agents (single source of truth).
_FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36",
]

# Body markers that indicate an interactive bot challenge (retry with a fresh fingerprint; if it
# persists we classify it so Wayback / cf-challenge gating can take over).
CHALLENGE_MARKERS = (
    "cf-chl-",
    "just a moment",
    "attention required",
    "checking your browser",
    "challenge-platform",
    "/cdn-cgi/challenge-platform",
    "px-captcha",
    "please verify you are a human",
    "enable javascript and cookies to continue",
)
# Body markers that indicate a legitimately private/suspended resource (expected negative, not a
# block we should fight).
PRIVATE_MARKERS = (
    "this account is private",
    "account suspended",
    "log in to see",
    "sign in to see",
    "page not found",
)

# Statuses we should retry (transient / block / throttle) rather than treat as terminal.
RETRYABLE_STATUS = {403, 408, 425, 429, 500, 502, 503, 504}
# Statuses that are expected negatives (resource genuinely absent/gone).
EXPECTED_NEGATIVE_STATUS = {404, 410}
# Statuses that typically indicate credential problems on keyed APIs.
AUTH_STATUS = {401, 402, 407}


def _default_user_agents() -> list[str]:
    try:
        from rahasya.config import settings

        uas = list(getattr(settings.http, "user_agents", []) or [])
        if uas:
            return uas
    except Exception:  # pragma: no cover
        pass
    return list(_FALLBACK_USER_AGENTS)


class _ResponseView:
    """Minimal httpx-compatible view over a curl_cffi response.

    Modules only ever touch ``.status_code``, ``.text``, ``.content``, ``.json()``, ``.headers``
    and ``.url``. We expose exactly that surface so the curl_cffi and httpx backends are
    interchangeable from a module's point of view.
    """

    __slots__ = ("_resp",)

    def __init__(self, resp: Any):
        self._resp = resp

    @property
    def status_code(self) -> int:
        return int(self._resp.status_code)

    @property
    def text(self) -> str:
        return self._resp.text or ""

    @property
    def content(self) -> bytes:
        return self._resp.content or b""

    @property
    def headers(self) -> Any:
        return self._resp.headers

    @property
    def url(self) -> Any:
        return getattr(self._resp, "url", None)

    def json(self) -> Any:
        return self._resp.json()


class StealthHTTPClient:
    """Resilient HTTP client with browser fingerprinting and correct 4xx handling."""

    def __init__(
        self,
        proxy: Optional[str] = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        ssl_verify: bool = True,
        request_jitter: Optional[Tuple[float, float]] = (0.5, 2.0),
        transport: Optional[httpx.AsyncBaseTransport] = None,
        user_agents: Optional[list[str]] = None,
        use_impersonation: bool = True,
        follow_redirects: bool = True,
        connect_timeout: float = 8.0,
        connect_error_retry_cap: int = 2,
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.max_retries = max_retries
        self.ssl_verify = ssl_verify
        self.request_jitter = request_jitter
        self.follow_redirects = follow_redirects
        # Fail fast on dead/IP-blocked hosts instead of hanging for the full read timeout on every
        # retry (a single tarpitting host must not stall a high-fanout scan).
        self.connect_timeout = min(connect_timeout, timeout)
        # Connect/TLS failures almost never recover within a scan, so cap their retries low (a
        # dead/blocked host should not consume all max_retries). HTTP-level blocks/5xx still use the
        # full max_retries with fingerprint rotation.
        self.connect_error_retry_cap = max(1, min(connect_error_retry_cap, max_retries))

        self._fingerprint = FingerprintManager(
            user_agents=user_agents or _default_user_agents(),
            available_impersonations=_CURL_IMPERSONATIONS,
        )

        # Decide backend. A caller-supplied transport (e.g. Tor, or a test MockTransport) forces
        # the httpx backend so existing behaviour and test seams are preserved.
        self._forced_httpx = transport is not None
        self._use_curl = bool(use_impersonation and _CURL_AVAILABLE and transport is None)

        # httpx backend is ALWAYS constructed: it is the fallback, and tests inject a
        # MockTransport by replacing ``self._client``.
        self._client = httpx.AsyncClient(
            proxy=proxy if transport is None else None,
            timeout=httpx.Timeout(timeout, connect=self.connect_timeout),
            verify=ssl_verify,
            transport=transport,
            follow_redirects=follow_redirects,
            http2=_http2_available(),
        )

        self._curl_session: Optional[Any] = None
        if self._use_curl:
            try:
                self._curl_session = _CurlAsyncSession(
                    timeout=timeout,
                    verify=ssl_verify,
                    proxy=proxy,
                    allow_redirects=follow_redirects,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(f"curl_cffi session init failed, falling back to httpx: {exc}")
                self._use_curl = False
                self._curl_session = None

    # -- header construction -------------------------------------------------------------------
    def _get_headers(self, custom_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = self._fingerprint.current.base_headers()
        if custom_headers:
            headers.update(custom_headers)
        return headers

    # -- response classification ---------------------------------------------------------------
    @staticmethod
    def _looks_like_challenge(status: int, body: str) -> bool:
        if status in (403, 429, 503):
            low = (body or "").lower()
            return any(marker in low for marker in CHALLENGE_MARKERS)
        return False

    @classmethod
    def _classify(cls, status: int, body: str) -> str:
        if 200 <= status < 400:
            return OUTCOME_SUCCESS
        if status in EXPECTED_NEGATIVE_STATUS:
            return OUTCOME_EXPECTED_NEGATIVE
        if status == 429:
            return OUTCOME_RATE_LIMITED
        if status in AUTH_STATUS:
            return OUTCOME_AUTH_ERROR
        if status == 403:
            low = (body or "").lower()
            if any(marker in low for marker in CHALLENGE_MARKERS):
                return OUTCOME_HTTP_ERROR  # a block we failed to beat
            if any(marker in low for marker in PRIVATE_MARKERS):
                return OUTCOME_EXPECTED_NEGATIVE
            # Bare 403 with no challenge/private marker: treat as expected negative (forbidden
            # resource) rather than inflating the failure count.
            return OUTCOME_EXPECTED_NEGATIVE
        if status >= 400:
            return OUTCOME_HTTP_ERROR
        return OUTCOME_SUCCESS

    # -- low-level dispatch to the active backend ----------------------------------------------
    async def _dispatch(self, method: str, url: str, headers: Dict[str, str], **kwargs) -> Any:
        if self._use_curl and self._curl_session is not None:
            impersonate = self._fingerprint.current.impersonate
            # Map httpx-style kwargs to curl_cffi.
            timeout = kwargs.pop("timeout", self.timeout)
            params = kwargs.pop("params", None)
            data = kwargs.pop("data", None)
            json_body = kwargs.pop("json", None)
            content = kwargs.pop("content", None)
            # curl_cffi accepts a (connect, read) tuple — bound the connect phase so dead/blocked
            # hosts fail fast instead of consuming the whole read timeout on every retry.
            curl_timeout = (self.connect_timeout, float(timeout))
            resp = await self._curl_session.request(
                method,
                url,
                headers=headers,
                params=params,
                data=data if content is None else content,
                json=json_body,
                timeout=curl_timeout,
                impersonate=impersonate,
                allow_redirects=self.follow_redirects,
            )
            return _ResponseView(resp)
        # httpx backend
        return await self._client.request(method, url, headers=headers, **kwargs)

    # -- core retry state machine --------------------------------------------------------------
    async def _request(
        self,
        method: str,
        url: str,
        *,
        raise_for_status: bool = False,
        expected_statuses: Optional[set[int]] = None,
        **kwargs,
    ) -> Any:
        """Perform a request with fingerprinting, retries, and outcome classification.

        By default this NO LONGER raises on 4xx. It returns the response and records a precise
        outcome. Callers that still want the old "raise on >=400" behaviour pass
        ``raise_for_status=True``. ``expected_statuses`` lets a caller mark extra codes as benign.
        """
        delay = 1.0
        last_exception: Optional[Exception] = None
        last_response: Optional[Any] = None

        if self.request_jitter is not None:
            await asyncio.sleep(random.uniform(*self.request_jitter))

        for attempt in range(self.max_retries):
            request_started = time.monotonic()
            try:
                headers = self._get_headers(kwargs.pop("headers", None))
                response = await self._dispatch(method, url, headers, **kwargs)
                duration_ms = round((time.monotonic() - request_started) * 1000, 2)
                status = response.status_code

                # Only read the body for ambiguous statuses (avoids pulling large payloads just to
                # classify a clean 200).
                body = ""
                if status in (403, 429, 503) or status in AUTH_STATUS:
                    try:
                        body = response.text
                    except Exception:  # noqa: BLE001
                        body = ""

                outcome = self._classify(status, body)
                if expected_statuses and status in expected_statuses:
                    outcome = OUTCOME_EXPECTED_NEGATIVE

                record_audit_event(
                    "network_request",
                    outcome=outcome,
                    url=_safe_url(response, url),
                    method=method.upper(),
                    status_code=status,
                    duration_ms=duration_ms,
                    attempt=attempt + 1,
                    max_attempts=self.max_retries,
                    via_proxy=bool(self.proxy),
                    impersonate=self._fingerprint.current.impersonate,
                )

                # Decide whether to retry. Retry genuine blocks / throttles / transient 5xx.
                retryable = status in RETRYABLE_STATUS and not (
                    status == 403 and outcome == OUTCOME_EXPECTED_NEGATIVE
                )
                if retryable and attempt < self.max_retries - 1:
                    last_response = response
                    # Present as a different coherent browser on the next attempt.
                    self._fingerprint.rotate()
                    sleep_for = _retry_after_seconds(response) or (delay * random.uniform(0.75, 1.25))
                    await asyncio.sleep(sleep_for)
                    delay *= 2
                    continue

                if raise_for_status and status >= 400:
                    _raise_status(response, status, url)
                return response

            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network/transport errors
                duration_ms = round((time.monotonic() - request_started) * 1000, 2)
                is_connect = _is_connect_error(exc)
                # Connect errors fail fast (dead/blocked host); other transient errors use the full
                # retry budget.
                attempt_cap = self.connect_error_retry_cap if is_connect else self.max_retries
                is_final = attempt >= attempt_cap - 1
                # A connect/TLS failure that persists through its (capped) retries is an
                # IP/egress-level block or a dead host — classify it as `unreachable` (not a
                # transport failure we could have fixed). Transient errors mid-sequence stay
                # `failed` so the audit still shows the retry churn.
                outcome = OUTCOME_UNREACHABLE if (is_connect and is_final) else OUTCOME_FAILED
                record_audit_event(
                    "network_request",
                    outcome=outcome,
                    url=url,
                    method=method.upper(),
                    duration_ms=duration_ms,
                    attempt=attempt + 1,
                    max_attempts=attempt_cap,
                    via_proxy=bool(self.proxy),
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                logger.warning(f"Attempt {attempt+1}/{attempt_cap} failed for {url}: {exc}")
                last_exception = exc
                # Transient transport errors are retried with backoff (and a rotated fingerprint),
                # instead of being re-raised on the first blip — but only up to attempt_cap.
                if not is_final:
                    self._fingerprint.rotate()
                    await asyncio.sleep(delay * random.uniform(0.75, 1.25))
                    delay *= 2
                    continue
                raise

        if raise_for_status and last_response is not None:
            _raise_status(last_response, last_response.status_code, url)
        if last_response is not None:
            return last_response
        raise last_exception or RuntimeError("Unknown request failure")

    # -- public API ----------------------------------------------------------------------------
    async def get(self, url: str, **kwargs) -> Any:
        return await self._request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> Any:
        return await self._request("POST", url, **kwargs)

    async def head(self, url: str, **kwargs) -> Any:
        return await self._request("HEAD", url, **kwargs)

    async def download(self, url: str, **kwargs) -> bytes:
        response = await self.get(url, **kwargs)
        return response.content

    async def close(self):
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001
            pass
        if self._curl_session is not None:
            try:
                await self._curl_session.close()
            except Exception:  # noqa: BLE001
                pass


def _http2_available() -> bool:
    try:
        import h2  # noqa: F401

        return True
    except Exception:  # pragma: no cover
        return False


def _retry_after_seconds(response: Any) -> Optional[float]:
    try:
        headers = response.headers
        value = None
        # httpx Headers and curl_cffi Headers both support .get.
        if hasattr(headers, "get"):
            value = headers.get("Retry-After") or headers.get("retry-after")
        if value is None:
            return None
        return float(int(str(value).strip()))
    except Exception:  # noqa: BLE001
        return None


def _is_connect_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
        return True
    return "connect" in type(exc).__name__.lower() or "timeout" in type(exc).__name__.lower()


def _safe_url(response: Any, fallback: str) -> str:
    try:
        url = getattr(response, "url", None)
        return str(url) if url else fallback
    except Exception:  # noqa: BLE001
        return fallback


def _raise_status(response: Any, status: int, url: str) -> None:
    """Raise an httpx.HTTPStatusError regardless of backend (preserves caller contract)."""
    if isinstance(response, httpx.Response):
        response.raise_for_status()
        return
    request = httpx.Request("GET", url)
    httpx_response = httpx.Response(status, request=request)
    raise httpx.HTTPStatusError(
        f"HTTP {status} for {url}", request=request, response=httpx_response
    )


class TorHTTPClient(StealthHTTPClient):
    """HTTP client that routes traffic through a Tor SOCKS5 proxy.

    Tor uses the httpx backend with a SOCKS transport (curl_cffi impersonation is bypassed because
    exit-node fingerprinting is a separate concern and the SOCKS transport is forced).
    """

    def __init__(
        self,
        tor_proxy: str = "socks5h://127.0.0.1:9050",
        timeout: float = 60.0,
        ssl_verify: bool = True,
        request_jitter: Optional[Tuple[float, float]] = (0.5, 2.0),
    ):
        # python-socks performs remote hostname resolution for SOCKS5 but accepts only the socks5
        # scheme spelling, not the curl-style socks5h.
        transport_url = tor_proxy.replace("socks5h://", "socks5://", 1)
        transport = AsyncProxyTransport.from_url(transport_url)
        super().__init__(
            proxy=tor_proxy,
            timeout=timeout,
            ssl_verify=ssl_verify,
            request_jitter=request_jitter,
            transport=transport,
            use_impersonation=False,
        )
        logger.info(f"Initialized TorHTTPClient via {tor_proxy}")


class PlaywrightClient:
    """Deprecated browser client retained only for backwards import compatibility.

    The project is explicitly not a browser-automation tool; anti-bot evasion is handled at the
    network/TLS layer by :class:`StealthHTTPClient` (curl_cffi impersonation). This class remains
    so that any lingering imports do not break, but it is not used by the pipeline.
    """

    def __init__(self, headless: bool = True):
        self.headless = headless

    async def get(self, url: str) -> str:  # pragma: no cover - intentionally unsupported
        raise NotImplementedError(
            "PlaywrightClient is deprecated. Rahasya performs anti-bot evasion at the TLS/HTTP "
            "layer via StealthHTTPClient (curl_cffi impersonation); no browser engine is used."
        )

    async def close(self):  # pragma: no cover
        return None
