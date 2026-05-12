# Neobanker Crawler

Standalone crawler-agent service for discovering neobanks, refreshing source data,
validating extracted records, and writing approved changes through the Neobanker backend
internal API.

This repository is the crawler service, not the existing `neobanker-agent` chatbot.
The crawler control plane runs on port `8001` and the chatbot remains on port `8000`.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                    FastAPI Control Plane :8001                   │
│  /health  /sources  /admin/runs  /review/queue  /admin/dashboard │
└────────────────────────┬─────────────────────────────────────────┘
                         │  workflow.requested event
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│               Stream Producer (InMemory / Redis Streams)         │
└────────────────────────┬─────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Orchestrator + Budget                         │
│  • Loads/saves CheckpointState (InMemory / Redis)               │
│  • Runs PlannedSteps in sequence order                           │
│  • Enforces token / USD / wall-clock / turn limits              │
└────────────────────────┬─────────────────────────────────────────┘
                         │  dispatches handlers
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│                      SubAgent Pipeline                           │
│                                                                  │
│  SourceSelectorAgent                                             │
│    ↓  picks source from config/sources.yaml for field_group     │
│  FetchAgent                                                      │
│    ↓  fetches content (HTTP / Wikipedia / PDF / AppStore /      │
│       Regulator).  Returns RawDoc with text, bytes, hash        │
│  ExtractionAgent                                                 │
│    ↓  LLM extracts structured JSON                              │
│       tier 1 → Ollama (local)                                   │
│       tier 2 → Anthropic claude-sonnet-4-6                      │
│       tier 3 → Anthropic claude-opus-4-7  (escalation)         │
│  ReconcileAgent                                                  │
│    ↓  merges candidates from multiple sources                   │
│       consensus → accepted                                       │
│       conflict   → conflict_review                               │
│  ValidationAgent                                                 │
│    ↓  validates schema + business rules                         │
│       confidence ≥ 0.75 → auto_apply                            │
│       confidence < 0.75 → needs_review                          │
│       hard errors        → invalid                              │
│  WriterAgent                                                     │
│    ↓  auto_apply  → POST /internal/crawler/upsert (MySQL)       │
│       needs_review→ InMemoryReviewQueueStore → /review/queue    │
│       invalid     → dropped, logged                              │
└──────────────────────────────────────────────────────────────────┘
```

---

## Workflows

The orchestrator supports four named workflows, each broken into a DAG of steps:

| Workflow | Steps | Default cadence |
|---|---|---|
| `discover_banks` | `discover_banks` | Daily |
| `refresh_company` | `refresh_company` | Manual |
| `refresh_field_group` | `refresh_company` → `refresh_field_group` | Manual |
| `re_extract_changed_snapshots` | `discover_changed_snapshots` → `re_extract_snapshot ×N` | Hourly |

Workflow plans are deterministic — the same params always produce the same
`checkpoint_key` and step `dedup_key`. Re-triggering an in-progress workflow
resumes from the last completed checkpoint rather than restarting.

---

## Field Groups

Each workflow targets one of these field groups. The extraction schema and
prompt are selected automatically.

| Field group | Extracted fields |
|---|---|
| `about` | company_name, description, headquarters, founded_year |
| `product` | account_types, cards, lending, supported_platforms |
| `financials` | funding_total_usd, assets_usd, revenue_usd, regulatory_status |
| `staff` | founders, executives, employee_count |
| `tech` | mobile_apps, api_surface, security_features |
| `marketing` | tagline, target_segments, brand_claims |
| `marketing.app` | app_rating, review_count, release_notes |
| `marketing.web` | homepage_claims, seo_title |
| `web3` | crypto_products, custody_model, token_support |

---

## Sources

Sources are declared in `config/sources.yaml`. Each entry maps a field group to a
fetch tool, cadence, cost tier, and URL template. No site-specific HTML parser is
needed — adding a source means adding a YAML entry.

| Source | Tool | Cadence | Cost |
|---|---|---|---|
| `official_site` | fetch.http | weekly | low |
| `wikipedia` | fetch.wikipedia | monthly | low |
| `google_play` | fetch.app_store | weekly | low |
| `app_store` | fetch.app_store | weekly | low |
| `annual_report` | fetch.pdf | quarterly | medium |
| `bank_code` | fetch.regulator | monthly | low |
| `regulator` | fetch.regulator | monthly | low |
| `search` | fetch.search | manual | medium |
| `pitchbook` | fetch.pitchbook | quarterly | high |

---

## Data Storage

| Destination | Status |
|---|---|
| **MySQL** via `POST /internal/crawler/upsert` | Ready — requires `AGENT_INTERNAL_KEY` |
| **Redis Streams** (workflow event bus) | Interface ready, in-memory placeholder active |
| **Redis** (checkpoints) | Interface ready, in-memory placeholder active |
| **MinIO** (raw content snapshots) | Tool stub exists, not yet wired |
| **Qdrant** (vector dedup for discovery) | Tool stub exists, not yet wired |
| **Review queue** | In-memory, survives only until service restart |

---

## What Is Included

- FastAPI control plane with health, workflow trigger, source registry, review queue,
  and dashboard endpoints.
- Pydantic schemas generated/checked against the backend Java entity model.
- Harness primitives for budgets, checkpoints, workflow planning, Redis-style dispatch,
  and subagent execution.
- Subagents for source selection, fetching, extraction, reconciliation, validation,
  writing, and discovery.
- Tool wrappers for HTTP/browser/PDF/app-store/Wikipedia/regulator fetches, MinIO-style
  snapshots, Ollama, Anthropic, Qdrant-like vector search, and backend writes.
- Shared raw-content guards (`src/crawler/utils/guards.py`) used across all pipeline stages.
- A source registry in `config/sources.yaml`.
- A data dictionary in `config/data_dictionary.yaml`.
- Tests for the current U01–U15 scaffold and control-plane behaviour.

The current implementation is a verified foundation and control plane. It is ready for
developer/operator use, smoke testing, and incremental wiring to real Redis/MinIO/Qdrant
workers. Full production crawling still needs long-running worker deployment connected
to real infrastructure.

---

## Requirements

- Python 3.12 or newer.
- `uv`.
- Access to the sibling backend repo when running schema parity tests:
  - local: `../neobanker-backend-MVP-V2`
  - server: `../backend`
- For backend writes:
  - Neobanker backend running, normally `http://127.0.0.1:8080`
  - `AGENT_INTERNAL_KEY` set to the same value accepted by the backend.

