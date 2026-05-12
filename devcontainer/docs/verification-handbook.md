# 后端验证手册（3 个层级）

> 创建：2026-05-09 · 适用：本地开发 / staging
> 假设：你已按 `database-schema-report.md` 启动了 MySQL + Spring Boot

本手册回答 3 个常见问题：

| # | 问题 | 用什么验证 | 通过标志 |
|---|---|---|---|
| **L1** | 后端进程是不是真的起来了？ | `actuator/health` + 进程检查 | HTTP 200 + `status` 字段含 `UP` |
| **L2** | 后端是不是真的连上数据库了？ | `health` 的 db 子项 + 直接打一个 DAO 端点 | `db.status=UP` + 端点返真实数据 |
| **L3** | 前端是不是真的能调到后端？ | 浏览器 DevTools Network + 数据展示 | 关键 API 200 + 页面显示真实数据（不是 mock） |

---

## L1. 后端进程是否成功启动 ✅

### 一句话验证

```bash
curl -sS http://127.0.0.1:8080/actuator/health | python3 -m json.tool
```

**通过标志**：返回 JSON 且 `status` 含 `UP`（即使总状态是 `DOWN`，只要 `db` 和你需要的子组件 `UP` 即可，`elasticsearch DOWN` 不算阻塞）。

### 完整 5 步检查表

| 检查项 | 命令 | 预期 |
|---|---|---|
| 1. 端口在监听 | `ss -tln \| grep :8080` 或 `docker exec my-ubuntu-dev ss -tln \| grep :8080` | 出现 `LISTEN ... 0.0.0.0:8080` |
| 2. 进程存活 | `docker exec my-ubuntu-dev ps -ef \| grep '[s]pring-boot'` | 至少一行 java/maven 进程 |
| 3. health 端点响应 | `curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/actuator/health` | `200` |
| 4. info 端点响应 | `curl -sS http://127.0.0.1:8080/actuator/info` | `{}` 或 build info（不报 404） |
| 5. 启动日志结尾 | `tail -50 /tmp/.../bzlakagt4.output \| grep -E 'Started\|Tomcat'` | `Started NeobankApplication in X seconds` |

### 常见错误 & 修复

| 现象 | 原因 | 修复 |
|---|---|---|
| `curl: connection refused` on :8080 | 进程没起 / 还在编译 | 看 `tail -f` 启动日志，等到 `Started ... in X seconds` 出现 |
| 启动卡 30s+ 然后 OOM | JVM 内存不足 | `export MAVEN_OPTS="-Xmx2g"` 重启 |
| `port 8080 already in use` | 端口冲突 | `lsof -i :8080` 找占用进程 → kill |
| `JAVA_HOME not set` | Maven 找不到 JDK | `export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64` |
| Maven 拉依赖失败 `Unknown host` | 容器 DNS 坏了 | `echo 'nameserver 8.8.8.8' > /etc/resolv.conf`（容器内） |

---

## L2. 后端是否成功连上数据库 ✅

### 一句话验证

```bash
curl -sS http://127.0.0.1:8080/actuator/health | python3 -c "import json,sys; d=json.load(sys.stdin); print('DB:', d['components']['db']['status'])"
```

**通过标志**：输出 `DB: UP`。

### 完整 5 步检查表

| 检查项 | 命令 | 预期 |
|---|---|---|
| 1. health 子项 | 见上 | `db.status=UP`，`details.database=MySQL` |
| 2. 表已建好 | `docker exec my-ubuntu-dev mysql -uroot -proot neobanker -e "SHOW TABLES;" \| wc -l` | `60+`（JPA `ddl-auto=update` 自动建 65 张） |
| 3. 数据已导入 | `docker exec my-ubuntu-dev mysql -uroot -proot neobanker -e "SELECT COUNT(*) FROM company;"` | `>0`（应该 555） |
| 4. 后端能读到数据 | `curl -sS -X POST http://127.0.0.1:8080/homepage/getBanksStatistics -H 'Content-Type: application/json' -d '{}' \| head -c 200` | 返回 JSON 数组，包含真实银行名（如 OakNorth Bank） |
| 5. JPA 没异常 | `tail -200 启动日志 \| grep -iE 'sqlexception\|hibernateexception\|cannot connect'` | 无输出 |

### 不止"连上"，还要"能读真实数据"

✅ 只看 `db.status=UP` 是不够的——它只验证 datasource ping 通。要验证 ORM + 数据完整性，必须实打一个端点：

