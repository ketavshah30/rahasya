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

    client = StealthHTTPClient(max_retries=1)
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
async def test_http_client_records_http_error_without_retry(tmp_path, monkeypatch):
    monkeypatch.setattr("rahasya.utils.http_client.random.uniform", lambda _a, _b: 0)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, request=request, text="missing")

    client = StealthHTTPClient(max_retries=3)
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with audit_scope("scan-failed", "test-module", tmp_path):
            with pytest.raises(httpx.HTTPStatusError):
                await client.get("https://example.com/missing")
    finally:
        await client.close()

    events = NetworkAuditStore(tmp_path).load("scan-failed")
    assert len(events) == 1
    assert events[0]["outcome"] == "http_error"
    assert events[0]["status_code"] == 404
    assert events[0]["attempt"] == 1