On the production host, `uv` is at:

```bash
/home/wenjixu/.local/bin/uv
```

---

## Install

```bash
# Local
cd /Users/xiaochong/Documents/neobanker/neobanker-crawler
uv sync

# Server
cd /home/wenjixu/neobanker/crawler
/home/wenjixu/.local/bin/uv sync
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `AGENT_INTERNAL_KEY` | Yes (for writes) | Shared secret for `POST /internal/crawler/upsert` |
| `ANTHROPIC_API_KEY` | Yes (for cloud LLM) | Anthropic API key used by `AnthropicClient` |
| `BACKEND_BASE_URL` | No | Backend base URL (default `http://127.0.0.1:8080`) |
| `OLLAMA_BASE_URL` | No | Ollama URL for local LLM (default `http://localhost:11434`) |
| `QDRANT_URL` | No | Qdrant URL for vector dedup (default `http://localhost:6333`) |

---

## Verify

```bash
uv run pytest -q
uv run mypy src tests
uv run ruff check .
```

Expected:

```
165 passed
Success: no issues found
All checks passed
```

The schema parity tests call `tools/scan_java_columns.py`, which reads backend Java
models and refreshes `src/crawler/schemas/_java_columns.json`. Run it manually after
backend model changes:

```bash
uv run python tools/scan_java_columns.py
```

---

## Run The Control Plane

```bash
# Local
uv run uvicorn crawler.api.main:app --host 127.0.0.1 --port 8001

# Server
cd /home/wenjixu/neobanker/crawler
/home/wenjixu/.local/bin/uv run uvicorn crawler.api.main:app --host 127.0.0.1 --port 8001
```

Smoke test:

```bash
curl -fsS http://127.0.0.1:8001/health
```

Expected shape:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "producer": "InMemoryStreamProducer",
  "queued_events": 0,
  "sources": 9,
  "observability_events": 0
}
```

---

## Trigger Workflows

```bash
# Discover new neobanks
curl -fsS -X POST \
  'http://127.0.0.1:8001/admin/runs/trigger?workflow=discover_banks' \
  -H 'Content-Type: application/json' \
  -d '{"requested_by": "manual", "params": {}}'

