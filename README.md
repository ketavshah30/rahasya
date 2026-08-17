# Rahasya — Digital Footprint Intelligence Platform

## Overview
Rahasya is a recursive OSINT platform that accepts minimal input and discovers a maximum digital footprint. It automates intelligence gathering across social media, breach databases, and the dark web to build a comprehensive entity profile.

## Architecture
```mermaid
graph TD
    A[Input Layer] --> B[Orchestrator]
    B --> C[Discovery Modules]
    C --> D[Social Modules]
    C --> E[Breach Modules]
    C --> F[DarkWeb Modules]
    C --> G[Multimedia Modules]
    D --> H[Correlation Engine]
    E --> H
    F --> H
    G --> H
    H --> I[Dashboard]
    H --> S[(On-disk ScanStore)]
    H -.-> J[(PostgreSQL)]
    H -.-> K[(Redis)]
    H -.-> L[(Neo4j - Optional)]
```

## Features
- Recursive discovery (BFS with depth/entity/time limits)
- Durable scan snapshots and live progress that survive refreshes and page switches
- 12+ OSINT modules (Maigret, Sherlock, WhatsMyName, HIBP, IntelX, LeakLookup, Ahmia, OnionSearch, EXIF, ImageHash, Archive.org)
- Entity resolution (fuzzy + deterministic)
- Interactive CIA Web correlation visualization (PyVis)
- Risk scoring and exposure analysis
- Dark web monitoring (via Tor)
- Celery + Redis task queue for async processing
- Production PostgreSQL storage

## Tech Stack
| Component | Technology | Purpose |
| --- | --- | --- |
| Core | Python 3.10+ | Primary logic and module execution |
| Web Dashboard | Streamlit | CIA-terminal themed user interface |
| Database | PostgreSQL | Persistent storage of entities and relationships |
| Task Queue | Celery + Redis | Asynchronous and distributed task execution |
| Graph DB | Neo4j (Optional) | Advanced relationship querying |
| Networking | Tor (Optional) | Dark web access for specific modules |

## Prerequisites
- Python 3.10+
- PostgreSQL 14+
- Redis 7+
- Tor (optional, for dark web)
- Node.js (optional, for Playwright)

## Installation
```bash
git clone https://github.com/yourusername/Rahasya.git
cd Rahasya
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -e .
cp .env.example .env
# Edit .env with your API keys and DB credentials
python -m rahasya init-db
```

## Quick Start
```bash
# Run a scan via CLI
python -m rahasya scan --name "John Doe" --email "john@example.com"

# Start the dashboard
python -m rahasya dashboard

# Start Celery worker (for async scans)
python -m rahasya worker

# Or start the complete production-shaped stack
docker compose up --build
```

Run network-heavy scans through the Linux Compose stack. The Windows host can
block high-fanout outbound connections with `WinError 10013`; the container
also verifies that current Maigret and Sherlock executables are installed.

## Configuration
| Environment Variable | Description |
| --- | --- |
| `DB__URL` | PostgreSQL connection string |
| `REDIS__URL` | Redis event connection string |
| `CELERY__BROKER_URL` | Celery broker connection string |
| `CELERY__ENABLED` | Dispatch dashboard scans to workers instead of local threads |
| `API_KEYS__HIBP` | HaveIBeenPwned API key (optional) |
| `API_KEYS__INTELX` | IntelligenceX API key (optional) |
| `API_KEYS__INTELX_TIER` | IntelligenceX instance tier: `public`, `free`, or `paid` |
| `NEO4J__URI` | Neo4j connection URI (optional) |
| `TOR__SOCKS_PORT` | Tor SOCKS port (optional) |
| `TOR__SOCKS_HOST` | Tor SOCKS host (`tor` in Compose, localhost outside it) |
| `SCAN__MODULE_TIMEOUT_SECONDS` | Default module deadline; high-fanout modules use 600-second overrides |
| `SCAN__MAX_DEPTH` | Maximum recursive depth |
| `SCAN__MAX_ENTITIES` | Maximum entity count |
| `SCAN__MAX_TIME_MINUTES` | Maximum scan duration |
| `STORAGE__SCAN_DIR` | Durable JSON scan directory |
| `STORAGE__STATE_DIR` | Durable provider quota state directory |
| `LOG_LEVEL` | Application logging level |
| `ENVIRONMENT` | Environment type (development/production) |
| `DEBUG` | Debug mode toggle |

## Module Overview
| Module | Input | Output | Requires | Notes |
| --- | --- | --- | --- | --- |
| Social | Username, Name | Profiles, Aliases | - | Uses Maigret, Sherlock, WhatsMyName |
| Breach | Email, Domain, Password hash | Breaches | API Keys for HIBP/IntelX/LeakLookup | Includes free HIBP Pwned Passwords lookup |
| DarkWeb | Email, Alias | Onion URLs | Tor | Uses Ahmia, OnionSearch |
| Multimedia | Images | EXIF Data, Hashes | - | Analyzes metadata and similarities |

## Dashboard
The Rahasya dashboard features a CIA-terminal themed interface providing actionable insights. Key pages include:
- **Scan Initialization**: Start targeted scans with minimal input.
- **Entity Resolution**: View and merge discovered entities.
- **CIA Web**: Interactive, filterable relationship mapping of digital footprints.
- **Risk Assessment**: Actionable risk scores based on breach and exposure data.

## Project Structure
```text
Rahasya/
├── rahasya/
│   ├── core/
│   ├── modules/
│   ├── db/
│   ├── cli/
│   └── dashboard/
├── tests/
├── .env.example
├── pyproject.toml
└── README.md
```

## License
MIT

## Disclaimer
This tool is for educational/ethical OSINT research only. Ensure you have authorization before scanning any targets.
