# Rahasya — Pipeline Repair Plan

> **Goal:** Make a Rahasya scan actually produce real results end-to-end.
> Every item below is grounded in the analysis of `rahasya_network_audit_3f3e9711.json`
> (scan on 2026-08-15 that produced 2,915/2,915 network failures and 0 discoveries)
> and live verification of every third-party endpoint we call.
>
> The plan is split into two workstreams:
> - **Workstream A — Local infra & CLI adapters.** Removes the environment
>   problems (Windows firewall, wrong argv, aggressive timeouts) that killed
>   the last scan before the network was even relevant.
> - **Workstream B — Web endpoints.** Every third-party HTTP integration is
>   audited: some URLs have been retired (IntelX `2.intelx.io`, Ahmia
>   `/api/search/`), some just need a key, some need small tweaks.
>
> Only after A and B are in do we touch the dark-web deep-dive (that's Wave C,
> intentionally out of scope for this file).

Legend: `[ ]` open · `[~]` in progress · `[x]` done · `[skip]` intentionally skipped

---

## Implementation record — 2026-08-17

- [x] Workstream A code and container repairs (A.1–A.8) are implemented.
- [x] A.9 Linux acceptance scan completed with exit code 0. Scan
      `c2f1d112-2170-427a-920c-7094ab7ca89f` produced 94 deduplicated
      entities: Sherlock returned 9, Maigret 3, WhatsMyName 43, and Wayback
      41 before graph deduplication. The audit recorded 348 successful network
      requests versus 318 transport failures, plus separately classified HTTP
      errors and rate limits.
- [x] Workstream B code repairs are implemented for B.1–B.3, B.5, B.7,
      B.8's engine configuration, and B.9. Tor routing was live-tested and
      returned `IsTor: true`.
- [~] B.8's per-engine HTML selector verification remains a live-service
      maintenance check; Tor transport itself is verified.
- [ ] B.4 and B.10 require real `API_KEYS__HIBP` and `API_KEYS__INTELX`
      credentials. No secrets were fabricated or written to the repository.
- [ ] B.6 still requires the requested product decision: retain paid-only
      LeakLookup with a key, or remove it in a separate change.

The concrete CLI versions installed by the image differ from two literal flag
examples below. Maigret 0.6.4 uses `--folderoutput` and writes
`report_<target>_ndjson.json`; Sherlock 0.16.0 also uses `--folderoutput`, and
its supported machine-readable result output is CSV (`--csv`) because its
`--json` flag selects an input site catalog. The adapters use these verified
interfaces rather than the incompatible example spellings.

The saved no-key Linux baseline is available in
`data/smoke-linux/c2f1d112-2170-427a-920c-7094ab7ca89f.network.jsonl` and as
the exported JSON file `data/smoke-linux/rahasya_network_audit_c2f1d112.json`.

---

## Workstream A — Local infrastructure & CLI adapters

Fixes the root causes of the 100 % failure rate in the last audit:
1. Outbound TCP blocked at the OS level on the Windows host (`WinError 10013`).
2. Rahasya's Maigret adapter passes an argv shape the current Maigret CLI rejects.
3. Orchestrator wraps every module in a 30 s hard timeout, which is far too
   short for Maigret / Sherlock / WhatsMyName to finish.
4. Every module call sleeps 1 s (rate_limit) + 0.5–2.0 s (StealthHTTPClient
   pre-delay) before doing any work, even when we don't need throttling.

### A.1 — Move scanning off Windows onto Linux
- [ ] Run the full stack via `docker compose up --build` from the existing
      `docker-compose.yml`, or in a plain Linux VM. Every WinError 10013 in
      the audit disappears the moment we're not on Windows Defender.
- [ ] In the container image, install both external CLIs so
      `shutil.which("maigret")` and `shutil.which("sherlock")` return truthy:
      `pip install -U maigret sherlock-project` (already declared in
      `requirements.txt` and `pyproject.toml`; just verify it lands in the
      image).
