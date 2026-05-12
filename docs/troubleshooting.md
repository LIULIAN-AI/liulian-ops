# Troubleshooting Matrix

Use this matrix to debug SSH, LLM connectivity, process state, and service health.
Before running commands, export values from your `config.yaml` (replace placeholders with your real config):

```bash
export APP_HOST="<servers.app.host>"
export APP_SSH_PORT="<servers.app.ssh_port>"
export APP_USER="<servers.app.user>"
export APP_KEY_PATH="<servers.app.key_path>"

export GPU_HOST="<servers.gpu.host>"
export GPU_SSH_PORT="<servers.gpu.ssh_port>"
export GPU_USER="<servers.gpu.user>"
export GPU_KEY_PATH="<servers.gpu.key_path>"
export OLLAMA_PORT="<servers.gpu.ollama_port>"
export VLLM_PORT="<servers.gpu.vllm_port>"
```

| Command | Symptom | Interpretation | Fix |
|---|---|---|---|
| `uv run neoctl doctor` | One or more `FAIL` rows | Service or connectivity is down | Follow row details; run targeted checks below |
| `ssh -p "$APP_SSH_PORT" -i "$APP_KEY_PATH" "$APP_USER@$APP_HOST" 'echo neoctl-ssh-ok'` | SSH timeout/refused | App server SSH/network/firewall issue | Validate `servers.app.{host,ssh_port,user,key_path}`; open SSH port; test from same network |
| `ssh -p "$GPU_SSH_PORT" -i "$GPU_KEY_PATH" "$GPU_USER@$GPU_HOST" 'echo neoctl-ssh-ok'` | SSH timeout/refused | GPU server unreachable | Validate `servers.gpu.{host,ssh_port,user,key_path}`; fix routing/firewall; ensure sshd running |
| `curl -sS --connect-timeout 3 "http://$GPU_HOST:$OLLAMA_PORT/api/tags"` | No response/non-200 | Ollama direct TCP unavailable | Start Ollama, open `servers.gpu.ollama_port`/tcp, verify bind to `0.0.0.0` |
| `curl -sS --connect-timeout 3 "http://$GPU_HOST:$VLLM_PORT/v1/models"` | No response/non-200 | vLLM direct TCP unavailable | Start vLLM, open `servers.gpu.vllm_port`/tcp, verify process listening |
| `curl -sS --connect-timeout 3 "http://localhost:$OLLAMA_PORT/api/tags"` | Fails locally with tunnel mode | Forward/reverse tunnel missing/broken | Recreate tunnel command; verify SSH key and remote port |
| `ps aux \| grep -E 'ollama serve\|vllm serve' \| grep -v grep` | No matching process | LLM server not running | Start `ollama serve` or `vllm serve`; check startup logs |
| `ss -ltnp \| grep -E ":${OLLAMA_PORT}\|:${VLLM_PORT}"` | No listener on expected port | LLM not bound to port/interface | Restart with correct host/port flags; inspect command args |
| `ps aux \| grep -E "ssh .* -L .*${OLLAMA_PORT}\|autossh .* -L .*${OLLAMA_PORT}" \| grep -v grep` | No tunnel process | Forward tunnel not active | Re-run forward tunnel command with `-f -N -L` |
| `sudo systemctl status <backend-systemd-service> --no-pager` | `inactive`/`failed` | Backend service failed restart | Check journal logs, rebuild backend, then restart (default service is `neobanker-backend` unless overridden) |
| `pm2 status` | frontend process missing/offline | Frontend process not running | Restart your configured PM2 app name (default: `neobanker-frontend-app`) and run `pm2 save` |
| `sudo systemctl status <agent-systemd-service> --no-pager` | `inactive`/`failed` | Agent service failed restart | `cd ~/neobanker/agent && uv sync && uv run pytest -q`, then restart (default service is `neobanker-agent` unless overridden) |

## Useful Follow-up Commands

```bash
# backend
curl -i http://localhost:8080/homepage/hot-search-words

# frontend
curl -i http://localhost:3000/homepage

# agent
curl -i http://localhost:8000/health

# ollama local check on GPU
curl -s --connect-timeout 5 "http://localhost:$OLLAMA_PORT/api/tags"

# vllm local check on GPU
curl -s --connect-timeout 5 "http://localhost:$VLLM_PORT/v1/models"
```