# Refresh a specific company's about field group
curl -fsS -X POST \
  'http://127.0.0.1:8001/admin/runs/trigger?workflow=refresh_field_group' \
  -H 'Content-Type: application/json' \
  -d '{
    "requested_by": "manual",
    "params": {
      "bank_id": "za-bank",
      "company_sort_id": 1,
      "field_group": "about"
    }
  }'

# List recently queued runs
curl -fsS http://127.0.0.1:8001/admin/runs
```

> **Note**: the default producer is in-memory. Triggering a workflow proves planning,
> dedup-key generation, and API shape. Production execution requires wiring the producer
> to Redis Streams and running subagent workers.

---

## Source Registry

```bash
curl -fsS http://127.0.0.1:8001/sources
curl -fsS http://127.0.0.1:8001/sources/official_site
```

---

## Review Queue

Low-confidence or conflicting validated records are routed to review instead of being
written automatically.

```bash
# List pending items
curl -fsS 'http://127.0.0.1:8001/review/queue?status=pending'

# Create a review item manually
curl -fsS -X POST http://127.0.0.1:8001/review/queue \
  -H 'Content-Type: application/json' \
  -d '{
    "field": "description",
    "proposed_value": "Digital bank for SMEs",
    "current_value": null,
    "confidence": 0.62,
    "reason": "low_confidence"
  }'

# Approve / reject an item
curl -fsS -X POST http://127.0.0.1:8001/review/queue/<item_id>/decision \
  -H 'Content-Type: application/json' \
  -d '{"decision": "approved", "reason": "verified", "reviewer": "ops"}'
```

Review decisions are terminal: an approved/rejected/skipped item will not be
overwritten by a later decision call.

---

## Dashboard

```bash
curl -fsS http://127.0.0.1:8001/admin/dashboard
```

Returns cost (tokens, USD) and freshness summaries from the in-process
`InMemoryObservabilitySink`. Replace with Langfuse/Prometheus for production.

---

## Backend Writes

Crawler writes go through the backend, never direct MySQL writes.

```
POST /internal/crawler/upsert
Header: X-Agent-Internal-Key: <AGENT_INTERNAL_KEY>
```

Backend implementation:

- `CrawlerController.java`
- `CrawlerUpsertService.java`
- `import_data/sql/create_crawler_provenance_tables.sql`

```bash
export AGENT_INTERNAL_KEY='<same value as backend>'
```

---

## Deployment On 158.132.12.57

```bash
# Server verification
cd /home/wenjixu/neobanker/crawler
UV=/home/wenjixu/.local/bin/uv
UV_CACHE_DIR=/tmp/neobanker-uv-cache "$UV" run pytest -q
UV_CACHE_DIR=/tmp/neobanker-uv-cache "$UV" run mypy src tests
UV_CACHE_DIR=/tmp/neobanker-uv-cache "$UV" run ruff check .

# Runtime smoke
UV_CACHE_DIR=/tmp/neobanker-uv-cache "$UV" run uvicorn crawler.api.main:app \
  --host 127.0.0.1 --port 8001
curl -fsS http://127.0.0.1:8001/health
```

---

## systemd

```bash
# Fill in placeholders, then install:
#   <DEPLOY_USER>  → wenjixu
#   <CRAWLER_DIR>  → /home/wenjixu/neobanker/crawler
#
# Create /etc/neobanker-crawler/env with:
#   AGENT_INTERNAL_KEY=<secret>
#   ANTHROPIC_API_KEY=<secret>

sudo cp neobanker-crawler.service /etc/systemd/system/neobanker-crawler.service
sudo mkdir -p /etc/neobanker-crawler
sudo chmod 600 /etc/neobanker-crawler/env   # after creating the file
sudo systemctl daemon-reload
sudo systemctl enable neobanker-crawler
sudo systemctl start neobanker-crawler
sudo systemctl status neobanker-crawler
```

---

## Development Workflow

1. Edit a unit.
2. Run the safety net:

   ```bash
   uv run pytest -q
   uv run mypy src tests
   uv run ruff check .
   ```

3. Deploy to the server and rerun the same checks there.
4. Smoke-test `/health`.
5. Commit and push.

GitHub repo: <https://github.com/neo-banker/neobanker-crawler>
