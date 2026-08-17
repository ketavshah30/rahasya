import json

import httpx
import pytest

from rahasya.config import IntelXSettings, Settings
from rahasya.core.models import Entity, EntityType
from rahasya.modules.breach.hibp_passwords_module import HIBPPasswordsModule
from rahasya.modules.breach.intelx_module import IntelXModule
from rahasya.modules.darkweb.ahmia_module import AhmiaModule
from rahasya.modules.darkweb.tor_manager import TorManager
from rahasya.modules.multimedia.archive_module import ArchiveModule
from rahasya.modules.multimedia.exif_module import ExifModule
from rahasya.modules.multimedia.image_hash_module import ImageHashModule
from rahasya.modules.social.maigret_module import MaigretModule
from rahasya.modules.social.sherlock_module import SherlockModule
from rahasya.modules.social.whatsmyname_module import WhatsMyNameModule
from rahasya.utils.http_client import StealthHTTPClient


def entity(entity_type=EntityType.USERNAME, value="known-user"):
    return Entity(
        entity_type=entity_type,
        value=value,
        normalized_value=value.casefold(),
        source_module="test",
    )


def test_cli_commands_match_current_provider_interfaces(tmp_path):
    maigret = MaigretModule._command("known-user", str(tmp_path))
    assert maigret[maigret.index("--json") + 1] == "ndjson"
    assert maigret[maigret.index("--folderoutput") + 1] == str(tmp_path)
    assert maigret[maigret.index("--retries") + 1] == "1"

    sherlock = SherlockModule._command("known-user", str(tmp_path))
    assert "--json" not in sherlock
    assert "--csv" in sherlock
    assert sherlock[sherlock.index("--folderoutput") + 1] == str(tmp_path)
    assert sherlock[sherlock.index("--timeout") + 1] == "10"


def test_maigret_ndjson_and_sherlock_csv_parsers(tmp_path):
    maigret_report = tmp_path / "report_known-user_ndjson.json"
    maigret_report.write_text(
        json.dumps({
            "sitename": "GitHub",
            "status": {"status": "Claimed"},
            "url_user": "https://github.com/known-user",
            "site": {"name": "GitHub", "tags": ["coding"]},
        }) + "\n",
        encoding="utf-8",
    )
    records = MaigretModule._read_ndjson(str(tmp_path))
    assert records[0]["sitename"] == "GitHub"

    sherlock_report = tmp_path / "known-user.csv"
    sherlock_report.write_text(
        "username,name,url_main,url_user,exists,http_status,response_time_s\n"
        "known-user,GitHub,https://github.com,https://github.com/known-user,Claimed,200,0.12\n",
        encoding="utf-8",
    )
    rows = SherlockModule._read_report(str(tmp_path), "known-user")
    assert rows[0]["name"] == "GitHub"
    assert SherlockModule._is_claimed(rows[0]["exists"])


def test_module_timeouts_rates_and_http_profiles():
    settings = Settings()
    assert settings.scan.module_timeout_seconds == 30.0
    assert settings.scan.module_timeout_overrides == {
        "Maigret": 600.0,
        "Sherlock": 600.0,
        "WhatsMyName": 600.0,
    }
    assert MaigretModule.rate_limit == 0.0
    assert SherlockModule.rate_limit == 0.0
    assert ExifModule.rate_limit == 0.0
    assert ImageHashModule.rate_limit == 0.0
    assert WhatsMyNameModule.request_jitter is None
    assert ArchiveModule.http_max_retries == 5


def test_intelx_tier_mapping_and_compatibility_env(monkeypatch):
    assert IntelXSettings(tier="public").base_url == "https://public.intelx.io"
    assert IntelXSettings(tier="free").base_url == "https://free.intelx.io"
    assert IntelXSettings(tier="paid").base_url == "https://2.intelx.io"
    monkeypatch.setenv("API_KEYS__INTELX_TIER", "public")
    assert Settings(_env_file=None).intelx.base_url == "https://public.intelx.io"


