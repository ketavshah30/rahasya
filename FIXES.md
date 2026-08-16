# Rahasya — Hardening & Feature Waves

> **Goal (per user):** Rahasya must accept any datapoint about a person (name, email, phone, username, photo) and recursively spider the internet to (a) find every online identity, (b) build a timeline of that person's online presence, and (c) correlate people to each other like a web — parents, siblings, alt accounts sharing recovery phone / email, employers, etc. The dashboard must persist scans across tab switches and refreshes, and the correlation + dark-web + social modules must actually *work* end-to-end.

Legend: `[ ]` open · `[~]` in progress · `[x]` done · `[skip]` intentionally skipped

---

## Wave 1 — Persistence, UX fixes & correctness (Priority: CRITICAL)

Fixes the #1 user complaint (scans vanish on tab switch), makes the graph tab useful, and cleans the correctness/security bugs that will bite Wave 2+ if we don't fix them now.

### 1A. Scan persistence & dashboard state (fixes "scan disappears on tab switch")
- [x] Add on-disk scan store at `data/scans/<scan_id>.json` (JSON file per scan — no PostgreSQL required for dev; DB path remains for prod later)
- [x] New `rahasya/storage/scan_store.py` with `ScanStore.save(result)`, `.load(scan_id)`, `.list()`, `.delete()`
- [x] Orchestrator persists scan state (entities, relationships, stats, request) after every depth level and again at completion
- [x] Dashboard `state.py`: replace `st.session_state`-only cache with reads from `ScanStore` so scans survive tab switches and full refreshes
- [x] Sidebar: real "Recent Scans" list read from disk with click-to-select (so user can jump back to any historical scan)
- [x] Sidebar/tab: currently-selected scan id shared across all pages via `st.session_state.current_scan_id` + fallback to newest scan from store
- [x] Sidebar system status stops lying: check real Postgres/Redis/Tor availability with quick socket probes (fallback to "N/A" not fake green)

### 1B. Rename "Kundli Graph" → funky CIA-style label
- [x] Rename dashboard page file `02_Kundli_Graph.py` → `02_CIA_Web.py`
- [x] Update page title, sidebar link text, hero copy on main page ("Kundli Graph" → "CIA Web / Correlation Web")
- [x] Update README + implementation_plan references

### 1C. Correctness & security bug fixes (from Wave-1 audit)
- [x] `utils/http_client.py`: remove `verify=False`; add optional `ssl_verify` config flag (default: True)
- [x] `modules/base.py::setup()`: stop overwriting `self.http_client` that subclasses set in `__init__`; centralize client creation so HIBP/Ahmia/LeakLookup/IntelX/WhatsMyName/OnionSearch don't leak file descriptors
- [x] Wrap all user-controlled query values with `urllib.parse.quote_plus` (HIBP, Ahmia, WhatsMyName)
- [x] Maigret/Sherlock: use `tempfile.NamedTemporaryFile` + `try/finally` for temp report files (concurrent-safe, always cleaned)
- [x] Orchestrator `cancel_scan`: use `asyncio.Task.cancel()` on the stored task instead of the max-entities hack
- [x] Orchestrator: keep a reference to the created task so it isn't GC'd
- [x] Orchestrator: enforce `confidence_threshold` before enqueueing a discovered entity for the next depth
- [x] Wrap every `mod.safe_execute(...)` call in `asyncio.wait_for(...)` with per-module timeout (30s default, configurable)
- [x] `StealthHTTPClient`: don't retry on 4xx *except* 429
- [x] Neo4j edge type is whitelisted against `RelationshipType.value` before Cypher f-string interpolation
- [x] `EntityResolver`: use `entity.normalized_value` uniformly (single source of truth) instead of re-lowering `entity.value`

### 1D. Streamlit deprecation & UX cleanup
- [x] Replace `use_container_width` deprecations with `width='stretch'` where warned
- [x] Wrap graph HTML in `components.v1.html` cleanly with height fallback
- [x] Add a **scan detail bar** at the top of every page showing which scan is selected + quick switch dropdown

### 1E. Testing this wave
- [x] Add integration test: run a scan → switch state → reload result from `ScanStore` → verify entities/relationships intact
- [x] Add unit tests: `EntityResolver` normalized-value dedup, orchestrator confidence-threshold filter, per-module timeout enforcement
- [x] Confirm `pytest` suite still fully green (49 passed / 4 skipped)

---

## Wave 2 — Correlation Engine v2 (the "web")  ✅ COMPLETE
> **This is the wave that makes the tool actually *correlate people* — the user's core ask.**

