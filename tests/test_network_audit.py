import httpx
import pytest

from rahasya.storage.network_audit import (
    NetworkAuditStore,
    audit_csv,
    audit_html_report,
    audit_json,
    audit_scope,
    redact_url,
)
from rahasya.utils.http_client import StealthHTTPClient


async def _no_sleep(*_args, **_kwargs):
    return None


def test_redact_url_removes_credentials_and_secret_queries():
    redacted = redact_url(
        "https://alice:password@example.com/search?q=public&api_key=secret&token=hidden"
    )
    assert redacted == (
        "https://alice:REDACTED@example.com/search?q=public&api_key=REDACTED&token=REDACTED"
    )
    assert "password" not in redacted
    assert "secret" not in redacted
    assert "hidden" not in redacted


def test_audit_store_summary_and_exports(tmp_path):
    store = NetworkAuditStore(tmp_path)
    store.record("scan-1", {
        "event_type": "network_request",
        "outcome": "success",
        "source_module": "test",
        "host": "example.com",
        "url": "https://example.com/",
        "status_code": 200,
    })
    store.record("scan-1", {
        "event_type": "network_request",
        "outcome": "http_error",
        "source_module": "test",
        "host": "example.com",
        "url": "https://example.com/missing",
        "status_code": 404,
    })

    events = store.load("scan-1")
    summary = store.summary("scan-1")
    assert summary["network_attempts"] == 2
    assert summary["successful_requests"] == 1
    assert summary["failed_requests"] == 1
    assert summary["unique_hosts"] == 1
    assert "network_request" in audit_csv(events)
    assert '"status_code": 404' in audit_json(events)
    assert "Rahasya Network & Source Audit" in audit_html_report("scan-1", events)


@pytest.mark.asyncio
async def test_http_client_records_success_and_redacts_secret_query(tmp_path, monkeypatch):
    monkeypatch.setattr("rahasya.utils.http_client.random.uniform", lambda _a, _b: 0)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"ok": True})

    client = StealthHTTPClient(max_retries=1, use_impersonation=False)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with audit_scope("scan-http", "test-module", tmp_path):
            await client.get("https://example.com/search?api_key=super-secret&q=public")
    finally:
        await client.close()

    events = NetworkAuditStore(tmp_path).load("scan-http")
    assert len(events) == 1
    assert events[0]["outcome"] == "success"
    assert events[0]["status_code"] == 200
    assert events[0]["source_module"] == "test-module"
    assert events[0]["url"].endswith("api_key=REDACTED&q=public")
    assert "super-secret" not in events[0]["url"]


@pytest.mark.asyncio
async def test_http_client_classifies_404_as_expected_negative_without_retry(tmp_path, monkeypatch):
    # New semantics: a 404 is an *expected negative* (resource genuinely absent), not a transport
    # failure. The client returns the response instead of raising, records outcome
    # "expected_negative", and does NOT retry (404 is terminal, not a block).
    monkeypatch.setattr("rahasya.utils.http_client.random.uniform", lambda _a, _b: 0)

    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(404, request=request, text="missing")

    client = StealthHTTPClient(max_retries=3, use_impersonation=False)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with audit_scope("scan-negative", "test-module", tmp_path):
            response = await client.get("https://example.com/missing")
            assert response.status_code == 404
    finally:
        await client.close()

    assert calls == 1  # terminal, not retried
    events = NetworkAuditStore(tmp_path).load("scan-negative")
    assert len(events) == 1
    assert events[0]["outcome"] == "expected_negative"
    assert events[0]["status_code"] == 404
    assert events[0]["attempt"] == 1

    # And expected negatives must NOT inflate the failure metric.
    summary = NetworkAuditStore(tmp_path).summary("scan-negative")
    assert summary["failed_requests"] == 0
    assert summary["expected_negative_requests"] == 1


@pytest.mark.asyncio
async def test_http_client_retries_bot_block_then_succeeds(tmp_path, monkeypatch):
    # A 403 Cloudflare challenge is retried with a rotated fingerprint and succeeds on retry.
    monkeypatch.setattr("rahasya.utils.http_client.random.uniform", lambda _a, _b: 0)
    monkeypatch.setattr("rahasya.utils.http_client.asyncio.sleep", _no_sleep)

    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                403, request=request, text="<html>Just a moment... cf-chl-bypass</html>"
            )
        return httpx.Response(200, request=request, json={"ok": True})

    client = StealthHTTPClient(max_retries=3, use_impersonation=False, request_jitter=None)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with audit_scope("scan-block", "test-module", tmp_path):
            response = await client.get("https://example.com/protected")
            assert response.status_code == 200
    finally:
        await client.close()

    assert calls == 2  # blocked once, retried, succeeded
    events = NetworkAuditStore(tmp_path).load("scan-block")
    assert events[0]["outcome"] == "http_error"      # first attempt: an un-beaten challenge
    assert events[-1]["outcome"] == "success"          # retry won
