#!/usr/bin/env bash
# Neobanker DEV bootstrap — local-machine mode (Linux / macOS / Windows-WSL2)
#
# 1. Pre-flight: docker, java 17, node 20, python 3.12, uv, git
# 2. Clone missing repos into ./repos/
# 3. Bring up infra (MySQL/Redis/ES) via docker compose
# 4. Patch .env files from templates/
# 5. Start backend (mvnw) → :8080  (60-120s on first run)
# 6. Import CSV data from backend/import_data/
# 7. Bulk-load ES + seed search_logs
# 8. Start agent (uv run uvicorn) + frontend (npm run dev)
# 9. Final health report
#
# Idempotent. Re-running skips already-done work.
#
# Usage:
#   bash scripts/bootstrap.sh                  # full
#   bash scripts/bootstrap.sh --infra-only     # only docker compose
#   bash scripts/bootstrap.sh --skip-clone     # repos/ already populated
#   bash scripts/bootstrap.sh --branch chatbot # use chatbot branch

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPOS_DIR="$ROOT_DIR/repos"
LOG_DIR="${LOG_DIR:-$ROOT_DIR/.runtime/logs}"
mkdir -p "$LOG_DIR"

GIT_ORG="${GIT_ORG:-neo-banker}"
GIT_BRANCH="${GIT_BRANCH:-main}"
INFRA_ONLY=0
SKIP_CLONE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --infra-only)  INFRA_ONLY=1; shift ;;
    --skip-clone)  SKIP_CLONE=1; shift ;;
    --branch)      GIT_BRANCH="$2"; shift 2 ;;
    --org)         GIT_ORG="$2"; shift 2 ;;
    -h|--help)     sed -n '3,21p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

# Linux: 172.17.0.1 (docker0). Mac/Windows: host.docker.internal
DOCKER_GATEWAY="${DOCKER_GATEWAY:-}"
if [[ -z "$DOCKER_GATEWAY" ]]; then
  case "$(uname -s)" in
    Linux*)   DOCKER_GATEWAY="172.17.0.1" ;;
    Darwin*|MINGW*|MSYS*|CYGWIN*) DOCKER_GATEWAY="host.docker.internal" ;;
    *)        DOCKER_GATEWAY="172.17.0.1" ;;
  esac
fi

C_GREEN='\033[0;32m'; C_YELLOW='\033[0;33m'; C_RED='\033[0;31m'; C_RESET='\033[0m'
info()  { echo -e "${C_GREEN}[+]${C_RESET} $*"; }
warn()  { echo -e "${C_YELLOW}[!]${C_RESET} $*"; }
fail()  { echo -e "${C_RED}[x]${C_RESET} $*" >&2; exit 1; }

# ─── 1. Pre-flight ──────────────────────────────────────────────────────
info "Pre-flight checks…"
command -v docker >/dev/null || fail "docker not installed"
docker compose version >/dev/null 2>&1 || command -v docker-compose >/dev/null \
  || fail "docker compose plugin not installed"
docker info >/dev/null 2>&1 || fail "docker daemon not running"
DC="docker compose"; docker compose version >/dev/null 2>&1 || DC="docker-compose"

if (( INFRA_ONLY == 0 )); then
  command -v git     >/dev/null || fail "git not installed"
  command -v java    >/dev/null || warn "java not installed — backend won't run"
  command -v node    >/dev/null || warn "node not installed — frontend won't run"
  command -v python3 >/dev/null || fail "python3 needed for ES bulk load"
  command -v uv      >/dev/null || warn "uv not installed — agent won't run. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
fi

# ─── 2. Clone repos ─────────────────────────────────────────────────────
mkdir -p "$REPOS_DIR"
clone_or_update() {
  local name="$1" dest="$REPOS_DIR/$name"
  if [[ -d "$dest/.git" ]]; then
    info "$name: already cloned (skip)"
  elif (( SKIP_CLONE == 1 )); then
    fail "$name: --skip-clone set but $dest doesn't exist"
  else
    info "$name: cloning…"
    git clone --branch "$GIT_BRANCH" --single-branch \
      "git@github.com:$GIT_ORG/$name.git" "$dest" 2>/dev/null \
      || git clone --branch "$GIT_BRANCH" --single-branch \
         "https://github.com/$GIT_ORG/$name.git" "$dest" \
      || fail "Failed to clone $name. Check your SSH key or use --org."
  fi
}

if (( INFRA_ONLY == 0 )); then
  clone_or_update neobanker-frontend-MVP-V3
  clone_or_update neobanker-backend-MVP-V2
  clone_or_update neobanker-agent
fi

FRONTEND_DIR="$REPOS_DIR/neobanker-frontend-MVP-V3"
BACKEND_DIR="$REPOS_DIR/neobanker-backend-MVP-V2"
AGENT_DIR="$REPOS_DIR/neobanker-agent"

# ─── 3. Infra services up ───────────────────────────────────────────────
info "Starting infra (MySQL, Redis, Elasticsearch)…"
$DC -f "$ROOT_DIR/docker/docker-compose.yml" up -d mysql redis elasticsearch