### 2A. Deep entity resolution across scans and within scan
- [x] Introduce a "Person Cluster" abstraction: a virtual person made up of multiple related entities (emails, phones, usernames, profiles) resolved to be the same real-world person
- [x] Cross-signal resolver: same phone/email/username appearing on multiple profiles → merge cluster (deterministic via `normalized_value`)
- [x] Recovery-phone / recovery-email pivots: when the same phone is seen as recovery hint on two different accounts, link the underlying persons with a `SHARES_RECOVERY` relationship + confidence based on obfuscation partial match
- [x] Name + DOB + location fuzzy resolver: RapidFuzz across profiles; only merges people when at least 2 of {full name, DOB, city, phone-suffix, email-prefix} match
- [x] Photo pHash resolver already exists → upgrade with Hamming distance ≤ 6 for merge, 7–12 for `LIKELY_SAME`
- [x] Add `RelationshipType.SHARES_RECOVERY`, `PARENT_OF`, `SIBLING_OF`, `SPOUSE_OF`, `WORKS_WITH`, `MENTIONS`, `ALT_ACCOUNT_OF`

### 2B. Recovery-hint enumeration (killer feature)
- [x] `GoogleRecoveryModule` — given a phone or email, probe Google account recovery flow (`https://accounts.google.com/signin/v2/usernamerecovery`) to enumerate email hints that match the number/email; parse the masked email/phone (e.g. `j••••e@gmail.com`, `•••••••90`)
- [x] `TwitterRecoveryModule` — POST to `https://api.twitter.com/i/users/email_available.json` / `phone_available.json` and to legacy `password_reset` endpoint to enumerate masked emails/phones
- [x] `InstagramRecoveryModule` — `https://www.instagram.com/accounts/account_recovery_send_ajax/` returns masked hints; parse partial email/phone
- [x] `PayPalRecoveryModule` — masked phone from PayPal password reset
- [x] All recovery modules produce `PARTIAL_EMAIL` / `PARTIAL_PHONE` entities that the resolver can pattern-match against known full values
- [x] New entity types: `PARTIAL_EMAIL`, `PARTIAL_PHONE` with a `matches_pattern(known_value)` helper (e.g. `j••••e` matches `john.doe`)
- [x] `RecoveryMatcherModule` — pivots partial hints from all recovery modules against known/discovered full values in the graph and creates high-confidence `ALT_ACCOUNT_OF` edges when they lock in

### 2C. Relationship discovery from profile scraping
- [x] `SocialGraphScraper` — for each discovered `SocialProfileEntity`, use requests-html / Playwright to pull:
      - bio text (extract emails, phones, usernames, other social links)
      - "About" / "Family & Relationships" (Facebook)
      - Instagram tagged posts (co-appearances)
      - Twitter/X "who they follow" (up to N) + list of most-mentioned handles
      - LinkedIn "Contact info" / "Experience" (job → colleagues)
- [x] Parse `rel_of_relative` / mention-graph co-occurrence: if X mentions Y more than N times on public posts → create `KNOWS` edge (weighted)
- [x] From LinkedIn: "Works at" → `WORKS_AT` edge to a `CompanyEntity` (new light entity type)
- [x] Add `CompanyEntity` and `RelationshipType.EMPLOYED_AT` / `WORKS_WITH`

### 2D. Family enumeration
- [x] `PeopleSearchModule` — clearnet aggregators that expose family: `truepeoplesearch.com`, `fastpeoplesearch.com`, `beenverified` (paid), `whitepages.com`, `spokeo` — Playwright + rotating UA (all US-centric but great for anglophone targets)
- [x] `PipedreamModule` — social-graph correlation via Pipl-like public trials
- [x] India-specific `TrueCallerModule` — reverse phone → name lookup (Truecaller has a web endpoint and an unofficial mobile API; account required)
- [x] `Familysearch.org / geni.com` public tree lookup for known persons of interest
- [x] Emit `PARENT_OF`, `SIBLING_OF`, `SPOUSE_OF` edges tagged with source module + confidence

---

## Wave 3 — OSINT breadth (find every online identity) ✅ COMPLETE
> Aggressively expand what Rahasya can scrape for a single person.

### 3A. Username hunting depth
- [x] Wire **Maigret Python API** (`import maigret`) instead of subprocess — richer output, tags, direct control over sites list
- [x] Add **Blackbird**, **Holehe** (email → services registered), **Socialscan**, **Toutatis** (Instagram OSINT) as first-class modules
- [x] `HoleheModule`: given email, enumerate 100+ services where an account exists (Twitter, Instagram, Adobe, Spotify, Amazon, etc.)
- [x] `SocialscanModule`: async username/email presence checker
- [x] `ToutatisModule`: extract obfuscated phone/email from a public Instagram account
- [x] `GHunt` for Gmail address → Google profile / Maps reviews / YouTube channel
- [x] `EmailrepModule` — emailrep.io API (free) for email reputation + social profile hints

### 3B. Deep-web / dark-web that actually works
- [x] Fix `OnionSearchModule` engines file to include working endpoints (Ahmia clearnet, Torch mirror, Haystak; auto-skip on 404); load engines eagerly on setup, not per-scan
- [x] Add **Ahmia** always-on (clearnet API — no Tor needed) so dark-web works even without Tor
- [x] Add **Dread**, **Hunchly** clearnet mirrors search when available
- [x] Add **BreachDirectory** (`breachdirectory.org`) — free tier for hash lookup + username pivots
- [x] Add **HudsonRock's Cavalier free API** — password-stealer database (excellent for correlating alt accounts by malware infection)
- [x] Add **DeHashed alternative**: **LeakPeek**, **Snusbase** (both offer limited free/API tiers)

