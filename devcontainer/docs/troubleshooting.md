# Neobanker DEV 环境踩坑记 + 解决方案

> 这次从零搭起 dev 环境，按时间顺序记录所有遇到的问题。
> 每个问题给出：症状、根因、修复方案、预防措施。

---

## 目录

1. [docker-compose 拉镜像超时](#1-docker-compose-拉镜像超时)
2. [容器内 DNS 解析失败](#2-容器内-dns-解析失败)
3. [Maven wrapper 找不到 mvnw](#3-maven-wrapper-找不到-mvnw)
4. [`run_import.sh` 硬编码 docker exec mysql-db](#4-run_importsh-硬编码-docker-exec-mysql-db)
5. [导入脚本表名 vs JPA 自动建表名不匹配](#5-导入脚本表名-vs-jpa-自动建表名不匹配)
6. [Spring Boot ES client 8.11 ↔ ES 7.10 server 不兼容](#6-spring-boot-es-client-811--es-710-server-不兼容)
7. [跨容器网络：localhost ≠ 容器内 localhost](#7-跨容器网络localhost--容器内-localhost)
8. [`/es/*` 接口要 JWT，无法批量 reindex](#8-es-接口要-jwt无法批量-reindex)
9. [Wikimedia thumb URL 返 400（ZA Bank logo）](#9-wikimedia-thumb-url-返-400za-bank-logo)
10. [SQL REGEX backref escape 串味，损坏 18 条 URL](#10-sql-regex-backref-escape-串味损坏-18-条-url)
11. [Homepage 缺 hot-search-words 卡片](#11-homepage-缺-hot-search-words-卡片)
12. [`.env.local` 缺 `NEXT_PUBLIC_BACKEND_API_URL`](#12-envlocal-缺-next_public_backend_api_url)
13. [Spring `@Cacheable` 缓存了空结果](#13-spring-cacheable-缓存了空结果)
14. [Backend 默认连远程生产 Redis](#14-backend-默认连远程生产-redis)
15. [Clerk publishable key 是 CI 占位符](#15-clerk-publishable-key-是-ci-占位符)
16. [bank-info 详情页 `Cannot read properties of null (reading 'owners')`](#16-bank-info-详情页-cannot-read-properties-of-null-reading-owners)

---

## 1. docker-compose 拉镜像超时

**症状**：`cd repos/neobanker-backend-MVP-V2 && docker compose up -d` 卡几分钟没动静

**根因**：网络慢 + docker hub 速率限制 + `docker-compose.yml` 用 `mysql:latest`（拉新镜像）

**修复**：
- 用 `dev/docker-compose.dev.yml` 锁定具体小镜像版本（`mysql:8.0`、`redis:7-alpine`）
- 后台拉：`docker pull mysql:8.0 &`，干别的事

**预防**：永远不用 `:latest`，docker-compose pin 版本

---

## 2. 容器内 DNS 解析失败

**症状**：在 `my-ubuntu-dev` 内部 `apt install`、`pip install`、Maven 拉依赖全报 `Temporary failure in name resolution`

**根因**：容器 `/etc/resolv.conf` 用 `nameserver 192.168.71.185` (宿主机内网 DNS) 不可达

**修复**（容器内执行）：
```bash
echo 'nameserver 8.8.8.8
nameserver 1.1.1.1' > /etc/resolv.conf
```

**预防**：docker daemon 配置 `--dns 8.8.8.8` 持久化；或用 docker-compose `dns:` 字段

---

## 3. Maven wrapper 找不到 mvnw

**症状**：`./mvnw -version` → `No such file or directory`

**根因**：路径错（`/home/.../neobanker-backend-MVP-V2/` 在容器内挂载点不同）

**修复**：用容器内挂载点 `/workspace/repos/neobanker-backend-MVP-V2/`，并 `chmod +x mvnw`

**预防**：bootstrap.sh 用相对路径 `$SCRIPT_DIR/../`

---

## 4. `run_import.sh` 硬编码 docker exec mysql-db

**症状**：`run_import.sh company` → `docker: command not found` (容器内没 docker CLI)

**根因**：脚本用 `docker exec mysql-db mysql ...` 假设外部 docker-compose 上下文

**修复**（最小化）：在容器内创建 `/usr/local/bin/docker` shim，把 `docker exec mysql-db` 重定向到本地 `mysql` client：
```bash
cat > /usr/local/bin/docker <<'EOF'
#!/bin/bash
if [ "$1" = exec ]; then
  shift; [ "$1" = -i ] && shift
  if [ "$1" = mysql-db ]; then
    shift; cmd=$1; shift
    exec "$cmd" -h 127.0.0.1 -P 3307 "$@"
  fi
fi
EOF
chmod +x /usr/local/bin/docker
```

**永久修复**：重构 `run_import.sh` 用 `MYSQL_PWD=... mysql -h $DB_HOST` 替代 `docker exec`

---

## 5. 导入脚本表名 vs JPA 自动建表名不匹配

**症状**：
- `run_import.sh news` → `Table 'neobanker.news' doesn't exist`
- `run_import.sh management` → `Table 'neobanker.management' doesn't exist`

**根因**：`run_import.sh` 用 wrapper 名 `news`/`management` 做 mysqldump 备份，但 import_*.py 实际写到 `company_news`/`staff_management` 表

**修复**：跳过 `run_import.sh` 直接调 `python3 import_news.py ...`（绕过 wrapper backup 逻辑）

**永久修复**：在 `run_import.sh` 加 wrapper-name → real-table-name 映射

---

## 6. Spring Boot ES client 8.11 ↔ ES 7.10 server 不兼容

**症状**：backend 启动时 `Missing [X-Elastic-Product] header. Please check that you are connecting to an Elasticsearch instance`，health 报 `elasticsearch: DOWN`

**根因**：`elasticsearch-java:8.11.1` 严格要求 server 返回 `X-Elastic-Product: Elasticsearch` header；ES 7.10 不发，**7.17.0 才加**

**修复**：换 ES 7.17.x 或 8.x：
```bash
docker stop elasticsearch && docker rm elasticsearch
docker pull docker.elastic.co/elasticsearch/elasticsearch:7.17.20
docker run -d --name elasticsearch -p 9200:9200 \
  -e "discovery.type=single-node" -e "xpack.security.enabled=false" \
  docker.elastic.co/elasticsearch/elasticsearch:7.17.20
```

**预防**：`docker-compose.dev.yml` pin `7.17.20`

---

## 7. 跨容器网络：localhost ≠ 容器内 localhost

**症状**：ES 容器 UP（`curl http://127.0.0.1:9200` 在宿主机 OK），但 backend health 报 `elasticsearch: DOWN — Connection refused`

**根因**：backend 跑在 `my-ubuntu-dev` 容器内；该容器的 `localhost` 不是宿主机 localhost。`application.properties` 写 `elasticsearch.host=localhost` 解析到容器自己，9200 没监听 → 拒绝连接

**修复**：用 docker bridge gateway IP 启动 backend：
```bash
docker exec my-ubuntu-dev bash -c "
  export ELASTICSEARCH_HOST=172.17.0.1 \
         SPRING_ELASTICSEARCH_REST_URIS=172.17.0.1:9200 \
         SPRING_DATA_REDIS_HOST=172.17.0.1
  ./mvnw spring-boot:run"
```

**Mac/Windows**：用 `host.docker.internal` 替代 `172.17.0.1`

**永久修复**：把 backend 也搬到 `docker-compose.dev.yml`，所有服务用同一个 user-defined network，hostname 用 service name

---

## 8. `/es/*` 接口要 JWT，无法批量 reindex

**症状**：`curl -X POST /es/createIndex` → `{"error":"?????Authorization??"}`

**根因**：`/es/**` 不在 `SecurityConfig.permitAll` 列表

**修复**（绕过）：直接写 ES bulk API，从 MySQL 批量 load：
```python
import pymysql, urllib.request, json
db = pymysql.connect(...)
cur.execute("SELECT id, name, ... FROM company")
lines = []
for r in cur:
    lines.append(json.dumps({'index':{'_index':'companies','_id':str(r['id'])}}))
    lines.append(json.dumps({'name':r['name'], ...}))
urllib.request.urlopen('http://127.0.0.1:9200/_bulk',
    data=('\n'.join(lines)+'\n').encode(),
    headers={'Content-Type':'application/x-ndjson'})
```

**永久修复**：要么把 `/es/init` 加到 permitAll，要么提供 dev token（jwt token 写在 .env）

---

## 9. Wikimedia thumb URL 返 400（ZA Bank logo）

**症状**：`<img src="https://upload.wikimedia.org/wikipedia/commons/thumb/.../1200px-ZA_Bank_logo.svg.png">` 浏览器加载失败

**根因**：Wikimedia thumb 只支持 ladder 上的尺寸（120/240/480/960/1280...），1200 不在列；error 页提示 "Use thumbnail steps listed on https://w.wiki/GHai"

**修复**：去 thumb，用原图 URL（SVG/PNG 浏览器直渲）
```python
# /commons/thumb/X/YZ/Filename/NNNpx-Filename → /commons/X/YZ/Filename
re.sub(r'/wikipedia/commons/thumb/([^/]+/[^/]+/[^/]+)/\d+px-[^/]+$',
       r'/wikipedia/commons/\1', url)
```

**预防**：CSV 数据源校验 logo URL；前端加 `<img onError={fallback}>`

---

## 10. SQL REGEX backref escape 串味，损坏 18 条 URL

**症状**：用 `docker exec mysql -e "UPDATE ... REGEXP_REPLACE(..., '\\\\1')"` 修复 URL，结果 18 条变成 `https://upload.wikimedia.org/wikipedia/commons/1`

**根因**：bash → docker exec → mysql `-e` 三层转义，`\\\\1` 在 mysql 实际收到 `\1` 但 REGEXP_REPLACE 把它当字面量

**修复**：用 Python pymysql 直连，参数化处理（不走 shell escape）

**预防**：永远不在 shell 嵌 SQL REGEX；用脚本语言

---

## 11. Homepage 缺 hot-search-words 卡片

**症状**：生产环境搜索框下方有 2 个银行 chip，dev 没有

**根因**：`HotSearchServiceImpl` 从 `search_logs` 表统计；dev 刚搭起来这表是 0 行

**修复**：seed 几条 search_logs：
```sql
INSERT INTO search_logs (id, created_at, updated_at, keyword, search_type, result_count, user_ip, session_id) VALUES
  (1, NOW(), NOW(), 'ZA Bank', 'COMPANY', 1, '127.0.0.1', 'seed-1'),
  (2, NOW(), NOW(), 'OakNorth Bank', 'COMPANY', 1, '127.0.0.1', 'seed-2'),
  ...;
```

**永久修复**：backend 加 fallback — `search_logs` 空时取 top N companies

---

## 12. `.env.local` 缺 `NEXT_PUBLIC_BACKEND_API_URL`

**症状**：浏览器 console `Failed to load resource: 404 @ http://127.0.0.1:3000/undefined/homepage/hot-search-words`

**根因**：`page.tsx` 直接用 `process.env.NEXT_PUBLIC_BACKEND_API_URL + '/homepage/hot-search-words'`，但 `.env.local` 没设 → undefined → URL 拼出 `/undefined/...`

**修复**：在 `.env.local` 添加：
```
NEXT_PUBLIC_BACKEND_API_URL=http://localhost:8080
```
然后 **重启 Next dev server**（next 不会 hot-reload 环境变量）

**永久修复**：所有 fetch 都走 `apiClient`/`config.backendApiUrl`（带 fallback），不要直接 `process.env.X`

---

## 13. Spring `@Cacheable` 缓存了空结果

**症状**：seed 完 search_logs 后 `/homepage/hot-search-words` 还返 `[]`

**根因**：`HotSearchServiceImpl.getHotSearchWords` 加了 `@Cacheable(value="hotSearchWords", key="#limit")`，第一次调用空结果被缓存 30 分钟

**修复**：重启 backend，让 caffeine cache 清空

**永久修复**：在 actuator 暴露 `caches` endpoint：
```properties
management.endpoints.web.exposure.include=health,info,metrics,caches
```
然后 `curl -X DELETE /actuator/caches/hotSearchWords` 即可清

---

## 14. Backend 默认连远程生产 Redis

**症状**：dev backend `redis: UP`，但实际连的 `221.122.67.17:16379`（生产 server）

**根因**：`application.properties` 硬编码 `spring.data.redis.host=221.122.67.17`

**修复**：用环境变量 override，启动时传：
```bash
export SPRING_DATA_REDIS_HOST=172.17.0.1
export SPRING_DATA_REDIS_PORT=6379
./mvnw spring-boot:run
```

**永久修复**：拆 `application-dev.properties` + `application-prod.properties`，profile 切换

---

## 15. Clerk publishable key 是 CI 占位符

**症状**：浏览器 console 报 `ClerkJS: Something went wrong initializing Clerk in development mode`，登录按钮不工作

**根因**：`.env.local` 用的是 `pk_test_Y2ktYnVpbGQuY2xlcmsuYWNjb3VudHMuZGV2JA==`（base64 解开 = `ci-build.clerk.accounts.dev$`）—— 是 CI 测试用的格式合法但不可用的占位

**修复**：去 https://dashboard.clerk.com 注册一个 development application，拿到真 `pk_test_...` + `sk_test_...` 替换。**该步骤必须人手操作**（涉及账号 + 邮箱验证）

**临时绕过（如不需要登录）**：保持现状，匿名用户 `/homepage`、`/banks-statistics`、`/bank-info/*` 都能用（permitAll）；只是 Sign In 按钮点了无反应

---

## 16. bank-info 详情页 `Cannot read properties of null (reading 'owners')`

**症状**：访问 `/bank-info/1/overview` 页面 crash

**根因**：`page.tsx:220` `AboutData.owners[0]` 没做 null check：
```tsx
{AboutData?.owners?.length === 0 ? <NoData/> :
 AboutData?.owners?.length === 1 ? renderOwner(AboutData.owners[0]) :
 (
   <>
     {renderOwner(AboutData.owners[0])}  // crashes if owners undefined
     {renderOwner(AboutData.owners[1])}
   </>
 )}
```
当 `owners` 是 `undefined`，前两个三元都不匹配，进 else 分支 → 引用 `[0]` 报错

**修复**（一行）：
```tsx
!AboutData?.owners?.length ? <NoData/> :
 AboutData.owners.length === 1 ? renderOwner(AboutData.owners[0]) :
 ...
```

**预防**：lint rule `@typescript-eslint/no-non-null-assertion`，TS strict mode

---

## 17. Why CI/docker doesn't work on macOS / Windows GHA runners

**症状**：你看到 `infra.yml` 这个 workflow 只在 `ubuntu-latest` 跑；如果之前你看过 macOS / Windows job 失败，这就是原因。

**根因**：是 **GitHub Actions 平台限制**，不是 Mac/Windows 系统问题，也不是 Docker 本身的问题。

| 平台 | 状态 | 为啥 |
|---|---|---|
| `macos-latest` runner | docker **完全没装** | GHA macOS runner 跑在 Apple Silicon Mac mini 上，**禁用了 nested virtualization**（Apple hypervisor.framework）。Docker on macOS 必须在 macOS 里跑一个 Linux VM，nested virt 关了就跑不起来。我试过 `brew install colima` 自己装 — colima 用 lima/QEMU，**也需要 nested virt** → 启 VM 时立刻 exit status 1。<br>引用：[GitHub docs — macOS GHA runners do not include Docker](https://docs.github.com/en/actions/using-jobs/choosing-the-runner-for-a-job#about-github-hosted-runners) |
| `windows-latest` runner | docker 在 **Windows-container 模式** | runner 用 Windows Server core，docker 默认拉 Windows 镜像（mysql:8.0 是 Linux 镜像 → 不兼容 → `image operating system "linux" cannot be used on this platform`）。切到 Linux containers 模式需要 Hyper-V + nested virt → **同样被禁用**。<br>引用：[GitHub docs — "Linux containers are not supported on the Windows runners"](https://docs.github.com/en/actions/using-github-hosted-runners/about-github-hosted-runners/about-github-hosted-runners) |
| `ubuntu-latest` runner | ✅ 原生 docker | 跑在 Linux VM 里，docker 是宿主层级，无 nested virt 需求 |

**对开发者意味着什么**：
- 你的 Mac/PC 本地 docker 是好的（Docker Desktop / OrbStack / Rancher Desktop / Colima 都行），`bash scripts/bootstrap.sh` 能跑通
- **CI 只在 Linux 验证 docker 流程**，不代表你 Mac 上跑不了
- 如果将来要 mac/Win CI 真验证，参考 [ci-roadmap.md §Cross-platform CI](ci-roadmap.md#cross-platform-ci-macwindows) — 要换到付费 GHA runner 或第三方 CI（Namespace.so / Cirrus CI）

**不会修**：免费 GHA runners 的 nested virt 限制不在我们能改的范围内。Lint 在 3 平台跑就够了——抓 Windows path 问题、bash shebang 兼容性等真正会咬开发者的东西。

---

## 一句话经验

| 教训 | 一句话 |
|---|---|
| docker-compose 永远 pin 版本 | `:latest` 在 production 是炸弹 |
| 容器互访 ≠ localhost | `172.17.0.1` (Linux) / `host.docker.internal` (Mac/Win) |
| Spring `@Cacheable` 会缓存错误结果 | 改 cache config 时重启服务 |
| Wikimedia thumb 只接受 ladder 尺寸 | 用原图 URL 最稳 |
| `process.env.X` 没 fallback 会拼出 `/undefined/...` | 用 config 模块带 default |
| ES client/server 主版本要对齐 | 8.x client 至少要 7.17 server |
