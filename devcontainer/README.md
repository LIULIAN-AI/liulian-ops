# Neobanker — Local Development Environment

[![Lint](https://github.com/neo-banker/neobanker-dev-env/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/neo-banker/neobanker-dev-env/actions/workflows/lint.yml)
[![Infra Bootstrap](https://github.com/neo-banker/neobanker-dev-env/actions/workflows/infra.yml/badge.svg?branch=main)](https://github.com/neo-banker/neobanker-dev-env/actions/workflows/infra.yml)
[![Full Bootstrap](https://github.com/neo-banker/neobanker-dev-env/actions/workflows/full.yml/badge.svg?branch=main)](https://github.com/neo-banker/neobanker-dev-env/actions/workflows/full.yml)

One-command bootstrap for the **full Neobanker stack** (frontend, backend, agent, DB, Redis, Elasticsearch) on a developer's local machine.

> 🎯 This is the **local-machine** path. For GitHub Codespaces, see the `feat/codespaces-auto-setup` branch.

中文：[`README.zh-CN.md`](./README.zh-CN.md)

---

## Platform Support — what's actually verified

| Platform | Lint | Infra-only | **Full stack** | Note |
|---|:---:|:---:|:---:|---|
| **Linux** (Ubuntu 22.04+) | ✅ CI | ✅ CI | 🟡 manual‡ | native docker; bootstrap.sh end-to-end works, but **CI only checks the docker layer** |
| **macOS** (Apple Silicon / Intel) | ✅ CI | 🟡 manual† | 🟡 manual | needs Docker Desktop / OrbStack / Rancher Desktop |
| **Windows 10/11** | ✅ CI | 🟡 manual† | 🟡 manual | needs WSL2 + Docker Desktop |

CI status badges reflect **live** test results from the latest commit on `main`.

### What CI actually verifies (be honest about it)

| Job | What it does | What it does NOT do |
|---|---|---|
| **Lint** (3 OSes) | Bash `bash -n` syntax check, env-template KEY=VALUE format, `docker compose config -q` (Linux only) | Does not start any container |
| **Infra-only** (Linux) | `bash scripts/bootstrap.sh --infra-only` → spins up MySQL/Redis/Elasticsearch containers + `docker exec ... ping` each | Does **not** clone the 3 service repos, does **not** start backend/agent/frontend, does **not** import data, does **not** test API endpoints |
| **Full** (Linux, gated) | Would clone all repos + start backend/agent/frontend + run end-to-end smoke test | **Currently disabled** — needs `BACKEND_REPO_TOKEN` / `FRONTEND_REPO_TOKEN` / `AGENT_REPO_TOKEN` GitHub secrets to clone the private repos. See [docs/ci-roadmap.md](docs/ci-roadmap.md) |

> **Bottom line**: the green ✅ above means "the docker compose stack starts cleanly on Linux". It does **not** mean "the whole Neobanker app works on every PR". For the latter, see the [CI Roadmap](docs/ci-roadmap.md).

> † macOS/Windows infra-only is "manual" because GitHub Actions free runners
> don't ship docker (macOS) or only ship Windows-container mode (Windows). The
> script itself works locally on those OSes — see [docs/troubleshooting.md](docs/troubleshooting.md#why-no-cidocker-on-macos-and-windows).
>
> ‡ Linux full-stack is "manual" because cloning private repos in CI requires
> GitHub PAT secrets that are not yet configured. The script works locally for
> developers with SSH access — verified by hand on 2026-05-09.

---

## What you get after `bootstrap.sh`

| Service | Port | URL |
|---|---:|---|
| Frontend (Next.js 14) | 3000 | http://localhost:3000/homepage |
| Backend (Spring Boot 3.1) | 8080 | http://localhost:8080/actuator/health |
| Agent (FastAPI) | 8000 | http://localhost:8000/docs |
| MySQL 8.0 | 3307 | `mysql -uroot -proot neobanker` |
| Elasticsearch 7.17 | 9200 | http://localhost:9200/_cat/indices?v |
| Redis 7 | 6379 | `redis-cli` |
| phpMyAdmin (optional) | 8088 | http://localhost:8088 |

Plus pre-imported data: **35,000+ rows** across 11 business tables (575 banks, 27,500 news, 2,700 products, etc.) and **575 companies indexed in Elasticsearch**.

---

## Prerequisites (install on host)

| Tool | Version | Install |
|---|---|---|
| Docker | 20+ with `docker compose` plugin | https://docs.docker.com/get-docker/ |
| Git | any | usually preinstalled |
| Java | **17** (Temurin or OpenJDK) | `sdk install java 17.0.10-tem` or your package manager |
| Node | **20** | https://nodejs.org or `nvm install 20` |
| Python | **3.12** | https://python.org or `pyenv install 3.12` |
| `uv` | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

> Versions match `deploy.yml` of each service repo (Java 17 / Node 20 / Python 3.12).

---

## Quickstart — Pick your path

### Path A — One-shot (fastest)

```bash
git clone git@github.com:neo-banker/neobanker-dev-env.git
cd neobanker-dev-env
bash scripts/bootstrap.sh
```

**What happens** (≈ 5–15 min on first run):
1. Pre-flight tool checks
2. Clones 3 service repos into `./repos/`
3. `docker compose up` for MySQL + Redis + Elasticsearch
4. Patches `.env` files from `templates/env/`
5. `npm install` + `uv sync` (first run only)
6. Starts Spring Boot backend with env-var overrides for cross-container networking
7. Imports CSV data (~30 sec)
8. Bulk-loads 575 companies into Elasticsearch
9. Seeds `search_logs` so homepage hot-search chips work
10. Starts agent + frontend
11. Prints health table + ready URLs

### Path B — Manual, step-by-step (you understand each step)

See [`docs/manual-install.md`](docs/manual-install.md) — every command broken out.

### Path C — AI agent does it for you

You ask Claude Code / Copilot CLI / Cursor to run the bootstrap. Drop this prompt into your AI coding agent:

> Read `https://github.com/neo-banker/neobanker-dev-env/blob/main/docs/ai-agent-prompt.md` and follow it to set up my local Neobanker dev environment.

The prompt file is a compact, agent-friendly recipe. See [`docs/ai-agent-prompt.md`](docs/ai-agent-prompt.md).

---

## Common ops

```bash
bash scripts/bootstrap.sh                  # full
bash scripts/bootstrap.sh --infra-only     # only docker compose, no apps
bash scripts/bootstrap.sh --skip-clone     # repos/ already populated
bash scripts/bootstrap.sh --branch chatbot # clone the chatbot branch instead

bash scripts/teardown.sh                   # stop apps + containers (preserve data volumes)
bash scripts/teardown.sh -v                # also drop volumes (fresh DB next time)
```

---

## Documentation map

| Doc | Read when |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | You want to understand container topology, ports, container-to-container networking |
| [`docs/database-schema.md`](docs/database-schema.md) / [`.html`](docs/database-schema.html) | You need to know which of the 65 MySQL tables actually have data (it's 11) and how they relate |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | Something broke during bootstrap — 16 known issues + fixes |
| [`docs/verification-handbook.md`](docs/verification-handbook.md) | You want to verify everything is working at L1 (process) / L2 (DB) / L3 (frontend ↔ backend) / L4 (ES search) |
| [`docs/ai-agent-prompt.md`](docs/ai-agent-prompt.md) | You want an AI coding agent to run the bootstrap for you |
| [`docs/manual-install.md`](docs/manual-install.md) | You want to understand each step or have a problem with the one-shot script |

---

## Repo layout

```
neobanker-dev-env/
├── docker/
│   └── docker-compose.yml        # MySQL/Redis/ES + optional MinIO/phpMyAdmin
├── scripts/
│   ├── bootstrap.sh              # one-shot setup
│   └── teardown.sh               # stop everything
├── templates/env/                # .env templates copied to each cloned repo
│   ├── frontend.env.example
│   ├── backend.env.example
│   ├── agent.env.example
│   └── dependencies.env.example
├── docs/                         # all documentation
├── repos/                        # cloned service repos (gitignored)
└── .runtime/                     # logs + pids (gitignored)
```

---

## Limitations

- **Clerk auth** in dev uses a CI placeholder publishable key — sign-in won't work. Anonymous browse OK. Replace with your own dev key from https://dashboard.clerk.com.
- **Some 3rd-party CDN logos** (Wikimedia, etc.) may 404 if URL changes upstream — see `docs/troubleshooting.md` §9.
- **`/es/*` reindex endpoints** require a JWT — bootstrap bypasses by writing to ES directly.
- **macOS / Windows** full bootstrap (with backend running) is **manual** — CI only verifies infra-only on those.

---

## Contributing

Fork → branch → PR. Add `[full-ci]` to the PR title to trigger the full Linux bootstrap CI job.

---

## License

Internal — Neobanker team only.
