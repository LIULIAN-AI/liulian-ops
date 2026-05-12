# Manual install — step by step

If you don't want the one-shot `bootstrap.sh`, here's every command spelled out.
Useful when something fails and you need to rerun just one piece.

---

## 0. Prerequisites

```bash
# Verify (replace with your platform's installer if missing)
docker --version           # 20+
docker compose version     # plugin
git --version
java -version              # 17
node --version             # 20
python3 --version          # 3.12
uv --version               # any recent
```

---

## 1. Clone repos

```bash
mkdir -p repos
cd repos
git clone git@github.com:neo-banker/neobanker-frontend-MVP-V3.git
git clone git@github.com:neo-banker/neobanker-backend-MVP-V2.git
git clone git@github.com:neo-banker/neobanker-agent.git
cd ..
```

---

## 2. Start infra (MySQL + Redis + Elasticsearch)

```bash
docker compose -f docker/docker-compose.yml up -d mysql redis elasticsearch

# Wait for ready
until docker exec neobanker-mysql mysqladmin ping -uroot -proot --silent; do sleep 2; done
until docker exec neobanker-redis redis-cli ping | grep -q PONG; do sleep 2; done
until curl -sS http://127.0.0.1:9200/_cluster/health | grep -qE '"status":"(green|yellow)"'; do sleep 3; done
echo "✅ infra ready"
```

**Optional** — phpMyAdmin GUI for inspecting MySQL:
```bash
docker compose -f docker/docker-compose.yml --profile full up -d phpmyadmin
# → http://localhost:8088 (root / root)
```

---

## 3. Grant root@'%' (so phpMyAdmin / external clients can connect)

```bash
docker exec neobanker-mysql mysql -uroot -proot -e "
  CREATE USER IF NOT EXISTS 'root'@'%' IDENTIFIED WITH mysql_native_password BY 'root';
  GRANT ALL ON *.* TO 'root'@'%' WITH GRANT OPTION;
  FLUSH PRIVILEGES;"
```

---

## 4. Patch .env files

```bash
# Frontend
cp templates/env/frontend.env.example repos/neobanker-frontend-MVP-V3/.env.local

# Agent
cp templates/env/agent.env.example repos/neobanker-agent/.env

# Backend has no .env — env vars are passed at startup time (step 6)
```

---

## 5. Install dependencies (first time only)

```bash
# Frontend
( cd repos/neobanker-frontend-MVP-V3 && npm install )

# Agent
( cd repos/neobanker-agent && uv sync --all-extras )

# Backend — Maven downloads deps automatically on first `./mvnw spring-boot:run`
```

---

## 6. Start backend (Spring Boot)

```bash
cd repos/neobanker-backend-MVP-V2
chmod +x mvnw

# Cross-container networking: backend in this shell needs to reach
# MySQL/Redis/ES that are running in OTHER containers.
# Linux: 172.17.0.1   |   macOS/Windows: host.docker.internal
export DOCKER_GATEWAY=172.17.0.1   # or host.docker.internal

export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64   # adjust for your platform
export ELASTICSEARCH_HOST=$DOCKER_GATEWAY
export SPRING_ELASTICSEARCH_REST_URIS=$DOCKER_GATEWAY:9200
export SPRING_DATA_REDIS_HOST=$DOCKER_GATEWAY
export SPRING_DATA_REDIS_PORT=6379

./mvnw spring-boot:run -DskipTests
```

Wait for: `Started NeobankerApplication in X seconds`. Tables auto-created by JPA.

Verify in another terminal:
```bash
curl -sS http://127.0.0.1:8080/actuator/health
# expect: db:UP, redis:UP, elasticsearch:UP
```

---

## 7. Import CSV data

```bash
pip install pymysql   # or: pip install pymysql --break-system-packages

cd repos/neobanker-backend-MVP-V2/import_data/import_scripts

python3 import_company.py             ../csv_data/company575_已查重.csv
python3 import_news.py                ../csv_data/news_2025-03-31.csv
python3 import_company_product.py     "../csv_data/product - Sheet1_cleaned.csv"
python3 import_financials.py          ../csv_data/500financials+4market.csv
python3 import_management.py          ../csv_data/management.csv
python3 import_no_tech.py             ../csv_data/employee_noTech.csv
python3 import_shareholder.py         ../csv_data/shareholder.csv
python3 import_marketing.py           ../csv_data/Marketing_cleaned.csv
python3 import_marketing_subtables.py "../csv_data/Marketing_New_Table - Sheet1.csv"
```

Verify:
```bash
docker exec neobanker-mysql mysql -uroot -proot neobanker -e "SELECT COUNT(*) FROM company"
# expect: 555
```

---

## 8. Bulk-load companies into Elasticsearch

```bash
python3 - <<'PY'
import pymysql, urllib.request, json
db = pymysql.connect(host='127.0.0.1', port=3307, user='root', password='root',
                     database='neobanker', charset='utf8mb4')
cur = db.cursor(pymysql.cursors.DictCursor)
cur.execute("SELECT id, name, ceoname, company_sort_id, bank_code, location_sort_id, logo_link, branding FROM company WHERE company_sort_id IS NOT NULL")
lines = []
for r in cur.fetchall():
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
print(json.loads(urllib.request.urlopen(req).read())['took'], 'ms')
PY
curl -X POST http://127.0.0.1:9200/companies/_refresh
```

Verify:
```bash
curl -sS 'http://127.0.0.1:8080/homepage/esSearch?keyword=ZA&type=company&page=0&size=3'
# expect: ZA Bank, Zain Cash, Paganza
```

---

## 9. Seed `search_logs` (so homepage hot-search chips show)

```bash
docker exec neobanker-mysql mysql -uroot -proot neobanker -e "
INSERT IGNORE INTO search_logs (id, created_at, updated_at, keyword, search_type, result_count, user_ip, session_id) VALUES
(1, NOW(), NOW(), 'ZA Bank', 'COMPANY', 1, '127.0.0.1', 'seed-1'),
(2, NOW(), NOW(), 'ZA Bank', 'COMPANY', 1, '127.0.0.1', 'seed-2'),
(3, NOW(), NOW(), 'OakNorth Bank', 'COMPANY', 1, '127.0.0.1', 'seed-3'),
(4, NOW(), NOW(), 'OakNorth Bank', 'COMPANY', 1, '127.0.0.1', 'seed-4'),
(5, NOW(), NOW(), 'Revolut', 'COMPANY', 1, '127.0.0.1', 'seed-5'),
(6, NOW(), NOW(), 'Nubank', 'COMPANY', 1, '127.0.0.1', 'seed-6');"
```

⚠️ Backend caches this — restart backend so the new keywords show up.

---

## 10. Start agent

```bash
cd repos/neobanker-agent
uv run uvicorn main:app --host 0.0.0.0 --port 8000
# → http://localhost:8000/docs
```

---

## 11. Start frontend

```bash
cd repos/neobanker-frontend-MVP-V3
npm run dev
# → http://localhost:3000/homepage
```

---

## Final smoke test

Open http://localhost:3000/homepage in browser:

- ✅ Hero shows search box + 4 hot-search chips (ZA Bank / OakNorth / Revolut / Nubank)
- ✅ "Recent News" lists ~6 real news headlines
- ✅ "Popular Banks" shows 8 bank logos
- ✅ Click "Banks Statistics" in nav → 24 banks paginated, search works

If anything fails, see [`troubleshooting.md`](troubleshooting.md).