def test_intelx_usage_is_persisted_and_resets_for_old_date(tmp_path):
    settings = Settings()
    settings.storage.state_dir = tmp_path
    module = IntelXModule(settings)
    assert module._load_daily_usage() == 0
    assert module._increment_daily_usage() == 1
    reloaded = IntelXModule(settings)
    assert reloaded._load_daily_usage() == 1
    module.usage_path.write_text('{"date":"2000-01-01","count":9}', encoding="utf-8")
    assert reloaded._load_daily_usage() == 0


def test_tor_proxy_uses_remote_dns_scheme():
    manager = TorManager(socks_host="tor", socks_port=9050)
    assert manager.proxy_url == "socks5h://tor:9050"
    assert type(manager._proxy_transport()).__name__ == "AsyncProxyTransport"


@pytest.mark.asyncio
async def test_hibp_passwords_known_hash():
    suffix = "1E4C9B93F3F0682250B6CF8331B7EE68FD8"

    class FakeClient:
        async def get(self, url, **kwargs):
            request = httpx.Request("GET", url)
            return httpx.Response(200, request=request, text=f"{suffix}:3303003\nDEADBEEF:2")

    module = HIBPPasswordsModule()
    module.http_client = FakeClient()
    results = await module.execute(entity(EntityType.PASSWORD_HASH, "password"), "scan-password")
    assert len(results) == 1
    assert results[0].severity == "High"
    assert results[0].affected_count == 3303003
    assert results[0].source_name == "HIBP Pwned Passwords"
    assert "password" not in json.dumps(results[0].metadata).casefold()


@pytest.mark.asyncio
async def test_ahmia_parses_html_results():
    html = """
    <ul><li class="result"><h4><a href="/search/redirect?redirect_url=http%3A%2F%2Fexample.onion">
    Example Onion</a></h4><p>Example description</p></li></ul>
    """

    class FakeClient:
        async def get(self, url, **kwargs):
            return httpx.Response(200, request=httpx.Request("GET", url), text=html)

    module = AhmiaModule()
    module.http_client = FakeClient()
    results = await module.execute(entity(), "scan-ahmia")
    assert len(results) == 1
    assert results[0].source_url == "http://example.onion"
    assert results[0].is_onion is True


@pytest.mark.asyncio
async def test_ahmia_disables_itself_after_repeated_degraded_responses():
    class EmptyClient:
        def __init__(self):
            self.calls = 0

        async def get(self, url, **kwargs):
            self.calls += 1
            return httpx.Response(200, request=httpx.Request("GET", url), text="<html></html>")

    client = EmptyClient()
    module = AhmiaModule()
    module.http_client = client
    for _ in range(4):
        assert await module.execute(entity(), "scan-degraded") == []
    assert client.calls == 3


@pytest.mark.asyncio
async def test_connect_error_is_terminal_and_jitter_can_be_disabled():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("unreachable", request=request)

    client = StealthHTTPClient(
        max_retries=3,
        request_jitter=None,
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(httpx.ConnectError):
            await client.get("https://unreachable.example")
    finally:
        await client.close()
    assert calls == 1


@pytest.mark.asyncio
async def test_whatsmyname_opens_per_host_circuit_after_three_failures(monkeypatch):
    module = WhatsMyNameModule()
    module.sites_data = {
        "sites": [
            {"name": f"Dead {index}", "uri_check": f"https://dead.example/{index}/{{account}}"}
            for index in range(5)
        ]
    }
    calls = 0

    async def fail_check(site, target):
        nonlocal calls
        calls += 1
        request = httpx.Request("GET", site["uri_check"].replace("{account}", target))
        raise httpx.ConnectError("unreachable", request=request)

    monkeypatch.setattr(module, "check_site", fail_check)
    assert await module.execute(entity(), "scan-circuit") == []
    assert calls == 3
