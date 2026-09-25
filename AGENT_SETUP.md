# Rahasya agent setup and implementation status

## Current status

Implemented: local Ollama model adapter, eight roles, validated module selection,
coordinator/specialist/reviewer loops, cited partial reports, CLI and dashboard
controls, persisted activity, and a Docker startup script for NVIDIA PCs.
Model assignments are configurable through `BRAIN__MODELS__<ROLE>`.

The existing scan orchestrator provides breadth-first traversal and invokes the
coordinator for each entity with eligible tools. After every tool result, the
specialist/coordinator can choose a different next tool. Each specialist selects
one existing module for the current entity; arbitrary shell commands and model-
supplied parameter overrides are not supported.

Full module observations are archived before review. At most 20 new candidates
per tool call enter the working graph, and five are reviewed; the remainder stay
pending or archived and do not become AI pivots. Only model-supported observations meeting the existing confidence and
module rules are queued for further investigation. Reviews never promote source
ground truth or rewrite provider confidence. A report summarizes at most six
supported observations, with validated entity references. Model judgments and
report wording are not independent verification of those sources.

Activity, usage counts, assessments, and summaries are saved inside each scan's
JSON snapshot and included in JSON exports. Agent events also appear in the
Network & Source Log. The Local AI agents dashboard page shows the saved activity
and evidence references. Refreshing the dashboard does not interrupt scans.

Remaining work: live model quality/performance evaluation on the target PC,
PostgreSQL integration, and automatic recovery of interrupted worker processes.
Snapshots preserve completed work for inspection; they do not yet resume an
agent loop after a worker crash. Existing JSON scans need no migration to load.

## Software validation

Run the tests without Ollama, model downloads, or GPU access:

```powershell
python -m pytest -q -W error::RuntimeWarning
```

Agent tests simulate the Ollama HTTP API and discovery modules. They exercise
decision validation, tool permissions, budgets, timeouts, cancellation, saved
observations, cited reports, CLI flags, and the Streamlit page. Launcher tests
replace Docker with a fake command and verify command ordering, preservation of
an existing `.env`, and stopping after each failed step. These checks do not
establish live model quality, container connectivity, or hardware performance.

## Handling large and differently formatted tool results

```text
Provider JSON / CSV / HTML / CLI reports
  -> existing per-tool parser -> shared Entity records
  -> deterministic deduplication and ranking (Python, no LLM)
  -> full observation archive + bounded working graph
  -> compact evidence cards -> agent review and decisions
```

The tool adapters already produce typed `Entity` records. A new
`brain/evidence.py` layer turns these into the same evidence-card schema for all
agents: ID, type, value, source, confidence, source attestation, source URLs,
availability, and a few allowed type-specific details. Examples are platform/bio
for a social profile and breach name/exposed data categories for a breach record.
Large metadata dictionaries, raw HTML, provider records, and unrecognized extra
fields never enter model context. Adding a tool requires a parser into this
contract; the LLM is not responsible for interpreting a new raw format.

Candidates are ordered using source-attested evidence, exact email/phone matches,
source reliability, evidence references, and provider confidence. Selection
reserves slots for different evidence types. It does not infer identity from a
matching name or promote confidence. Within each batch, duplicate identities
from the same source share a slot; full variants remain in the archive. Already
known identities are archived without spending more review slots. This can defer
additional corroboration or contradictions from another provider; it is not a
complete cross-source claim reconciliation system.

`BRAIN__MAX_CANDIDATES_PER_TOOL` defaults to 20. The reviewer sees at most five
ranked cards and the reporter at most six supported cards; neither gets a prompt
per raw record. Synthetic tests cover 10,000 observations, including strong
evidence at the end of the list, and verify constant model call counts, bounded
prompt sizes, and a small saved working graph. These are software checks, not
model or GPU benchmarks. Card lengths/counts are bounded; they are not an exact
tokenizer-based input budget.

Complete observations emitted by modules (including their metadata, not missing
original HTTP bodies) are appended once to `<scan_id>.evidence.jsonl` beside the
scan JSON. Selected records contain `metadata.raw_evidence` with a file name and
byte offset. `EvidenceStore.read(scan_id, offset)` retrieves one record without
loading the whole archive. The dashboard shows received/selected/duplicate/
deferred counts. Bulk metadata is omitted from frequent snapshot rewrites.
Existing snapshots are unchanged; deleting a scan also removes its archive.
Include this sidecar in backups: the ordinary scan JSON export contains working
candidates and references, not all archived observations.

Ranking is heuristic: archived or pending items may still be relevant. There is
no automatic second pass over them yet. This stage bounds model and snapshot
work; it still processes and archives all returned records in Python, and does
not reduce provider latency, initial parsing, or the module's peak result-list
memory. Streaming adapters and provider-side search limits remain future work.

