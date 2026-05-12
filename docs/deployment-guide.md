# Automated Deployment Guide (`neoctl`)

This is the fully automated operator flow using the implemented CLI commands.

## 1) Local Prerequisites

From `neoctl/` on your operator machine:

```bash
uv sync
cp config.yaml.example config.yaml
```

Required local tools used by neoctl/runtime: `ssh`, `scp`, `curl`, `uv`, `git` (and optionally `autossh`).

Canonical command contract (source of truth for CLI signatures):
- [Commands reference → Canonical command contract](commands-reference.md#canonical-command-contract)

## 2) Configure `config.yaml`

Set:
- app SSH: `servers.app.host`, `ssh_port`, `user`, `key_path`, `deploy_path`
- GPU SSH: `servers.gpu.host`, `ssh_port`, `user`, `key_path`
- LLM ports: `servers.gpu.ollama_port` (default `37434`), `servers.gpu.vllm_port` (default `38000`)
- backend choice: `llm.local.backend` (`ollama` or `vllm`)
- repo subdirs under `deploy.services.*.dir` if non-default

`deploy backend/frontend/agent` expects checked-out repos at:
- `~/neobanker/backend`
- `~/neobanker/frontend`
- `~/neobanker/agent`

(or your configured `deploy_path` + service dirs).

## 3) Validate CLI + Dry-Run

```bash
uv run neoctl --help
uv run neoctl deploy --help
uv run neoctl deploy all --dry-run
```

For the complete command matrix (including staged dry-runs), use the
[canonical command contract](commands-reference.md#canonical-command-contract).

## 4) Automated Live Deployment

### Option A — full orchestration

```bash
uv run neoctl deploy all
```

Execution order:
1. `deploy llm`
2. `deploy backend`
3. `deploy frontend`
4. `deploy agent`

### Option B — staged rollout

```bash
uv run neoctl deploy llm
uv run neoctl deploy backend --branch main
uv run neoctl deploy frontend --branch main
uv run neoctl deploy agent --branch main
```

## 5) Connectivity Behavior (implemented)

`deploy llm` uses this policy:

1. **Direct TCP first**  
   - `http://<gpu_host>:<servers.gpu.ollama_port>/api/tags` for Ollama (default `37434`)  
   - `http://<gpu_host>:<servers.gpu.vllm_port>/v1/models` for vLLM (default `38000`)
2. **Forward tunnel fallback**  
   Creates local `ssh -L`/`autossh -L` tunnel and probes localhost.
3. **Reverse tunnel manual-only**  
   CLI prints a hint command; it does not auto-create reverse tunnels.

## 6) Post-Deploy Verification

```bash
uv run neoctl doctor
```

Expected:
- Backend: `http://localhost:8080/homepage/hot-search-words`
- Frontend: `http://localhost:3000/homepage`
- Agent: `http://localhost:8000/health`
- LLM connectivity reported as direct or forward tunnel

## 7) Targeted Redeploy Commands

```bash
uv run neoctl deploy llm
uv run neoctl deploy backend --branch main --service-name neobanker-backend
uv run neoctl deploy frontend --branch main --pm2-name neobanker-frontend-app
uv run neoctl deploy agent --branch main --service-name neobanker-agent
```