info "Waiting for MySQL…"
until docker exec neobanker-mysql mysqladmin ping -uroot -proot --silent 2>/dev/null; do sleep 2; done
info "Waiting for Redis…"
until docker exec neobanker-redis redis-cli ping 2>/dev/null | grep -q PONG; do sleep 2; done
info "Waiting for Elasticsearch…"
until curl -sS --max-time 2 http://127.0.0.1:9200/_cluster/health 2>/dev/null \
  | grep -qE '"status":"(green|yellow)"'; do sleep 3; done
info "ES version: $(curl -sS http://127.0.0.1:9200/ | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"]["number"])')"

# Grant root@'%' so phpMyAdmin / external clients can connect
docker exec neobanker-mysql mysql -uroot -proot -e "
  CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED WITH mysql_native_password BY 'root';
  GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;
  FLUSH PRIVILEGES;" 2>/dev/null || true

if (( INFRA_ONLY == 1 )); then
  info "Infra-only mode — done. Apps not started."
  exit 0
fi

# ─── 4. Patch .env files (don't overwrite user values) ──────────────────
info "Patching .env files…"
copy_env() {
  local example="$1" dest="$2"
  if [[ ! -f "$dest" ]]; then
    cp "$example" "$dest"; info "  created $(basename "$dest")"
  else
    while IFS= read -r line; do
      [[ "$line" =~ ^[[:space:]]*# ]] && continue
      [[ -z "$line" ]] && continue
      local key="${line%%=*}"
      grep -q "^${key}=" "$dest" 2>/dev/null || echo "$line" >> "$dest"
    done < "$example"
  fi
}
copy_env "$ROOT_DIR/templates/env/frontend.env.example" "$FRONTEND_DIR/.env.local"
copy_env "$ROOT_DIR/templates/env/agent.env.example"    "$AGENT_DIR/.env"

# ─── 5. Install deps (idempotent) ───────────────────────────────────────
if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  info "First-run: npm install in frontend (a few minutes)…"
  (cd "$FRONTEND_DIR" && npm install)
fi
if [[ ! -d "$AGENT_DIR/.venv" ]] && command -v uv >/dev/null; then
  info "First-run: uv sync in agent…"
  (cd "$AGENT_DIR" && uv sync --all-extras)
fi

# ─── 6. Start backend ───────────────────────────────────────────────────
info "Starting Spring Boot backend on :8080 (logs: $LOG_DIR/backend.log)…"
(
  cd "$BACKEND_DIR"
  chmod +x mvnw
  export JAVA_HOME="${JAVA_HOME:-$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")}"
  export ELASTICSEARCH_HOST="$DOCKER_GATEWAY"
  export SPRING_ELASTICSEARCH_REST_URIS="$DOCKER_GATEWAY:9200"
  export SPRING_DATA_REDIS_HOST="$DOCKER_GATEWAY"
  export SPRING_DATA_REDIS_PORT=6379
  nohup ./mvnw spring-boot:run -DskipTests > "$LOG_DIR/backend.log" 2>&1 &
)
info "Waiting for backend (60-120s on first run while Maven downloads)…"
until curl -sS --max-time 2 http://127.0.0.1:8080/actuator/health 2>/dev/null \
  | grep -q '"db":{"status":"UP"'; do sleep 5; done
info "Backend ready."

# ─── 7. Import CSV data (idempotent) ────────────────────────────────────
COMPANY_COUNT=$(docker exec neobanker-mysql mysql -uroot -proot neobanker -N \
  -e "SELECT COUNT(*) FROM company;" 2>/dev/null || echo 0)
if [ "${COMPANY_COUNT:-0}" -gt 100 ]; then
  info "Data already imported ($COMPANY_COUNT companies). Skipping CSV import."
else
  info "Importing CSV data (~30s)…"
  python3 -c "import pymysql" 2>/dev/null || \
    pip3 install --quiet pymysql 2>/dev/null || \
    pip3 install --quiet pymysql --break-system-packages 2>/dev/null || true
  (
    cd "$BACKEND_DIR/import_data/import_scripts"
    for entry in \
      "import_company.py:../csv_data/company575_已查重.csv" \
      "import_news.py:../csv_data/news_2025-03-31.csv" \
      "import_company_product.py:../csv_data/product - Sheet1_cleaned.csv" \
      "import_financials.py:../csv_data/500financials+4market.csv" \
      "import_management.py:../csv_data/management.csv" \
      "import_no_tech.py:../csv_data/employee_noTech.csv" \
      "import_shareholder.py:../csv_data/shareholder.csv" \
      "import_marketing.py:../csv_data/Marketing_cleaned.csv" \
      "import_marketing_subtables.py:../csv_data/Marketing_New_Table - Sheet1.csv"; do
      script="${entry%%:*}"; csv="${entry#*:}"
      [[ -f "../csv_data/$(basename "$csv")" ]] || { warn "skip $script (CSV missing)"; continue; }
      info "  $script"
      python3 "$script" "$csv" 2>&1 | tail -2 || warn "$script had issues"
    done
  )
fi