## Models and execution

Local inference is the default because the project requires no model API charges.
Initial routing for the reported 16 GB RAM / RTX 2050 4 GB VRAM PC, pending
runtime measurements:

| Roles | Model |
|---|---|
| All eight roles | `qwen3:4b`, served by Ollama |
| Optional experiment on a machine with more available memory | `qwen3:8b` |

These choices are starting hypotheses for evaluation, not measured Rahasya
performance claims. Each role has its own assignment and allowed modules; all
roles share one downloaded model. The runtime serializes inference within each
Python process; Ollama's server configuration serializes requests across worker
processes as well. Do not start eight model server processes.
Role definitions are in `rahasya/brain/agents.py`.

The model adapter uses Ollama's local `/api/chat` endpoint with JSON schemas.
Python validates each structured decision and dispatches the selected module.
This uses schema-constrained decisions rather than native Ollama `tool_calls`.
`BRAIN__PROVIDER` currently accepts only `ollama`; there is no paid API fallback.
The role settings reject model names containing `cloud`, but this does not prove
where arbitrary aliases or custom servers execute. Disable cloud on the Ollama
server as described below. Remote model server origins and redirects are rejected.
No live model inference has been benchmarked in this implementation session;
the integration tests use simulated HTTP responses and discovery modules.

Qwen3 4B is approximately a 2.5 GB model download; 8B is approximately 5.2 GB.
These are download sizes, not RAM requirements. Model context, operating system,
Docker, database, and other applications need additional memory. The user
reports 16 GB RAM and an RTX 2050 with 4 GB VRAM. Start with a 4,096-token context,
one inference request at a time, and one loaded model. These are initial tuning
choices, not a guarantee of full GPU residency or acceptable speed. Some layers
may use system RAM/CPU. Check `ollama ps` during inference and measure real task
latency. The larger 8B download alone exceeds this GPU's VRAM, so it is not the
default for this PC.

Downloaded model inference has no per-token/API fee and needs no API key or
training. Hardware, electricity, and the initial download still have costs.
OSINT network lookups still need internet access. Optional paid OSINT providers
remain separate from the model: leave their API keys unset for the free setup.

Official model and runtime references, checked 25 September 2026:

- https://ollama.com/library/qwen3:4b
- https://ollama.com/library/qwen3:8b
- https://docs.ollama.com/capabilities/tool-calling
- https://docs.ollama.com/capabilities/structured-outputs
- https://docs.ollama.com/faq
- https://docs.ollama.com/windows

### Local model setup on Windows

Install Ollama from https://ollama.com/download/windows. In Windows user
environment variables set the following, then quit and restart Ollama:

```text
OLLAMA_NO_CLOUD=1
OLLAMA_CONTEXT_LENGTH=4096
OLLAMA_NUM_PARALLEL=1
OLLAMA_MAX_LOADED_MODELS=1
```

These variables must be set for the Ollama server process: adding them only to
Rahasya's `.env` does not configure a separately running Windows server.

From PowerShell, download and try the provisional model:

```powershell
ollama pull qwen3:4b
ollama run qwen3:4b
```

That runs a standalone model conversation, not a Rahasya investigation. Native
Python will use `BRAIN__BASE_URL=http://127.0.0.1:11434`. For the application in
Docker Desktop, `.env.example` uses `http://host.docker.internal:11434` to reach
the Windows host. Host firewall and Ollama binding must allow that connection;
container-to-host connectivity has not been verified. Keep the inference
service private. The recommended Docker option below uses a private Compose
service and avoids container-to-Windows-host networking configuration.

If `.env` already contains the previous hosted model assignments, replace its
`BRAIN__MODELS__...` values with `qwen3:4b`; changing `.env.example` does not
override an existing `.env`. Model tags are configurable independently per role.

## Running on PCs

Recommended baseline: Docker Desktop with Linux containers on Windows. The
existing Docker image provides Python 3.11 and the project dependencies. Each
developer can run an independent stack and database on their own PC. Python
3.10+ also supports editing and offline checks outside Docker.

For the reported NVIDIA PC, start Docker Desktop with the WSL2 backend and
working NVIDIA GPU support. From the repository directory in PowerShell run:

```powershell
.\scripts\start-local-ai.ps1
```

The script creates `.env` if absent, starts Ollama, downloads `qwen3:4b`, builds
and starts the application and one Celery worker, then runs a synthetic model
probe. It stops on command failures. Use `-SkipModelPull` after the model has been
downloaded. Use `-CpuOnly` if NVIDIA access is unavailable; inference may be slow.
The first run requires downloading the Docker image, Python dependencies, and
model. Existing `.env` settings are preserved; update any old hosted model names.

