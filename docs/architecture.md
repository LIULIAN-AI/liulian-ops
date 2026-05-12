# neoctl Deployment Architecture

## Topology

```text
Operator machine (neoctl)
  ├─ SSH to App server (backend/frontend/agent deploy)
  ├─ SSH to GPU server (LLM install/start)
  └─ LLM connectivity probe:
       1) direct TCP to GPU
       2) forward tunnel localhost fallback
       3) reverse tunnel (manual-only)
```

## Nodes and Responsibilities

- **Operator machine**: runs `uv run neoctl ...`
- **App server**:
  - Backend (`systemd`, port `8080`)
  - Frontend (`pm2`, port `3000`)
  - Agent (`systemd`, port `8000`)
- **GPU server**:
  - Ollama (`37434`) or vLLM (`38000`)

## Connectivity Policy (Implemented)

1. **Direct TCP first**
   - Ollama probe: `GET /api/tags`
   - vLLM probe: `GET /v1/models`
2. **Forward tunnel fallback**
   - `ssh/autossh -L <local_port>:localhost:<llm_port>`
3. **Reverse tunnel manual-only**
   - `ssh/autossh -R <port>:localhost:<port>`
   - CLI only prints helper command

## Deploy Orchestration

`deploy all` runs:
1. `deploy llm`
2. `deploy backend`
3. `deploy frontend`
4. `deploy agent`

If any stage fails, execution stops and returns a CLI error.

## Runtime Command Model

- Backend deploy: `git fetch` → `git reset --hard origin/<branch>` → Maven build → systemd restart → health check
- Frontend deploy: `git fetch` → `git reset --hard origin/<branch>` → `npm ci` → `npm run build` → `pm2 restart`
- Agent deploy: `git fetch` → `git reset --hard origin/<branch>` → `uv sync` → `uv run pytest -q` → systemd restart + health check