# ─── 8. Bulk-load ES + seed search_logs ─────────────────────────────────
info "Indexing companies → Elasticsearch…"
python3 - <<'PYEOF'
import pymysql, urllib.request, json
db = pymysql.connect(host='127.0.0.1', port=3307, user='root', password='root',
                     database='neobanker', charset='utf8mb4')
cur = db.cursor(pymysql.cursors.DictCursor)
cur.execute('''SELECT id, name, ceoname, company_sort_id, bank_code,
                      location_sort_id, logo_link, branding
               FROM company WHERE company_sort_id IS NOT NULL''')
rows = cur.fetchall()
lines = []
for r in rows:
    lines.append(json.dumps({'index': {'_index': 'companies', '_id': str(r['id'])}}))
    lines.append(json.dumps({
        'id': str(r['id']), 'name': r['name'] or '', 'CEOName': r['ceoname'] or '',
        'companySortId': r['company_sort_id'] or '', 'bankCode': r['bank_code'] or '',
        'locationSortId': r['location_sort_id'] or '', 'logoLink': r['logo_link'] or '',
        'spotlight': (r['branding'] or '')[:500], 'score': 1.0,
    }, ensure_ascii=False))
body = ('\n'.join(lines) + '\n').encode('utf-8')
req = urllib.request.Request('http://127.0.0.1:9200/_bulk', data=body,
    headers={'Content-Type': 'application/x-ndjson'}, method='POST')
r = json.loads(urllib.request.urlopen(req, timeout=30).read())
urllib.request.urlopen('http://127.0.0.1:9200/companies/_refresh').read()
print(f'  indexed {len(r["items"])} companies')
PYEOF

docker exec neobanker-mysql mysql -uroot -proot neobanker -e "
INSERT IGNORE INTO search_logs (id, created_at, updated_at, keyword, search_type, result_count, user_ip, session_id) VALUES
(1, NOW(), NOW(), 'ZA Bank', 'COMPANY', 1, '127.0.0.1', 'seed-1'),
(2, NOW(), NOW(), 'ZA Bank', 'COMPANY', 1, '127.0.0.1', 'seed-2'),
(3, NOW(), NOW(), 'ZA Bank', 'COMPANY', 1, '127.0.0.1', 'seed-3'),
(4, NOW(), NOW(), 'OakNorth Bank', 'COMPANY', 1, '127.0.0.1', 'seed-4'),
(5, NOW(), NOW(), 'OakNorth Bank', 'COMPANY', 1, '127.0.0.1', 'seed-5'),
(6, NOW(), NOW(), 'Revolut', 'COMPANY', 1, '127.0.0.1', 'seed-6'),
(7, NOW(), NOW(), 'Nubank', 'COMPANY', 1, '127.0.0.1', 'seed-7');" 2>/dev/null
info "Seeded search_logs."

# ─── 9. Start agent + frontend ─────────────────────────────────────────
listening() {
  ss -tln 2>/dev/null | grep -q ":$1\b" || \
  lsof -i ":$1" -sTCP:LISTEN 2>/dev/null | grep -q .
}

if ! listening 8000 && command -v uv >/dev/null; then
  info "Starting agent FastAPI on :8000…"
  ( cd "$AGENT_DIR" && nohup uv run uvicorn main:app --host 0.0.0.0 --port 8000 > "$LOG_DIR/agent.log" 2>&1 & )
  until curl -sS --max-time 2 http://127.0.0.1:8000/health >/dev/null 2>&1; do sleep 2; done
fi

if ! listening 3000; then
  info "Starting Next.js frontend on :3000…"
  ( cd "$FRONTEND_DIR" && nohup npm run dev > "$LOG_DIR/frontend.log" 2>&1 & )
  until curl -sI --max-time 2 http://127.0.0.1:3000/homepage 2>/dev/null \
    | grep -q "HTTP/1.1 200"; do sleep 3; done
fi

# ─── 10. Final report ───────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════════════════════════"
info "ALL SERVICES UP"
echo "════════════════════════════════════════════════════════════════"
curl -sS http://127.0.0.1:8080/actuator/health | python3 -c "
import json,sys; d=json.load(sys.stdin)
for k,v in d['components'].items():
    s=v.get('status','?'); i='✅' if s=='UP' else '❌'
    print(f'  {i} backend.{k}: {s}')"
echo
echo "  🌐 Frontend     http://localhost:3000/homepage"
echo "  🌐 Backend      http://localhost:8080/actuator/health"
echo "  🌐 Swagger      http://localhost:8080/swagger-ui.html"
echo "  🌐 Agent        http://localhost:8000/docs"
echo "  🔍 ES           http://localhost:9200/_cat/indices?v"
echo "  🗄  MySQL        localhost:3307 (root/root)"
echo "  📊 phpMyAdmin   $DC -f docker/docker-compose.yml --profile full up -d phpmyadmin"
echo "                  http://localhost:8088"
echo
echo "  Logs:  $LOG_DIR/{backend,agent,frontend}.log"
echo "  Stop:  bash scripts/teardown.sh"
echo "════════════════════════════════════════════════════════════════"
