"""Tests for the browser-fingerprinting HTTP layer (no real network, no browser)."""

import httpx
import pytest

from rahasya.storage.network_audit import NetworkAuditStore, audit_scope
from rahasya.utils.fingerprint import FingerprintManager, build_profile
from rahasya.utils.http_client import (
    OUTCOME_AUTH_ERROR,
    OUTCOME_EXPECTED_NEGATIVE,
    OUTCOME_RATE_LIMITED,
    OUTCOME_SUCCESS,
    OUTCOME_UNREACHABLE,
    StealthHTTPClient,
)

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
FIREFOX_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0"
EDGE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0"
)
ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Mobile Safari/537.36"
)


async def _no_sleep(*_a, **_k):
    return None


# --- fingerprint coherence --------------------------------------------------------------------

def test_chrome_profile_emits_coherent_client_hints():
    profile = build_profile(CHROME_UA, ["chrome124", "chrome120"])
    headers = profile.base_headers()
    assert headers["User-Agent"] == CHROME_UA
    assert "Chromium" in headers["Sec-CH-UA"]
    assert "Google Chrome" in headers["Sec-CH-UA"]
    assert headers["Sec-CH-UA-Mobile"] == "?0"
    assert headers["Sec-CH-UA-Platform"] == '"Windows"'
    assert profile.impersonate == "chrome124"


def test_firefox_profile_does_not_emit_chromium_hints():
    # Sending Sec-CH-UA from a Firefox UA is itself a bot tell. It must be absent.
    profile = build_profile(FIREFOX_UA, ["chrome124"])
    headers = profile.base_headers()
    assert "Sec-CH-UA" not in headers
    assert profile.sec_ch_ua is None


def test_edge_profile_brand_and_mobile_flag():
    profile = build_profile(EDGE_UA, ["chrome124"])
    assert "Microsoft Edge" in profile.base_headers()["Sec-CH-UA"]
    android = build_profile(ANDROID_UA, ["chrome124"])
    hints = android.base_headers()
    assert hints["Sec-CH-UA-Mobile"] == "?1"
    assert android.platform == '"Android"'


def test_impersonation_picks_closest_not_newer():
    # UA is Chrome 124, available targets include 120 and 131; we must not claim a NEWER TLS
    # profile than the UA advertises (keep UA/TLS coherent).
    profile = build_profile(CHROME_UA, ["chrome131", "chrome120", "chrome116"])
    assert profile.impersonate == "chrome120"


def test_fingerprint_manager_is_stable_until_rotated():
    mgr = FingerprintManager(user_agents=[CHROME_UA, FIREFOX_UA], available_impersonations=["chrome124"])
    first = mgr.current.user_agent
    # current must be stable across reads
    assert mgr.current.user_agent == first
    rotated = mgr.rotate().user_agent
    assert rotated != first  # with two UAs, rotate switches


def test_rotation_changes_ja3_impersonation_even_with_one_ua():
    # The whole point of rotation after a block is to change the TLS/JA3 fingerprint. Even when a
    # single UA is configured, rotation must cycle through DISTINCT impersonation targets rather
    # than repeating the same one (which would make retry-after-block useless).
    available = ["chrome124", "chrome131", "chrome136", "safari180"]
    mgr = FingerprintManager(user_agents=[CHROME_UA], available_impersonations=available)
    seq = [mgr.current.impersonate] + [mgr.rotate().impersonate for _ in range(3)]
    assert seq[0] == "chrome124"  # strongest/closest to the UA leads
    assert len(set(seq)) >= 3  # consecutive rotations present different JA3 handshakes


def test_safari_desktop_ua_does_not_map_to_ios_target():
    safari_desktop_ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.5 Safari/605.1.15"
    )
    profile = build_profile(safari_desktop_ua, ["safari180", "safari180_ios", "chrome124"])
    assert profile.impersonate is not None
    assert not profile.impersonate.endswith("ios")  # desktop UA must keep a desktop TLS profile


# --- client classification / behaviour --------------------------------------------------------

def _mock_client(handler, **kw):
    client = StealthHTTPClient(use_impersonation=False, request_jitter=None, **kw)
    return client, handler


@pytest.mark.asyncio
async def test_head_is_supported(tmp_path, monkeypatch):
    monkeypatch.setattr("rahasya.utils.http_client.asyncio.sleep", _no_sleep)
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        return httpx.Response(200, request=request)

    client = StealthHTTPClient(use_impersonation=False, request_jitter=None)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        resp = await client.head("https://example.com/profile")
        assert resp.status_code == 200
    finally:
        await client.close()
    assert seen["method"] == "HEAD"


