# Rahasya - Build Task Tracker

Last updated: 2026-07-31

## Phase 1: Foundation & Core Infrastructure

### Project Setup
- [x] `pyproject.toml` - dependencies, metadata, entry points
- [x] `requirements.txt` - pip fallback
- [x] `.env.example` - environment template aligned with nested Pydantic settings
- [x] `.gitignore`
- [x] `README.md`

### Configuration & Models
- [x] `rahasya/__init__.py` + `__main__.py`
- [x] `rahasya/config.py` - Pydantic Settings for database, Redis, Celery, Tor, API keys, Neo4j, HTTP, scan limits
- [x] `rahasya/core/models.py` - entity, relationship, scan request/result models
- [x] Backward-compatible aliases for legacy target fields and relationship `type`

### Utilities
- [x] `rahasya/utils/logging.py` - structured logging with loguru and named logger helper
- [x] `rahasya/utils/rate_limiter.py` - token bucket rate limiter
- [x] `rahasya/utils/validators.py` - input normalization for names, email, phone, URLs, usernames
- [x] `rahasya/utils/http_client.py` - resilient HTTP client with retry, proxy support, stealth headers

### Storage Layer
- [x] `rahasya/storage/database.py` - async SQLAlchemy engine + sessions using current settings schema
- [x] `rahasya/storage/sql_models.py` - ORM models for scans, entities, relationships, modules
- [x] `rahasya/storage/repository.py` - repository helpers

### Celery + Redis
- [x] `rahasya/celery_app.py` - Celery application factory
- [x] `rahasya/tasks/` - Celery task definitions

### Core Engine
- [x] `rahasya/core/events.py` - pub/sub event system
- [x] `rahasya/core/entity_queue.py` - Redis-backed priority queue with in-memory fallback
- [x] `rahasya/core/orchestrator.py` - recursive pivot engine
- [x] Compatibility exports in `rahasya/core/config.py`, `graph.py`, `modules.py`, `entity_resolver.py`, `validators.py`

### Module Base
- [x] `rahasya/modules/base.py` - abstract base class with setup, availability, safe execution, rate limiting
- [x] `rahasya/modules/__init__.py` - module discovery and registry

## Phase 2: Discovery Modules

### Social & Username
- [x] `rahasya/modules/social/maigret_module.py`
- [x] `rahasya/modules/social/sherlock_module.py`
- [x] `rahasya/modules/social/whatsmyname_module.py`

### Breach & Leak
- [x] `rahasya/modules/breach/hibp_module.py`
- [x] `rahasya/modules/breach/intelx_module.py`
- [x] `rahasya/modules/breach/leaklookup_module.py`

### Dark Web Engine
- [x] `rahasya/modules/darkweb/tor_manager.py`
- [x] `rahasya/modules/darkweb/onionsearch_module.py`
- [x] `rahasya/modules/darkweb/ahmia_module.py`
- [x] `data/config/onion_engines.json`

### Multimedia & Archives
- [x] `rahasya/modules/multimedia/exif_module.py`
- [x] `rahasya/modules/multimedia/image_hash_module.py`
- [x] `rahasya/modules/multimedia/archive_module.py`

## Phase 3: Correlation Engine

- [x] `rahasya/correlation/graph_manager.py` - NetworkX backend with optional Neo4j backend
- [x] `rahasya/correlation/entity_resolver.py` - deterministic, fuzzy, cross-source, and photo matching
- [x] `rahasya/correlation/relationship_rules.py` - YAML rule engine
- [x] `data/config/relationship_rules.yaml`

## Phase 4: Kundli Dashboard & Reporting

- [x] `rahasya/dashboard/app.py` - Streamlit shell + theme
- [x] `rahasya/dashboard/static/style.css` - dark intelligence-terminal theme
- [x] `rahasya/dashboard/pages/01_New_Scan.py`
- [x] `rahasya/dashboard/pages/02_Kundli_Graph.py`
- [x] `rahasya/dashboard/pages/03_Timeline.py`
- [x] `rahasya/dashboard/pages/04_Exposure_Report.py`
- [x] `rahasya/dashboard/pages/05_Export.py`
- [x] `rahasya/dashboard/components/graph_viewer.py`
- [x] `rahasya/dashboard/components/entity_card.py`
- [x] `rahasya/dashboard/components/risk_meter.py`
- [x] `rahasya/dashboard/templates/report.html`

## Phase 5: Testing & Integration

- [x] Unit tests for core modules: `45 passed, 4 skipped`
- [x] Package compile check: `python -m compileall -q rahasya`
- [x] Module registry smoke check: all 11 discovery modules discovered
- [x] Dashboard import smoke check: app and all pages import successfully
- [x] Dependency installation verified after resolving Maigret/NetworkX conflict

## Follow-Up Notes

- API-key modules remain optional and disable themselves when keys are absent.
- Tor-backed dark web search remains optional and requires local Tor with `TOR__ENABLED=true`.
- Streamlit import smoke emits expected bare-mode warnings outside `streamlit run`.
- Streamlit reports deprecations for `st.components.v1.html` and `use_container_width`; these are not blockers but should be modernized in a later polish pass.
