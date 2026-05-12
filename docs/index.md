# neoctl Docs

This docs set is aligned to the current CLI implementation (`doctor`, `deploy llm/backend/frontend/agent/all`).

## Start Here

1. [README quickstart](../README.md)
2. [Deployment guide (automated)](deployment-guide.md)
3. [Manual deployment guide](manual-deployment.md)
4. [Troubleshooting matrix](troubleshooting.md)
5. [Architecture](architecture.md)
6. [Commands reference](commands-reference.md)

## Connectivity Policy

- **Primary:** direct TCP to GPU LLM (`37434` for Ollama, `38000` for vLLM)
- **Fallback:** forward SSH tunnel to localhost
- **Last resort:** reverse tunnel, **manual-only**