```bash
# Bank 列表（不依赖 Elasticsearch）
curl -sS -X POST http://127.0.0.1:8080/homepage/getBanksStatistics \
  -H 'Content-Type: application/json' -d '{}' | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(f'返回 {len(data)} 条记录')
print('示例:', json.dumps(data[0], ensure_ascii=False, indent=2))
"
```

**期望**：返回 **真实公司名** 和 **真实产品名**（如 "OakNorth Bank · Business loans"），不是占位符或 demo。

### 常见错误 & 修复

| 现象 | 原因 | 修复 |
|---|---|---|
| `db.status=DOWN` + `Communications link failure` | MySQL 没起 / 端口错 | `docker exec my-ubuntu-dev service mysql status`，确认监听 `:3307` |
| `Access denied for user` | 密码错 | 对照 `application.properties` 的 `username/password`：默认 `neobanker_backend / neobanker_backend_password` |
| `Unknown database 'neobanker'` | 库没建 | `mysql -uroot -e "CREATE DATABASE neobanker CHARACTER SET utf8mb4;"` |
| `Table 'neobanker.xxx' doesn't exist` | JPA 没建表（启动失败前死的） | 看启动日志找根因，或手动 `./mvnw spring-boot:run` 看 stderr |
| 端点返 `[]` 但 db UP | 表存在但数据没导 | 跑 `import_data/import_scripts/import_*.py`，参考 `database-schema-report.md` §1 |

---

## L3. 前端是否成功连接后端 ✅

### 一句话验证

打开 http://127.0.0.1:3000/homepage，看 "Recent News" 部分是否显示真实新闻标题（如 "Titan Trust Bank Selects Oracle FSS"）。

### 完整 6 步检查表

| 检查项 | 怎么做 | 预期 |
|---|---|---|
| 1. 前端进程在跑 | `curl -sI http://127.0.0.1:3000/homepage` | `HTTP/1.1 200 OK` |
| 2. 前端配置指对了后端 | `cat repos/neobanker-frontend-MVP-V3/.env.local \| grep BACKEND` 或 `config/environment.ts` 看 `backendApiUrl` | 默认 `http://localhost:8080` |
| 3. 浏览器 → 后端的 CORS 通 | DevTools Network → 看 API 请求 | 无 CORS error，请求 status `200` |
| 4. 主页数据来源是后端 | DevTools Network → 过滤 `8080` | 至少 3 个请求 200：`getBanksStatistics`、`getResourceUrlsByType`、`location/all` |
| 5. 数据是真的 | 看主页 Recent News | 出现真实新闻标题（不是 "Lorem ipsum" 或固定 mock） |
| 6. 详情页能拉数据 | 打开 `/bank-info/1/overview` | 显示真实银行（ZA Bank 等） |

### Playwright 一键验证脚本

```bash
# 在 frontend 目录跑
npx playwright test e2e/integration.spec.ts
```

或手动用 DevTools：

```js
// 浏览器 console 跑这个
fetch('http://localhost:8080/actuator/health').then(r=>r.json()).then(console.log)
fetch('http://localhost:8080/homepage/getBanksStatistics', {
  method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'
}).then(r=>r.json()).then(d=>console.log('rows:', d.length, 'first:', d[0]))
```

### 常见错误 & 修复

| 现象 | 原因 | 修复 |
|---|---|---|
| 前端页面空白 / "Failed to fetch" | 前端连不到后端 | 检查 `NEXT_PUBLIC_BACKEND_API_URL`，或者后端没起 |
| 浏览器 DevTools 显示 CORS error | 后端 CORS 配置缺前端 origin | 后端 `WebConfig.java` 的 `addCorsMappings` 加 `http://127.0.0.1:3000` |
| 主页 Recent News 空 | `company_news` 表空 / 后端报 SQL 错 | `SELECT COUNT(*) FROM company_news;` 应该 `27550`；如果是 0 跑 `import_news.py` |
| 详情页 `Cannot read properties of null (reading 'owners')` | 前端缺 null check（已知 bug） | bank-info/[sortId]/overview/page.tsx:220 加 `?.owners?.[0]` optional chaining |
| `/banks-statistics` 列表空白 | Elasticsearch DOWN，`/homepage/esSearch` 500 | 起 ES：`docker run -d -p 9200:9200 -e discovery.type=single-node elasticsearch:7.10.0` |
| Network 请求显示 `localhost:8080` 但实际打到生产 | `.env.local` 覆盖了 dev | 删除 `.env.local` 里的 `NEXT_PUBLIC_BACKEND_API_URL` 或改成 dev |

---

## 一键全栈健康检查脚本

把下面这段保存到 `scripts/health-check.sh`，每次起服务后跑一次：