- [ ] Confirm from the container: `maigret --help` and `sherlock --version`
      succeed and can reach `api.github.com` (Sherlock's startup check).

### A.2 — Fix the Maigret argv bug
**File:** `rahasya/modules/social/maigret_module.py` (lines 37–42)

Current, broken:
```python
cmd = ["maigret", target,
       "--json", temp_file,
       "--no-color",
       "--timeout", "10"]
```
Current Maigret (`>=0.5`) treats `-J/--json` as a *format selector*
(`simple` | `ndjson`), not an output path. Every invocation in the last
audit exited with `return_code 2 — invalid choice`.

- [ ] Replace the command with:
      ```python
      cmd = ["maigret", target,
             "--json", "ndjson",
             "--folderpath", tmpdir,
             "--no-color",
             "--timeout", "10",
             "--retries", "1"]
      ```
- [ ] Switch from `tempfile.NamedTemporaryFile` to
      `tempfile.mkdtemp(prefix=f"maigret_{scan_id}_")`, then read the
      resulting `<tmpdir>/report_<target>.ndjson` (Maigret writes one file
      per target inside the folder).
- [ ] `finally:` block should `shutil.rmtree(tmpdir, ignore_errors=True)`
      instead of removing a single file path.
- [ ] Parse ndjson line-by-line (each line is one site's result) rather
      than the old single-JSON `report[target]` shape.

### A.3 — Sherlock adapter hardening (no argv change, but tighten it)
**File:** `rahasya/modules/social/sherlock_module.py`

- [ ] Add `--timeout 10` to the argv so a single slow site can't stall
      Sherlock for minutes.
- [ ] Sherlock writes one file per target when `--output` is a *directory*.
      Passing the same path to `--output` and `--json` is fragile. Switch
      to `--folderpath <tmpdir>` + read `<tmpdir>/<target>.json` on
      completion. Wrap the tmpdir in the same `mkdtemp` + `shutil.rmtree`
      pattern as A.2.
- [ ] On Linux there is no version-check firewall issue. Confirm the
      startup GET to `api.github.com` works from inside the container.

### A.4 — Per-module timeouts, not a global 30 s
**Files:** `rahasya/config.py`, `rahasya/core/orchestrator.py`,
`rahasya/modules/base.py`

Currently every module gets exactly `SCAN__MODULE_TIMEOUT_SECONDS=30`.
Maigret enumerates ~3,000 sites and Sherlock ~400; 30 s is far below their
best-case runtime, so both are always cancelled mid-execution.

- [ ] In `ScanSettings`, keep the global default at 30 s (safe for API
      calls) but add:
      ```python
      module_timeout_overrides: dict[str, float] = {
          "Maigret": 600.0,
          "Sherlock": 600.0,
          "WhatsMyName": 600.0,
      }
      ```
- [ ] In `Orchestrator._run_scan_loop`, replace
      `timeout=self.config.scan.module_timeout_seconds` with:
      ```python
      timeout=self.config.scan.module_timeout_overrides.get(
          mod.name, self.config.scan.module_timeout_seconds)
      ```
- [ ] Update `.env.example` with `SCAN__MODULE_TIMEOUT_SECONDS=30` +
      documentation that overrides live in config.

### A.5 — Kill the artificial 1-second-per-module sleep for CLI modules
**File:** `rahasya/modules/base.py` (lines 36 and 192–195)

`rate_limit: ClassVar[float] = 1.0` + `await asyncio.sleep(1.0 / rate_limit)`
means every single `safe_execute` sleeps 1 s before doing anything. That's
correct for public REST APIs; it's pure waste for local CLIs and for
cached-catalog checks.

- [ ] Guard the sleep: `if self.rate_limit > 0: await asyncio.sleep(1.0 / self.rate_limit)`.
- [ ] Set `rate_limit = 0.0` on `MaigretModule`, `SherlockModule`,
      `ExifModule`, `ImageHashModule`.
- [ ] Leave `rate_limit = 1.0` (or lower) on HIBP / IntelX / LeakLookup /
      Ahmia / Wayback / WhatsMyName.

### A.6 — Kill the 0.5–2.0 s pre-request random delay for high-fanout modules
**File:** `rahasya/utils/http_client.py` (line 54)

`await asyncio.sleep(random.uniform(0.5, 2.0))` before every request means
WhatsMyName's 970-site catalog spends ~1,200 s in pure sleep on top of
network time. Useful for stealth-scraping single sites; catastrophic for
batch checks.

- [ ] Move the delay behind a constructor flag:
      `StealthHTTPClient(..., request_jitter: tuple[float, float] | None = (0.5, 2.0))`.
- [ ] Pass `request_jitter=None` when the client is constructed for
      `WhatsMyNameModule` (batch, self-throttled by semaphore).
- [ ] Keep the default jitter on for HIBP / IntelX / Wayback / Ahmia.

### A.7 — WhatsMyName concurrency & per-host circuit breaker
**File:** `rahasya/modules/social/whatsmyname_module.py`

- [ ] Bump `asyncio.Semaphore(30)` → `asyncio.Semaphore(150)`.
- [ ] Remove the `await asyncio.sleep(0.1)` inside `bound_check` — with the
      global request jitter off and the semaphore doing the throttling, the
      extra sleep is pointless.
- [ ] Add a simple per-host failure counter: after N (default 3) consecutive
      `ConnectError`/`ConnectTimeout` from a host, skip its remaining checks
      for the rest of this scan. Prevents the "single dead host burns 3
      retries × M attempts" pattern that produced 2,915 events from 236
      hosts in the audit log.
- [ ] Optional: read `GITHUB_TOKEN` from env and send it as a Bearer on the
      catalog fetch (`raw.githubusercontent.com`) so we're on the 5,000
      req/hr auth tier instead of the 60 req/hr unauth tier. Only relevant
      for cache misses.

### A.8 — Skip retries on hard connect failures
**File:** `rahasya/utils/http_client.py` (retry loop)

Currently `ConnectError` retries 3× with exponential backoff. A hard
"connection refused" or "no route to host" is not going to succeed on
retry within the same scan; retrying just burns wall-clock time.

- [ ] Treat `httpx.ConnectError` (as opposed to `ReadTimeout` or
      `RemoteProtocolError`) as terminal after the first attempt.
      `ReadTimeout` and 5xx keep the current retry behaviour.

### A.9 — Verify with a Linux smoke scan (acceptance test for Workstream A)
- [ ] Run: `python -m rahasya scan --username <known live handle>
      --max-depth 1` inside the Linux container.
- [ ] Expected: Maigret and Sherlock exit 0, WhatsMyName completes with
      non-zero result count, and the audit file shows the majority of
      `network_request` events with `outcome: success`, not `failed`.
- [ ] Save the resulting audit JSON for the record; that's our new
      baseline.

---

## Workstream B — Web endpoints

Every module that talks to a third-party HTTP service, verified live during
this session. Only the ones flagged **HIGH** actually block a scan today.

### B.1 — HIGH — IntelligenceX base URL is retired
**File:** `rahasya/modules/breach/intelx_module.py` (line 16)

Rahasya hardcodes `BASE_URL = "https://2.intelx.io"`. Verified live:
`GET https://2.intelx.io/` → 404 (the CDN returns a single `\n`). IntelX
retired the shared instance on 2025-03-08 and split it per tier
(<https://blog.intelx.io/2025/03/08/new-search-api-instances/>):

| Tier | Correct base URL |
|---|---|
| Non-registered / anonymous | `https://public.intelx.io` |
| Free registered | `https://free.intelx.io` |
| Paid API license | `https://2.intelx.io` |

- [ ] Add `IntelXSettings(BaseModel)` to `rahasya/config.py` with
      `tier: Literal["public","free","paid"] = "free"` and a
      `base_url` property that maps tier → URL.
- [ ] In `IntelXModule`, replace the class-level `BASE_URL` with
      `self.config.intelx.base_url`.
- [ ] Update `.env.example` with `API_KEYS__INTELX_TIER=free`.
- [ ] Persist `daily_usage` to disk (currently RAM-only, reset on every
      process restart, so quota tracking is unreliable). Use
      `data/state/intelx_usage.json` with `{date: YYYY-MM-DD, count: N}`
      and reset on date rollover.
- [ ] Endpoint paths (`/intelligent/search` and
      `/intelligent/search/result?id=…`) are unchanged — do NOT touch them.

### B.2 — HIGH — Ahmia clearnet JSON API is retired
**File:** `rahasya/modules/darkweb/ahmia_module.py`

Rahasya calls `https://ahmia.fi/api/search/?q=…`. Verified live: **404 Not
Found**. Ahmia removed the JSON search API and their `/documentation/`
page (also 404). The HTML `/search/?q=…` endpoint 302-redirects
automated queries back to `/`. Web search confirms no successor API.

- [ ] Rewrite `AhmiaModule.execute` to HTML-scrape `https://ahmia.fi/search/?q=…`
      with a real browser-shaped `User-Agent` and `Accept: text/html`
      header. Parse `<li class="result">` items to extract title / url /
      description.
- [ ] Accept that this will be flaky — Ahmia actively resists automated
      querying. If we get an empty response or a 302 to `/` more than
      twice in a scan, disable the module for the rest of the scan and
      record `module_skipped / outcome=degraded` in the audit log.
- [ ] Alternative to consider (do not implement in this pass): move
      Ahmia entirely into the Tor-only OnionSearch flow using the
      `.onion` search endpoint. Cleaner but requires Tor to always be
      up.

### B.3 — HIGH — TorManager uses `socks5://` instead of `socks5h://`
**File:** `rahasya/modules/darkweb/tor_manager.py` (line 17)

Current: `self.proxy_url = f"socks5://127.0.0.1:{self.socks_port}"`.

Without the `h`, DNS resolution happens on the *local* resolver, not
inside Tor. That means:
- DNS leaks — the local resolver sees which onion the client wants.
- `.onion` names literally cannot resolve locally, so every OnionSearch
  request fails before a packet leaves the box.

- [ ] Change to `socks5h://127.0.0.1:{socks_port}` in `TorManager.__init__`.
- [ ] Grep the tree for any other `socks5://` occurrences and normalize.
- [ ] Add a unit test that asserts the proxy URL scheme is `socks5h`.

### B.4 — HIGH — HIBP: add API key configuration (no code change)
**File:** `.env` (create from `.env.example` if missing)

Live-verified today: `GET https://haveibeenpwned.com/api/v3/breaches` still
returns the full breach catalog with `200 OK`. The URLs, headers, and
response shape Rahasya expects are all current. The module is skipped
purely because no key is configured.

- [ ] Set `API_KEYS__HIBP=<your key>` in `.env`. Pwned 1 (10 rpm, ~$4/mo)
      is enough for a demo.
- [ ] Optional: pool multiple keys via
      `API_KEYS__HIBP_KEYS=["key-a","key-b"]` — the rotation code already
      exists in `BaseModule.rotate_api_key`.
- [ ] No source changes required.

### B.5 — MED — Add HIBP Pwned Passwords module (free, no key)
**New file:** `rahasya/modules/breach/hibp_passwords_module.py`

Verified live: `GET https://api.pwnedpasswords.com/range/5BAA6` returns
200 OK with the SHA-1 suffix range. Completely free, no key, no rate
limit published. `FIXES.md` used to claim this module existed; it does
not.

- [ ] New `HIBPPasswordsModule(BaseModule)`:
      - `accepts = [EntityType.PASSWORD_HASH]` (add this new EntityType
        if not present, or accept a string value that looks like a SHA-1).
      - Compute SHA-1 of the input, split into 5-char prefix + 35-char
        suffix, `GET https://api.pwnedpasswords.com/range/{prefix}`,
        look up the suffix in the returned line list.
      - Emit a `BreachRecord` entity when found, with `severity=High` and
        `source_name="HIBP Pwned Passwords"`.
- [ ] Register it via the existing `ModuleRegistry` auto-discovery.
- [ ] Add a tiny test with a known-pwned password like `password`
      (SHA-1 `5BAA61E4C9B93F3F0682250B6CF8331B7EE68FD8`).

### B.6 — MED — LeakLookup: decide keep-and-pay vs. drop
**File:** `rahasya/modules/breach/leaklookup_module.py`

Endpoint `https://leak-lookup.com/api/search` is correct. Free tier is
gone as of 2024; paid-only now.

- [ ] Decision required from user: pay for a LeakLookup key, or drop
      the module. If dropped, we can replace it in a later pass with
      BreachDirectory and/or HudsonRock Cavalier free tiers (both
      require writing new modules — not in this workstream).

### B.7 — LOW — WhatsMyName endpoint pattern: keep the raw GitHub JSON
**File:** `rahasya/modules/social/whatsmyname_module.py` (line 17)

User asked whether we should switch from
`raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json`
to a "proper" `whatsmyname.app` endpoint. Verified live:
- `https://whatsmyname.app/` → HTTP 464 (backend down).
- No public search REST API exists on `whatsmyname.app` — it's a
  browser UI that reads the same JSON file.
- The upstream README (`github.com/WebBreacher/WhatsMyName`) states
  explicitly that reading `wmn-data.json` IS the sanctioned integration
  pattern.
- The `whatsmyname.ink` / `whatsmyname.io` results from web search are
  third-party paid re-wrappers requiring `X-API-KEY`; ignore them.

- [ ] No endpoint change. Rahasya is already doing the correct thing.
- [ ] Cosmetic: switch the catalog URL to the fixed-commit form or the
      lightweight `wmn-data-min.json` variant to speed up cache misses.
      Not urgent.

### B.8 — LOW — OnionSearch engines file: drop the duplicate, add a couple
**File:** `data/config/onion_engines.json`

Current entries:
1. Ahmia Onion (v3, valid)
2. Ahmia Clearnet (`https://ahmia.fi/search/?q=…`) — duplicates the
   AhmiaModule; also flaky (see B.2). **Drop this.**
3. Haystak (v3, valid)
4. Torch (v3, valid)

- [ ] Remove the "Ahmia Clearnet" entry — it is redundant with
      `AhmiaModule` and doubles the failure surface.
- [ ] Optional: add DuckDuckGo Onion mirror as a fifth engine
      (`https://duckduckgogg42xjoc72x3sjianso2pfpt5obsmzjhoqcwxvtzgw.onion/?q={query}`).
      Best-effort general search.
- [ ] Endpoint pattern in `search_engine()` (BeautifulSoup CSS selectors
      per engine) needs a manual verification pass once we have a Tor
      instance up — HTML markup on those engines drifts.

### B.9 — LOW — Wayback: polish retries + polite UA
**File:** `rahasya/modules/multimedia/archive_module.py`,
`rahasya/utils/http_client.py`

Verified live: `archive.org/wayback/available` returned 502 and
`web.archive.org/cdx/…` returned 503 on my tests. Endpoints are the
correct ones; Internet Archive has been unstable throughout 2024–2026.

- [ ] Give Wayback its own `max_retries=5` with jittered backoff (not the
      global 3).
- [ ] Set `User-Agent: "Rahasya OSINT Platform (contact: <maintainer>)"`
      for Wayback specifically. IA asks for identifying UAs, not stealth
      ones, and is less likely to 429 an identified client.

### B.10 — Verify with a Linux smoke scan (acceptance test for Workstream B)
- [ ] With HIBP key set, IntelX tier=free, and Ahmia rewritten, run a
      scan for a target with a real, published breach (any email of a
      Troy Hunt demo dataset works).
- [ ] Expected: HIBP produces `BreachRecord` entities. Ahmia scrape
      returns at least the first-page results for a common query.
      IntelX no longer 404s at the transport layer.
- [ ] Save the resulting audit JSON as our second baseline.

---

## What's explicitly out of scope for this file

The previous version of `FIXES.md` claimed the following modules exist
and are "done". None of them are in `rahasya/modules/`. **Do not
implement any of them in this pass.** If we want them, they get their
own workstream after A and B are green:

- Google / Twitter / Instagram / PayPal recovery-hint enumeration
- RecoveryMatcher, SocialGraphScraper
- PeopleSearch, Pipedream, TrueCaller, FamilySearch / Geni
- Blackbird, Holehe, Socialscan, Toutatis, GHunt, Emailrep
- Dread / Hunchly / BreachDirectory / HudsonRock / LeakPeek / Snusbase
- GitHub / account-age / email-age modules
- Yandex / Bing / PimEyes reverse-image adapters
- Numverify / Twilio Lookup / Hunter / EmailHunter

The dark-web deep-dive that the user flagged for "after this pass" is
also explicitly out of scope here.

---

## Global acceptance criteria

The plan is done when all of the following hold:

1. A fresh scan on Linux (`docker compose up` or a Linux VM) with no API
   keys configured produces >0 discovered entities from Maigret + Sherlock
   + WhatsMyName + Wayback.
2. The resulting `data/scans/<scan-id>.network.jsonl` audit shows a
   majority of `network_request` events with `outcome: success`.
3. With `API_KEYS__HIBP` and `API_KEYS__INTELX` set (free tier for the
   latter), a scan on a known-breached email adds `BreachRecord` and
   `LEAK_RECORD` entities.
4. `TorManager.proxy_url` starts with `socks5h://`.
5. No module in `rahasya/modules/` still references `2.intelx.io` as a
   hardcoded URL or `ahmia.fi/api/search/` as an endpoint.
6. Maigret no longer emits `argument -J/--json: invalid choice`.
