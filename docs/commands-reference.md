# Commands Reference

All commands are run from `neoctl/` with `uv run neoctl ...`.

## Canonical command contract

This section is the single source of truth for neoctl command signatures across operator docs.
`README.md` and `docs/deployment-guide.md` should link here instead of duplicating full command lists.

```bash
uv run neoctl --help
uv run neoctl --version
uv run neoctl doctor
uv run neoctl deploy --help
uv run neoctl deploy llm [--dry-run]
uv run neoctl deploy backend [--branch TEXT] [--service-name TEXT] [--dry-run]
uv run neoctl deploy frontend [--branch TEXT] [--pm2-name TEXT] [--dry-run]
uv run neoctl deploy agent [--branch TEXT] [--service-name TEXT] [--dry-run]
uv run neoctl deploy all [--dry-run]
```

## Root

```bash
uv run neoctl --help
uv run neoctl --version
```

## `doctor`

```bash
uv run neoctl doctor
```

Runs HTTP/service checks for backend, frontend, agent, plus LLM connectivity/tunnel/cloud-key status.

## `deploy` group

```bash
uv run neoctl deploy --help
```

### `deploy llm`

```bash
uv run neoctl deploy llm [--dry-run]
```

Options:
- `--dry-run` assemble actions only

### `deploy backend`

```bash
uv run neoctl deploy backend [--branch TEXT] [--service-name TEXT] [--dry-run]
```

Defaults:
- `--branch main`
- `--service-name` uses `deploy.services.backend.service_name` or `liulian-api`

### `deploy frontend`

```bash
uv run neoctl deploy frontend [--branch TEXT] [--pm2-name TEXT] [--dry-run]
```

Defaults:
- `--branch main`
- `--pm2-name` uses `deploy.services.frontend.pm2_name` or `liulian-web-app`

### `deploy agent`

```bash
uv run neoctl deploy agent [--branch TEXT] [--service-name TEXT] [--dry-run]
```

Defaults:
- `--branch main`
- `--service-name` uses `deploy.services.agent.service_name` or `liulian-agent`

### `deploy all`

```bash
uv run neoctl deploy all [--dry-run]
```

Runs `llm -> backend -> frontend -> agent` in order.

## Practical Examples

```bash
uv run neoctl deploy llm
uv run neoctl deploy backend --branch main
uv run neoctl deploy frontend --pm2-name liulian-web-app
uv run neoctl deploy agent --service-name liulian-agent
uv run neoctl deploy all
```