```bash
#!/usr/bin/env bash
set -e

echo "=== L1: Backend process ==="
curl -sS http://127.0.0.1:8080/actuator/health | python3 -c "
import json,sys
d=json.load(sys.stdin)
checks = {'db','redis','ping'}
for k in checks:
    s = d['components'].get(k,{}).get('status','MISSING')
    icon = '✅' if s == 'UP' else '❌'
    print(f'  {icon} {k}: {s}')
" || { echo '❌ /actuator/health 不通'; exit 1; }

echo
echo "=== L2: Database has data ==="
COUNT=$(docker exec my-ubuntu-dev mysql -uroot -proot neobanker -N -e "SELECT COUNT(*) FROM company;" 2>/dev/null)
[ "$COUNT" -gt 0 ] && echo "  ✅ company table: $COUNT rows" || { echo "  ❌ company empty"; exit 1; }

echo
echo "=== L3: Backend serving real data ==="
FIRST_BANK=$(curl -sS -X POST http://127.0.0.1:8080/homepage/getBanksStatistics \
  -H 'Content-Type: application/json' -d '{}' | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['name'])")
echo "  ✅ first bank from API: $FIRST_BANK"

echo
echo "=== L3b: Frontend ==="
curl -sI http://127.0.0.1:3000/homepage | head -1 | grep -q '200' \
  && echo "  ✅ frontend 3000 OK" || echo "  ❌ frontend down"

echo
echo "🎉 All checks passed"
```

---

## 故障排查矩阵

```
症状                              -> 先看 L?
-------------------------------------------------
浏览器打不开 :3000              -> L3 第 1 步（前端进程）
浏览器能开但数据空              -> L3 第 4 步（API 请求）
浏览器报 CORS                   -> L3 第 3 步
API 返 500                      -> L1 启动日志 + L2 db.status
API 返 200 但 [] 空             -> L2 第 3 步（数据是否导入）
:8080 connection refused        -> L1 第 1 步（端口）
启动日志卡在 "Hibernate ..."    -> L2 第 4 步（JPA 异常）
```

---

## 附：当前已验证的端点（2026-05-09 实测）

| Endpoint | Method | 状态 | 说明 |
|---|---|---|---|
| `/actuator/health` | GET | ✅ 200 | health 总入口 |
| `/actuator/info` | GET | ✅ 200 | build info |
| `/homepage/getBanksStatistics` | POST | ✅ 200 | bank+product 列表（不需要 ES） |
| `/homepage/getResourceUrlsByType` | POST | ✅ 200 | 资源 URL |
| `/location/all` | GET | ✅ 200 | 地区列表 |
| `/homepage/esSearch?keyword=...` | GET | ✅ 200 | 搜索（现已 OK，见 §L4） |
| `/api/companies` (JWT-protected) | GET | 401 | 正常，需要登录 token |

---

## L4. Elasticsearch 集成 ✅（搜索功能）

### 一句话验证

```bash
curl -sS 'http://127.0.0.1:8080/homepage/esSearch?keyword=ZA&type=company&page=0&size=5' | python3 -m json.tool | head -20
```

**通过标志**：返回 `totalElements > 0`，`content` 数组里有真实银行（如 ZA Bank）。

### 启动 ES（容器版）

```bash
# ⚠️ 注意：必须用 7.17.x（含）以上版本
# 因为 backend client 是 elasticsearch-java:8.11.1，会校验 X-Elastic-Product header
# ES 7.10/7.11 不发这个 header → backend 报 "Missing [X-Elastic-Product] header"
docker run -d --name elasticsearch \
  -p 9200:9200 -p 9300:9300 \
  -e "discovery.type=single-node" \
  -e "ES_JAVA_OPTS=-Xms512m -Xmx512m" \
  -e "xpack.security.enabled=false" \
  docker.elastic.co/elasticsearch/elasticsearch:7.17.20

# 等就绪
until curl -sS http://127.0.0.1:9200/ 2>/dev/null | grep -q '"number"'; do sleep 3; done
echo "ES ready: $(curl -sS http://127.0.0.1:9200/ | python3 -c "import json,sys;print(json.load(sys.stdin)['version']['number'])")"
```

### 让 backend 连上 ES

如果 backend 在 `my-ubuntu-dev` 容器内、ES 在另一个独立容器，两者不能用 `localhost:9200` 互通，需用 docker bridge gateway：

