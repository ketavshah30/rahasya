"""Opt-in LIVE smoke tests for the fingerprinted HTTP client.

These make REAL outbound requests and are therefore skipped by default. Enable with:

    RAHASYA_LIVE_SMOKE=1 pytest tests/test_http_live_smoke.py -q

They assert that the client's outcome for well-known public endpoints is ``success`` or
``expected_negative`` — i.e. NOT a transport failure or an un-beaten bot block — which is the
end-to-end proof that the fingerprinting architecture reduces 403s/401s in practice.
"""

import os

import pytest

from rahasya.utils.http_client import (
    OUTCOME_EXPECTED_NEGATIVE,
    OUTCOME_SUCCESS,
    StealthHTTPClient,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RAHASYA_LIVE_SMOKE") != "1",
    reason="live network smoke test; set RAHASYA_LIVE_SMOKE=1 to run",
)

ACCEPTABLE = {OUTCOME_SUCCESS, OUTCOME_EXPECTED_NEGATIVE}

LIVE_TARGETS = [
    "https://api.github.com/users/torvalds",
    "https://www.gravatar.com/205e460b479e2e5b48aec07710c08d50.json",
    "https://archive.org/wayback/available?url=example.com",
    "https://www.instagram.com/instagram/",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", LIVE_TARGETS)
async def test_live_endpoint_is_not_blocked(url):
    client = StealthHTTPClient(timeout=25, request_jitter=None, max_retries=2)
    try:
        resp = await client.get(url)
        outcome = StealthHTTPClient._classify(resp.status_code, getattr(resp, "text", "") or "")
        assert outcome in ACCEPTABLE, f"{url} -> {resp.status_code} ({outcome})"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_live_tls_fingerprint_is_browser_grade():
    """The client should present a browser TLS fingerprint + HTTP/2 (not Python/HTTP1.1)."""
    client = StealthHTTPClient(timeout=25, request_jitter=None)
    try:
        resp = await client.get("https://tls.peet.ws/api/all")
        data = resp.json()
    finally:
        await client.close()
    # HTTP/2 negotiated and a browser UA echoed back.
    assert data.get("http_version") in ("h2", "HTTP/2")
    assert "Mozilla/5.0" in (data.get("user_agent") or "")
