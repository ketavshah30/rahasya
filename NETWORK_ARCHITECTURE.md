# Rahasya Network Layer — Anti-Bot & Resilience Architecture

This document describes the hardened outbound HTTP architecture that drives Rahasya's OSINT
modules. The goal is simple: **requests should not spuriously fail.** Specifically, cut the 403
(bot-protection) and 401 (auth/soft-block) noise, stop counting *expected* negatives as failures,
and present a realistic browser network fingerprint — all **without Playwright or any browser
engine**.

## Why requests were failing

| Root cause | Symptom | Fix |
| --- | --- | --- |
| Plain `httpx` exposes a Python TLS/JA3 handshake and HTTP/1.1 only | WAFs (Cloudflare/Akamai/PerimeterX) 403 the client regardless of User-Agent | Route through `curl_cffi` Chrome **impersonation** (real browser JA3 + HTTP/2) with an httpx+HTTP/2 fallback |
| `raise_for_status()` raised on every 4xx and re-raised immediately | A 403 bot-block was never retried; an expected 404/private 403 was logged as `http_error` | Client no longer raises by default; a precise **outcome taxonomy** classifies each response |
| User-Agent re-randomised on every retry attempt | Session looked like a different browser mid-conversation (a bot tell) | One **coherent, stable** `BrowserProfile` per client; rotate only *after* a real block |
| Incoherent/empty client hints | Browser header set didn't match the UA | `Sec-CH-UA*` emitted **only** for Chromium UAs, derived from the chosen UA |
| `LiveProbe` called `client.head()` which did not exist | Every profile mis-probed as `timeout` (silent `AttributeError`) | Added `StealthHTTPClient.head()` |
| `ConnectError` re-raised on first blip | One transient DNS/connect failure killed an otherwise-reachable host | Transient transport errors are retried with backoff before giving up |

## Components

- **`rahasya/utils/fingerprint.py`**
  - `BrowserProfile` — a coherent `(User-Agent, Sec-CH-UA*, Sec-Fetch-*, Accept*, impersonate)` set.
  - `build_profile(ua, available)` — derives client hints + the closest `curl_cffi` impersonation
    target (never claims a *newer* Chrome TLS profile than the UA advertises).
  - `FingerprintManager` — holds ONE profile for a client's lifetime; `rotate()` switches to a
    different coherent browser only when a request was genuinely blocked.

- **`rahasya/utils/http_client.py` — `StealthHTTPClient`**
  - **Backend selection:** `curl_cffi.requests.AsyncSession` with `impersonate="chrome<N>"` when
    available (primary); `httpx.AsyncClient(http2=True)` fallback. A caller-supplied `transport`
    (Tor SOCKS, or a test `MockTransport`) forces the httpx backend.
  - **Outcome taxonomy** (recorded in the per-scan network audit):
    - `success` — 2xx/3xx.
    - `expected_negative` — 404/410, or a 401/403 that is a genuinely private/forbidden resource
      (no challenge). **Not** counted as a failure.
    - `auth_error` — 401/402/407 on a keyed API (bad/missing credential). **Not** bot noise.
    - `rate_limited` — 429 (honours `Retry-After`).
    - `unreachable` — host persistently refuses/times out the TCP/TLS connection (IP/egress-level
      block or dead host). Fast-failed and **excluded** from the failure metric — fingerprinting
      cannot fix an IP block.
    - `http_error` — an un-beaten bot challenge or other unexpected ≥400.
    - `failed` — transient transport error during the retry sequence.
  - **Retry policy:** 403-with-challenge / 429 / 408 / 5xx are retried with backoff **and a rotated
    fingerprint**; `Retry-After` is honoured; transient connect/read errors are retried; bare 403
    (forbidden resource) and 404/410 are terminal (not retried, not failures).
  - **Fingerprint rotation changes the JA3, not just the UA.** A block is a TLS-layer rejection, so
    on each retry the client switches to a *different coherent impersonation target*
    (`chrome124 → chrome150 → chrome146 → … → safari-desktop`), ranked by WAF acceptance
    (Chrome-desktop first, Firefox/mobile last). Observed effect: a site that 403s `chrome124`
    returns 200 on the `chrome150` retry.
  - **Fast-fail on dead hosts.** A bounded `connect_timeout` (default 8s) plus a low
    `connect_error_retry_cap` (default 2) mean an IP-blocked host is classified `unreachable`
    quickly instead of consuming the full retry budget and stalling a high-fanout scan.
  - **`head()`** with the modules' existing ranged-GET fallback.
  - `raise_for_status=True` / `expected_statuses=...` kwargs preserve old behaviour for callers who
    want it.

- **`rahasya/storage/network_audit.py`** — `summarize_events` now reports
  `expected_negative_requests` and `auth_error_requests` separately, and **excludes** both from
  `failed_requests` so the reliability metric reflects true transport failures.

## What is intentionally NOT done

- **No Playwright / Camoufox / headless Chromium.** `PlaywrightClient` remains only as a deprecated
  no-op shim for import compatibility. Anti-bot evasion is entirely network/TLS-layer.
- **Hard interactive CAPTCHAs / JS challenges are not brute-forced.** They are detected
  (`cf-challenge`) and handled by downstream gating (e.g. Wayback evidence preservation), not
  fought in a loop.

## Verifying it works

Offline unit tests (default): `pytest tests/test_http_fingerprint.py tests/test_network_audit.py`

Live end-to-end proof (opt-in, real network):

```bash
RAHASYA_LIVE_SMOKE=1 pytest tests/test_http_live_smoke.py -q
```

A quick manual JA3 check against `https://tls.peet.ws/api/all` shows the difference:

| | Plain httpx | StealthHTTPClient |
| --- | --- | --- |
| JA3 | Python/OpenSSL hash (flagged) | genuine Chrome JA3 |
| HTTP | HTTP/1.1 | HTTP/2 (`h2`) |
| UA | `python-httpx/...` | `Mozilla/5.0 ... Chrome/...` |

## Dependencies

`curl-cffi>=0.7` and `h2>=4.0` (via `httpx[http2]`) are now **required** core dependencies, not
optional anti-detection extras.