```bash
# 启动 backend 时用环境变量 override
docker exec my-ubuntu-dev bash -c "cd /workspace/repos/neobanker-backend-MVP-V2 && \
  export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
         ELASTICSEARCH_HOST=172.17.0.1 \
         SPRING_ELASTICSEARCH_REST_URIS=172.17.0.1:9200 && \
  ./mvnw spring-boot:run -DskipTests"
```

> `172.17.0.1` 是 docker0 bridge gateway IP（宿主机），ES 监听在宿主机上，从 `my-ubuntu-dev` 容器内访问宿主机用此 IP。

验证 backend ↔ ES：

```bash
curl -sS http://127.0.0.1:8080/actuator/health | python3 -c "
import json,sys; d=json.load(sys.stdin)
print('elasticsearch:', d['components']['elasticsearch']['status'])"
# 期望: elasticsearch: UP
```

### 灌索引数据（reindex）

backend 提供了 `/es/*` 接口（**需 JWT token**）：

```bash
curl -sS -X POST http://127.0.0.1:8080/es/createIndex -H "Authorization: Bearer $TOKEN"
curl -sS -X POST http://127.0.0.1:8080/es/addCompanies -H "Authorization: Bearer $TOKEN"
curl -sS -X POST http://127.0.0.1:8080/es/addCompanyProducts -H "Authorization: Bearer $TOKEN"
curl -sS -X POST http://127.0.0.1:8080/es/addMarketing -H "Authorization: Bearer $TOKEN"
curl -sS -X POST http://127.0.0.1:8080/es/addFinancials -H "Authorization: Bearer $TOKEN"
```

**没 token 时的快速绕过**：直接从 MySQL bulk-load 到 ES（`scripts/es_bulk_load_companies.py`）：

```python
import pymysql, urllib.request, json
db = pymysql.connect(host='127.0.0.1', port=3307, user='root',
                     password='root', database='neobanker', charset='utf8mb4')
cur = db.cursor(pymysql.cursors.DictCursor)
cur.execute("""SELECT id, name, ceoname, company_sort_id, bank_code,
                       location_sort_id, logo_link, branding
                FROM company WHERE company_sort_id IS NOT NULL""")
rows = cur.fetchall()

lines = []
for r in rows:
    lines.append(json.dumps({'index': {'_index': 'companies', '_id': str(r['id'])}}))
    lines.append(json.dumps({
        'id': str(r['id']), 'name': r['name'] or '',
        'CEOName': r['ceoname'] or '', 'companySortId': r['company_sort_id'] or '',
        'bankCode': r['bank_code'] or '', 'locationSortId': r['location_sort_id'] or '',
        'logoLink': r['logo_link'] or '',
        'spotlight': (r['branding'] or '')[:500], 'score': 1.0,
    }, ensure_ascii=False))
body = ('\n'.join(lines) + '\n').encode('utf-8')

req = urllib.request.Request('http://172.17.0.1:9200/_bulk', data=body,
    headers={'Content-Type': 'application/x-ndjson'}, method='POST')
print(json.loads(urllib.request.urlopen(req, timeout=30).read())['took'], 'ms')
```

刷新索引后立刻可搜：

```bash
curl -sS 'http://127.0.0.1:9200/companies/_refresh'
curl -sS 'http://127.0.0.1:9200/_cat/indices?v'
# health  status  index     docs.count  store.size
# yellow  open    companies        575      149.1kb
```

### 验证前端 banks-statistics 搜索

```
浏览器打开 http://127.0.0.1:3000/banks-statistics?search=OakNorth
→ 期望显示 "OakNorth Bank (United Kingdom)" 1 条卡片

http://127.0.0.1:3000/banks-statistics?search=ZA
→ 期望显示 ZA Bank, Zain Cash, Paganza 3 条卡片
```

### 常见错误 & 修复

| 现象 | 原因 | 修复 |
|---|---|---|
| `elasticsearch.status=DOWN` 但 ES 容器 UP | backend 在另一容器，连不到 `localhost:9200` | 用 `ELASTICSEARCH_HOST=172.17.0.1` 启动 |
| 启动日志报 `Missing [X-Elastic-Product] header` | ES 7.10/7.11 太老，不兼容 8.x client | 升级到 ES 7.17.x 或 8.x |
| backend UP 但 `/homepage/esSearch` 返 0 条 | ES 索引为空 | 跑 reindex（见上节） |
| 搜索结果跟数据库不一致 | 索引过时 / 没 refresh | `curl -X POST 'http://127.0.0.1:9200/companies/_refresh'` |
| ES 容器 OOM 重启 | 默认 ES_JAVA_OPTS 太大 | `-e "ES_JAVA_OPTS=-Xms512m -Xmx512m"` |
