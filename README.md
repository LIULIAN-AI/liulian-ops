# neoctl

`neoctl` is the Neobanker deployment CLI for:
- LLM connectivity/bootstrap (`deploy llm`)
- app service deploys (`deploy backend|frontend|agent`)
- full orchestration (`deploy all`)
- health checks (`doctor`)

## Quickstart

Run all commands from this directory (`neoctl/`).

```bash
uv sync
cp config.yaml.example config.yaml
```

Edit `config.yaml` and set at least:
- `servers.app.{host,ssh_port,user,key_path,deploy_path}`
- `servers.gpu.{host,ssh_port,user,key_path,ollama_port,vllm_port,model_storage}`
- `llm.local.backend` (`ollama` or `vllm`)

Canonical command contract (single source of truth):
- [Commands reference → Canonical command contract](docs/commands-reference.md#canonical-command-contract)

Basic CLI sanity checks:

```bash
uv run neoctl --help
uv run neoctl deploy --help
```

For the complete dry-run/live command matrix, use:
- [Commands reference → Canonical command contract](docs/commands-reference.md#canonical-command-contract)
- [Automated deployment guide](docs/deployment-guide.md)

Typical end-to-end run:

```bash
uv run neoctl deploy all --dry-run
uv run neoctl deploy all
uv run neoctl doctor
```

Connectivity policy (implemented):
1. Direct TCP first (`GPU:<servers.gpu.ollama_port>` for Ollama, `GPU:<servers.gpu.vllm_port>` for vLLM; defaults: `37434` and `38000`)
2. Forward SSH tunnel fallback
3. Reverse tunnel is manual-only (documented, not auto-created)

## Documentation

- [Docs index](docs/index.md)
- [Automated deployment guide](docs/deployment-guide.md)
- [Manual deployment guide](docs/manual-deployment.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Architecture](docs/architecture.md)
- [Commands reference](docs/commands-reference.md)