### 3C. Timeline of online presence
- [x] `ArchiveModule`: for every discovered profile URL, hit Wayback Machine CDX API: `http://web.archive.org/cdx/search/cdx?url={url}&output=json` — get list of `(timestamp, url)` pairs
- [x] Emit `TimelineEvent` mini-entities with `event_type` (profile_created, first_snapshot, first_breach_seen, first_dark_web_mention) so Timeline page can render a real Plotly Gantt
- [x] `GitHubModule`: profile creation date, first commit date, contribution graph → adds to timeline
- [x] `TwitterAccountAge` — parse account created date from public snapshot / snowflake ID
- [x] `EmailAgeModule` — emailrep + Gravatar age hint

### 3D. Photo intelligence
- [x] Wire OpenCV Haar face detection into `ImageHashModule` (already in requirements)
- [x] Reverse-image search adapter: **Yandex** first (least anti-bot), then **Bing images**; scrape by uploading to their web endpoints via Playwright
- [x] **PimEyes** stub (paid, but architecturally ready for user's API key)
- [x] Extract `phash`, `dhash`, `whash` and store all three on `PhotoEntity` for better matching

### 3E. Number/email intel
- [x] `NumverifyModule` — free tier for carrier / country lookup (500/month)
- [x] `TwilioLookupModule` — carrier + line-type
- [x] `TruecallerModule` (web scrape or unofficial mobile API) — reverse phone → name (India-heavy dataset)
- [x] `EmailHunterModule` (hunter.io free tier) — find emails at a company domain
- [x] `HaveIBeenPwnedPasswordsModule` — free k-anonymity endpoint for password-hash prefix lookup (already documented as free)

---

## Wave 4 — Live/reactive dashboard & CIA-terminal polish

### 4A. Real-time updates
- [x] Move scan execution to a background thread/process — Streamlit page returns immediately with a scan id, then polls the `ScanStore` every N seconds
- [x] Progress bar shows current depth, module in progress, entities so far — read from a lightweight `scan_status.json` written by orchestrator every event
- [x] `st_autorefresh` on the CIA Web / Timeline / Exposure Report pages while scan is running

### 4B. Interactive CIA Web (correlation graph)
- [x] Replace the flat PyVis dump with a filterable graph:
      - filter by entity type
      - filter by relationship type (e.g. only `SHARES_RECOVERY` + `ALT_ACCOUNT_OF` to see the alt-account cluster)
      - filter by confidence slider
      - highlight the "Person Cluster" boundaries (color rings)
- [x] Click a node → side panel with full details, breach list, mentions, source modules
- [x] Path finder: pick 2 nodes → show shortest path in the graph (why is X connected to Y?)
- [x] Physics presets: "Force-Directed", "Hierarchical (by depth)", "Cluster-focused"

### 4C. Timeline page
- [x] Plotly Gantt of all `TimelineEvent`s + breach dates + first-seen-on-platform dates
- [x] Group by entity so timeline shows life of each identity
- [x] Zoom + hover with source module + URL

### 4D. Exposure/risk model calibration
- [x] Replace the arbitrary `min(100, x*35)` with a documented scoring rubric (weights per breach severity, dark-web mention count decay, count of unique platforms, sensitivity of data leaked)
- [x] Show category radar chart + top 5 "why this score is high" rationales
- [x] Recommendations engine (delete X account, rotate password on breached site Y, enable 2FA)

### 4E. Cosmetics
- [x] Rename all "Kundli" references (already partial in Wave 1B) — sweep entire codebase
- [x] CIA-terminal chrome: monospace / phosphor-green + cyan accents, subtle scanline overlay
- [x] Sidebar: "Active Investigation" panel with scan id, target summary, live entity count
- [x] Sidebar: current-user footer (single-user mode for now)

---

## Wave 5 — Infrastructure & scale (defer until Waves 1–4 land)

- [x] Actually use Celery for scan dispatch (currently CLI runs orchestrator inline)
- [x] Redis pub/sub for `EventBus` so dashboard receives events across process boundaries
- [x] Alembic migrations (real migrations, not `create_all`)
- [x] Docker Compose (Postgres + Redis + Tor + Neo4j + web + worker + flower)
- [x] Prometheus metrics endpoint on worker
- [x] Rate-limit-aware API-key rotation (multiple HIBP / IntelX keys)
- [x] CI: GitHub Actions running pytest + ruff + mypy

---

## Cross-cutting policies

- Every new module MUST inherit `BaseModule`, declare `accepts` / `produces`, and be discoverable by `ModuleRegistry` auto-scan
- Every new relationship type MUST be added to `RelationshipType` enum AND to the Neo4j whitelist AND to the resolver rules
- Every new entity subclass MUST include `normalized_value` semantics that make it dedup-safe across scans
- Persist to `ScanStore` after every meaningful mutation — never rely on Streamlit session state as the source of truth
- Follow ethical / legal boundary policy from the plan (no credential stuffing, no unauthorized access, no scraping private walled content behind a login the user doesn't own)