Open http://localhost:8501 and select **Use local AI agents** when starting a
scan. Open **Local AI agents** in the sidebar to inspect results. The overlay
sets `BRAIN__ENABLED=true`, uses `http://ollama:11434`, and disables Ollama Cloud.
It does not expose the model API on a host port. PostgreSQL, Redis, and Tor start
as existing application dependencies. Neo4j and Flower are not started.

The script uses `docker-compose.yml`, `compose.ollama.yml`, and, unless CPU-only,
`compose.ollama-gpu.yml`. Include the same files for subsequent Compose commands.
Compose configuration and PowerShell parsing were validated; container startup
and GPU inference have not been exercised in this session.

Inspect settings and test inference after building (these commands use the GPU overlay):

```powershell
docker compose -f docker-compose.yml -f compose.ollama.yml -f compose.ollama-gpu.yml exec worker python -m rahasya brain-check --probe
```

Or, with dependencies installed in a native Python environment and Ollama on Windows:

```powershell
$env:BRAIN__BASE_URL = 'http://127.0.0.1:11434'
python -m rahasya brain-info
python -m rahasya brain-check --probe
python -m rahasya scan --email 'your-address@example.org' --agentic
```

The existing `.env.example` uses Docker hostnames (`postgres`, `redis`, `tor`)
and `/app/data/...` paths. Running the full application directly in Windows
requires host-specific settings; copying that file alone does not configure a
native Windows deployment. Compose currently does not publish the database port.

### Limits and failure behavior

Default limits are 15 tool calls per scan, 3 per entity, 60 model calls, a
120-second model request deadline, 4,096 context tokens, and 512 output tokens.
The existing scan time/entity/depth limits also apply. These are configurable
under `BRAIN__...` in `.env.example`. A model-call cap is not a token cap.

Missing models, invalid decisions, invalid evidence references, or an unreachable
Ollama server fail the AI scan with an explicit error and preserve saved results.
There is no paid or standard-mode fallback. Tool failures/timeouts are recorded;
the model can choose another eligible tool within the remaining limits. Where
modules surface internal provider errors through the audit log, an empty result
is labeled inconclusive. Modules that swallow errors without logging them still
cannot distinguish failure from no results and need further provider hardening.

Hitting a hard budget completes a partial scan with a stop reason; report creation
may be skipped. Cancellation stops the active local request and scan task. The
server controls GPU scheduling; stopping a client request does not guarantee
immediate release of server-side GPU work. No automatic tool retries occur.

Only registered modules allowed for a selected specialist, compatible with the
current entity, enabled for the scan, and available in the environment can run.
Module-specific ground-truth and archive prerequisites remain enforced. Unsupported
modules, RecoveryHintProbe, and password/hash checking are outside the AI toolset.
Model inputs exclude arbitrary metadata and raw pages. Prompts treat evidence as
untrusted data; structural validation prevents it from granting new capabilities.

The initial model context contains bounded observations, not complete retrieved
documents, image pixels, or the whole graph. The media agent interprets EXIF and
hash outputs; it does not perform face identification. Task planning is currently
per entity inside the existing traversal, not a globally optimized investigation.

## Database plan

Use one PostgreSQL database, shared by agents through application repositories.
PostgreSQL is already declared in Compose with a persistent `postgres-data`
volume. No cloud database subscription or manually authored tables are needed.
Use SQLAlchemy and Alembic for schema changes.

Existing schema migrations can be applied to the local Compose database with:

```powershell
docker compose -f docker-compose.yml -f compose.ollama.yml -f compose.ollama-gpu.yml exec web python -m rahasya init-db
```

This initializes existing SQL tables; it does not move JSON scans or enable SQL
persistence in the current scan path. The next storage implementation must:

1. Extend the schema with agent tasks, tool executions, evidence, claims,
   decisions, checkpoints, and model usage linked to scans.
2. Route scan writes and dashboard reads through the same PostgreSQL repository.
3. Commit concurrent task results transactionally and prevent duplicate actions.
4. Import existing JSON scans explicitly and idempotently with backups; retain
   source files until the import has been verified.

Redis supports Celery scheduling and transient events. It is not the evidence
database. Use NetworkX as a graph view reconstructed from stored entities and
relationships; Neo4j and a vector database are optional future additions.

Docker volumes retain data across ordinary restarts and `docker compose down`.
They are not backups. Export PostgreSQL backups and preserve evidence files;
`docker compose down -v` removes named volumes and their data.

For a later shared team deployment, host one application, worker, and PostgreSQL
stack and let other PCs use the application UI. Add authentication and access
controls before exposing it to the team. Independent local Compose stacks do
not synchronize their databases automatically.
