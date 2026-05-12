# AI Agent Bootstrap Prompt

Drop this prompt into your AI coding agent (Claude Code, Cursor, Copilot CLI, Aider, Cline, etc.) to set up the full Neobanker dev stack.

---

## How to use

### Option A — One-liner

In your AI agent (Claude Code, Cursor chat, etc.) say:

> Read https://github.com/neo-banker/neobanker-dev-env/blob/main/docs/ai-agent-prompt.md and execute the bootstrap procedure for me. Stop and ask before any destructive action.

### Option B — Paste the prompt directly

Copy everything in the **PROMPT** section below and paste it into your agent.

---

## PROMPT

```
You are setting up the Neobanker local development environment on my machine.
You have shell + file-edit access. Follow this exact procedure.

# Goal
Bootstrap a fully working local stack: frontend (:3000) + backend (:8080) +
agent (:8000) + MySQL (:3307) + Redis (:6379) + Elasticsearch (:9200), with
~38k rows of demo data pre-loaded.

# Pre-flight (verify, don't install — ask user if missing)
Verify these tools exist on PATH. If any is missing, STOP and ask the user
to install before proceeding. Do NOT auto-install.
- docker (with `docker compose` plugin)
- git
- java 17
- node 20
- python 3.12
- uv (https://astral.sh/uv)

# Detect platform
Set DOCKER_GATEWAY based on `uname -s`:
- Linux  → 172.17.0.1
- Darwin → host.docker.internal
- Windows (MINGW/MSYS/CYGWIN) → host.docker.internal

# Procedure

1. Clone the dev-env repo (if not already in it):
     git clone git@github.com:neo-banker/neobanker-dev-env.git
     cd neobanker-dev-env

2. Run the one-shot bootstrap:
     bash scripts/bootstrap.sh

   This will:
   a. Clone 3 service repos into ./repos/
   b. `docker compose up` for MySQL/Redis/Elasticsearch
   c. Patch .env files from templates/env/
   d. `npm install` + `uv sync` (first run only)
   e. Start Spring Boot backend (60-120s)
   f. Import CSV data
   g. Bulk-load companies into ES + seed search_logs
   h. Start agent + frontend

3. Verify (open these URLs in user's browser, ask them to confirm each):
   - http://localhost:3000/homepage         → must show 4 hot-search chips +
                                                Recent News + 8 Popular Banks
   - http://localhost:8080/actuator/health  → all 5 components UP
                                                (db, redis, elasticsearch, ping, diskSpace)
   - http://localhost:9200/_cat/indices     → 'companies' index with 575 docs

4. Report back:
   - All ports listening?
   - Any service DOWN in /actuator/health?
   - Browser screenshot if possible

# Things to NOT do (safety)
- Do NOT modify any file under repos/ — those are upstream services
- Do NOT install missing tools without asking
- Do NOT push to git
- Do NOT delete .runtime/ logs (user may want to debug)
- Do NOT change application.properties — use env vars (already in bootstrap.sh)

# If something fails
Read docs/troubleshooting.md — it has 16 known issues with fixes. Match the
symptom and apply the fix. If it's a new failure mode, STOP and report.

# Common known fixes (from troubleshooting.md)
- "Connection refused 9200" from backend → use DOCKER_GATEWAY env var, not localhost
- "Missing X-Elastic-Product header" → ES version too old, use 7.17+
- "Access denied for user 'root'@'…'" → run the GRANT in step 3 of manual-install.md
- "Cannot read properties of null (reading 'owners')" → frontend null-check bug, ignore
- Maven plugin resolution failure with DNS error → fix container DNS:
    echo 'nameserver 8.8.8.8' > /etc/resolv.conf

# When done
Print:
  ✅ Frontend  http://localhost:3000/homepage
  ✅ Backend   http://localhost:8080/actuator/health
  ✅ Agent     http://localhost:8000/docs
  ✅ phpMyAdmin: docker compose -f docker/docker-compose.yml --profile full up -d phpmyadmin → http://localhost:8088 (root/root)

Then stop and wait for the user.
```

---

## Tested with

- ✅ Claude Code (Opus 4.7) — recommended, handles long-running processes well
- ✅ Cursor agent (Sonnet)
- ⚠️ Copilot CLI — works but no real-time output streaming
- ⚠️ Aider — works for the file edits, but you must run shell commands manually

## Tips for AI agents

- **Run `bootstrap.sh` in the foreground** so the agent can see logs. If your agent only supports background bash, tail `.runtime/logs/backend.log` separately.
- **Maven downloads ~300 MB on first run** — don't kill the process if it's "stuck", check the log.
- **Browser verification needs a browser MCP** (Playwright/Chrome DevTools) or screenshot tool. Without one, ask the user to verify URLs visually.
- **Token cost**: full bootstrap is ~1-2 min of agent supervision. Most time is shell waits.
