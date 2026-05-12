# Manual Deployment Guide (App + GPU)

Use this when you need explicit server-side control instead of `neoctl deploy all`.

## Variables Used Below

Set these in your shell first:

```bash
export APP_HOST="<app-host>"
export APP_SSH_PORT="10022"
export APP_USER="<app-user>"
export APP_KEY="$HOME/.ssh/id_ed25519"

export GPU_HOST="<gpu-host>"
export GPU_SSH_PORT="10022"
export GPU_USER="<gpu-user>"
export GPU_KEY="$HOME/.ssh/id_gpu"
```

---

## A) GPU Server Manual LLM Setup

### A1. SSH to GPU server

```bash
ssh -p "$GPU_SSH_PORT" -i "$GPU_KEY" "$GPU_USER@$GPU_HOST"
```

### A2. Direct TCP policy (preferred)

Open only needed ports:

```bash
sudo ufw allow 37434/tcp
sudo ufw allow 38000/tcp
```

From app/operator machine, test direct access:

```bash
curl -sS --connect-timeout 3 "http://$GPU_HOST:37434/api/tags"
curl -sS --connect-timeout 3 "http://$GPU_HOST:38000/v1/models"
```

### A3. Ollama path (`37434`)

Run on GPU server:

```bash
which ollama || curl -fsSL https://ollama.com/install.sh | sh
OLLAMA_HOST=0.0.0.0:37434 OLLAMA_MODELS=/nfshdd/models nohup ollama serve > ~/neoctl-ollama.log 2>&1 &
curl -s --connect-timeout 5 http://localhost:37434/api/tags
ollama pull 'qwen3.5:9b'
```

### A4. vLLM path (`38000`)

Run on GPU server:

```bash
python3 -c 'import vllm' 2>/dev/null || pip install vllm
HF_HOME=/nfshdd/models nohup vllm serve 'Qwen/Qwen3.5-7B-Instruct' --host 0.0.0.0 --port 38000 > ~/neoctl-vllm.log 2>&1 &
curl -s --connect-timeout 5 http://localhost:38000/v1/models
```

---

## B) App Server Manual Service Deploy

### B1. SSH to app server

```bash
ssh -p "$APP_SSH_PORT" -i "$APP_KEY" "$APP_USER@$APP_HOST"
```

### B2. Backend deploy (`~/neobanker/backend`)

```bash
cd ~/neobanker/backend
git fetch origin
git reset --hard origin/main
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
./mvnw -B package -DskipTests --no-transfer-progress
sudo systemctl restart neobanker-backend
sleep 5
STATUS=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/homepage/hot-search-words)
[ "$STATUS" = "200" ]
```

### B3. Frontend deploy (`~/neobanker/frontend`)

```bash
cd ~/neobanker/frontend
git fetch origin
git reset --hard origin/main
npm ci --prefer-offline
npm run build
pm2 restart neobanker-frontend-app && pm2 save
curl -I http://localhost:3000/homepage
```

### B4. Agent deploy (`~/neobanker/agent`)

```bash
cd ~/neobanker/agent
git fetch origin
git reset --hard origin/main
uv sync
uv run pytest -q
sudo systemctl restart neobanker-agent
sleep 3
curl -fsS http://localhost:8000/health
```

---

## C) Tunnel Modes

### C1. Forward tunnel fallback (supported path)

Run on operator/app side (not GPU):

```bash
autossh -M 0 -f -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new -L 37434:localhost:37434 -p "$GPU_SSH_PORT" -i "$GPU_KEY" "$GPU_USER@$GPU_HOST"
```

If `autossh` is unavailable:

```bash
ssh -f -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new -L 37434:localhost:37434 -p "$GPU_SSH_PORT" -i "$GPU_KEY" "$GPU_USER@$GPU_HOST"
```

Validate:

```bash
curl -sS --connect-timeout 3 http://localhost:37434/api/tags
```

Use `38000` similarly for vLLM.

### C2. Reverse tunnel (manual-only, not auto by neoctl)

Run this on GPU server:

> If the app SSH private key is not available on GPU, remove `-i "$APP_KEY"` and use the default SSH identity on GPU.

```bash
autossh -M 0 -f -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -R 37434:localhost:37434 -p "$APP_SSH_PORT" -i "$APP_KEY" "$APP_USER@$APP_HOST"
```

If `autossh` is unavailable:

```bash
ssh -f -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -R 37434:localhost:37434 -p "$APP_SSH_PORT" -i "$APP_KEY" "$APP_USER@$APP_HOST"
```

Validate on app server:

```bash
curl -sS --connect-timeout 3 http://localhost:37434/api/tags
```

> Reverse tunnel is kept as last-resort/manual because it is intentionally not auto-established by CLI.