@pytest.mark.asyncio
async def test_401_is_auth_error_not_bot_noise(tmp_path, monkeypatch):
    monkeypatch.setattr("rahasya.utils.http_client.asyncio.sleep", _no_sleep)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, request=request, text="unauthorized")

    client = StealthHTTPClient(use_impersonation=False, request_jitter=None, max_retries=2)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with audit_scope("scan-auth", "test", tmp_path):
            resp = await client.get("https://api.example.com/v3/data")
            assert resp.status_code == 401
    finally:
        await client.close()

    summary = NetworkAuditStore(tmp_path).summary("scan-auth")
    assert summary["auth_error_requests"] == 1
    assert summary["failed_requests"] == 0  # auth problems are not transport failures


@pytest.mark.asyncio
async def test_429_honours_retry_after(tmp_path, monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("rahasya.utils.http_client.asyncio.sleep", fake_sleep)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, request=request, headers={"Retry-After": "2"}, text="slow down")
        return httpx.Response(200, request=request, json={"ok": True})

    client = StealthHTTPClient(use_impersonation=False, request_jitter=None, max_retries=3)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with audit_scope("scan-429", "test", tmp_path):
            resp = await client.get("https://api.example.com/x")
            assert resp.status_code == 200
    finally:
        await client.close()

    assert calls == 2
    assert 2.0 in sleeps  # honoured Retry-After: 2
    events = NetworkAuditStore(tmp_path).load("scan-429")
    assert events[0]["outcome"] == OUTCOME_RATE_LIMITED
    assert events[-1]["outcome"] == OUTCOME_SUCCESS


@pytest.mark.asyncio
async def test_bare_403_without_challenge_is_expected_negative(tmp_path, monkeypatch):
    monkeypatch.setattr("rahasya.utils.http_client.asyncio.sleep", _no_sleep)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, request=request, text="Forbidden")

    client = StealthHTTPClient(use_impersonation=False, request_jitter=None, max_retries=3)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with audit_scope("scan-403", "test", tmp_path):
            resp = await client.get("https://example.com/private")
            assert resp.status_code == 403
    finally:
        await client.close()

    # A bare 403 with no challenge marker is a forbidden resource (expected), NOT retried as a
    # block, and NOT counted as a failure.
    assert calls == 1
    summary = NetworkAuditStore(tmp_path).summary("scan-403")
    assert summary["expected_negative_requests"] == 1
    assert summary["failed_requests"] == 0


@pytest.mark.asyncio
async def test_persistent_connect_error_is_classified_unreachable(tmp_path, monkeypatch):
    # A host that refuses every connection (IP-level block / dead host) must be classified
    # `unreachable` — fast-failed at the connect cap and EXCLUDED from the failed_requests
    # reliability metric, since it is not a defect fingerprinting could fix.
    monkeypatch.setattr("rahasya.utils.http_client.asyncio.sleep", _no_sleep)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectTimeout("blocked", request=request)

    client = StealthHTTPClient(
        use_impersonation=False, request_jitter=None, max_retries=5, connect_error_retry_cap=2
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with audit_scope("scan-unreach", "test", tmp_path):
            with pytest.raises(httpx.ConnectTimeout):
                await client.get("https://blocked.example/")
    finally:
        await client.close()

    assert calls == 2  # fast-failed at the connect cap, not all 5 retries
    summary = NetworkAuditStore(tmp_path).summary("scan-unreach")
    assert summary["unreachable_requests"] == 1
    assert summary["failed_requests"] == 1  # the one non-final retry churn
    # The final, defining outcome is unreachable (not failed).
    events = NetworkAuditStore(tmp_path).load("scan-unreach")
    assert events[-1]["outcome"] == OUTCOME_UNREACHABLE


def test_classify_matrix():
    c = StealthHTTPClient._classify
    assert c(200, "") == OUTCOME_SUCCESS
    assert c(301, "") == OUTCOME_SUCCESS
    assert c(404, "") == OUTCOME_EXPECTED_NEGATIVE
    assert c(410, "") == OUTCOME_EXPECTED_NEGATIVE
    assert c(401, "") == OUTCOME_AUTH_ERROR
    assert c(429, "slow") == OUTCOME_RATE_LIMITED
    assert c(403, "just a moment cf-chl-") == "http_error"  # un-beaten challenge
    assert c(403, "Forbidden") == OUTCOME_EXPECTED_NEGATIVE
