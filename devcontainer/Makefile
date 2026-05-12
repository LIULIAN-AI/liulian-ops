# LIULIAN dev workspace Makefile.
# All commands run from liulian-ops/devcontainer/.
#
# Quick reference:
#   make help          show this menu
#   make dev           start workspace + postgres + redis + minio
#   make dev-full      also start ollama + prometheus + grafana
#   make stop          stop everything, keep volumes
#   make destroy       stop + delete volumes (nuclear)
#   make shell         drop into workspace shell
#   make logs          tail all services
#   make api / agent / web / ingest  start a service inside the workspace
#   make health        ping all 4 service /healthz endpoints
#   make seed          load demo data into postgres + minio

SHELL := /bin/bash
COMPOSE := docker compose -f docker-compose.dev.yml
EXEC := $(COMPOSE) exec -u liulian workspace bash -lc

# pass host UID/GID so file ownership matches
export USER_UID := $(shell id -u)
export USER_GID := $(shell id -g)

# tint helpers
GREEN := \033[32m
RED   := \033[31m
DIM   := \033[2m
RESET := \033[0m

.DEFAULT_GOAL := help

## help: show this menu
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed -E 's/^## //' | column -t -s ':'

## dev: bring up workspace + core services (postgres, redis, minio)
dev:
	@printf "$(GREEN)→ Building workspace image (first run only)…$(RESET)\n"
	$(COMPOSE) build workspace
	@printf "$(GREEN)→ Starting core services…$(RESET)\n"
	$(COMPOSE) up -d postgres redis minio
	@printf "$(GREEN)→ Starting workspace…$(RESET)\n"
	$(COMPOSE) up -d workspace
	@printf "$(GREEN)→ Ready.$(RESET) Shell in with $(DIM)make shell$(RESET).\n"
	@$(MAKE) status

## dev-full: also start ollama + prometheus + grafana
dev-full:
	$(COMPOSE) --profile llm --profile obs up -d
	@$(MAKE) status

## status: list running services + ports
status:
	@printf "\n$(GREEN)Services:$(RESET)\n"
	@$(COMPOSE) ps
	@printf "\n$(GREEN)Host port map:$(RESET)\n"
	@printf "  $(DIM)8000$(RESET) → liulian-api          (FastAPI Swagger at /api/docs)\n"
	@printf "  $(DIM)8001$(RESET) → liulian-agent        (FastAPI /health + SSE /agent/chat)\n"
	@printf "  $(DIM)8002$(RESET) → liulian-ingest       (FastAPI)\n"
	@printf "  $(DIM)3000$(RESET) → liulian-web          (Next.js dev)\n"
	@printf "  $(DIM)8081$(RESET) → liulian-mobile       (Expo Metro)\n"
	@printf "  $(DIM)6006$(RESET) → Storybook\n"
	@printf "  $(DIM)8080$(RESET) → MkDocs (liulian-python /docs)\n"
	@printf "  $(DIM)5432$(RESET) → Postgres\n"
	@printf "  $(DIM)6379$(RESET) → Redis\n"
	@printf "  $(DIM)9000$(RESET) → MinIO S3 API\n"
	@printf "  $(DIM)9001$(RESET) → MinIO console (browser)\n"

## stop: stop services but keep volumes
stop:
	$(COMPOSE) down

## destroy: stop services AND delete volumes (nuclear, requires confirm)
destroy:
	@read -p "Destroy all liulian dev volumes? [y/N] " ans; \
	if [ "$$ans" = "y" ] || [ "$$ans" = "Y" ]; then \
		$(COMPOSE) down -v; \
	else \
		echo "aborted."; \
	fi

## shell: drop into the workspace container as user 'liulian'
shell:
	$(COMPOSE) exec -u liulian workspace bash -l

## logs: tail logs from all services
logs:
	$(COMPOSE) logs -f --tail=100

## install: install all repo deps inside the workspace (one-shot)
install:
	$(EXEC) "cd /workspace/liulian-api && uv venv .venv --python 3.11 && uv pip install --python .venv/bin/python -e '.[dev]'"
	$(EXEC) "cd /workspace/liulian-agent && uv venv .venv --python 3.11 && uv pip install --python .venv/bin/python httpx fastapi 'uvicorn[standard]' pydantic pyyaml pytest httpx"
	$(EXEC) "cd /workspace/liulian-ingest && uv venv .venv --python 3.11 && uv pip install --python .venv/bin/python -e '.[dev]' || true"
	$(EXEC) "cd /workspace/liulian-web && pnpm install --frozen-lockfile || pnpm install"
	$(EXEC) "cd /workspace/liulian-design-system && npm install || true && node scripts/build.mjs"
	@printf "$(GREEN)→ All repo deps installed.$(RESET)\n"

## api: start liulian-api inside the workspace (port 8000 mapped to host)
api:
	$(COMPOSE) exec -u liulian -d workspace bash -lc \
	  "cd /workspace/liulian-api && .venv/bin/python -m uvicorn liulian_api.main:app --host 0.0.0.0 --port 8000 --reload"
	@printf "$(GREEN)→ liulian-api at http://localhost:8000/api/docs$(RESET)\n"

## agent: start liulian-agent inside the workspace (port 8001 mapped)
agent:
	$(COMPOSE) exec -u liulian -d workspace bash -lc \
	  "cd /workspace/liulian-agent && .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8001 --reload"
	@printf "$(GREEN)→ liulian-agent at http://localhost:8001$(RESET)\n"

## web: start liulian-web Next.js dev server (port 3000 mapped)
web:
	$(COMPOSE) exec -u liulian -d workspace bash -lc \
	  "cd /workspace/liulian-web && pnpm dev --hostname 0.0.0.0 --port 3000"
	@printf "$(GREEN)→ liulian-web at http://localhost:3000$(RESET)\n"

## all: install + start api + agent + web in one go
all: install api agent web health

## health: ping every service's /healthz
health:
	@printf "$(GREEN)→ Pinging services…$(RESET)\n"
	@for url in http://localhost:8000/healthz http://localhost:8001/health http://localhost:3000; do \
		printf "  %-40s " "$$url"; \
		curl -s -o /dev/null -w "%{http_code}\n" --max-time 3 "$$url" || echo "fail"; \
	done

## seed: load SwissRiver demo manifest + a few stations into postgres + minio
seed:
	$(EXEC) "cd /workspace/liulian-api && .venv/bin/python -c 'print(\"TODO: seeding script lands on Day 3 sprint commit\")'"

.PHONY: help dev dev-full status stop destroy shell logs install api agent web all health seed
