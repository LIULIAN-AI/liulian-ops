# Neobanker — 本地开发环境

[![Lint](https://github.com/neo-banker/neobanker-dev-env/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/neo-banker/neobanker-dev-env/actions/workflows/lint.yml)
[![Infra Bootstrap](https://github.com/neo-banker/neobanker-dev-env/actions/workflows/infra.yml/badge.svg?branch=main)](https://github.com/neo-banker/neobanker-dev-env/actions/workflows/infra.yml)
[![Full Bootstrap](https://github.com/neo-banker/neobanker-dev-env/actions/workflows/full.yml/badge.svg?branch=main)](https://github.com/neo-banker/neobanker-dev-env/actions/workflows/full.yml)

一条命令启动 **Neobanker 全栈本地开发环境**（前端、后端、Agent、MySQL、Redis、Elasticsearch）。

> 🎯 这是 **本机** 路线。GitHub Codespaces 路线在 `feat/codespaces-auto-setup` 分支。

English: [`README.md`](./README.md)

---

## 平台支持 — CI 实际验证范围

| 平台 | Lint | 仅基础设施 | **完整启动** | 备注 |
|---|:---:|:---:|:---:|---|
| **Linux**（Ubuntu 22.04+） | ✅ CI | ✅ CI | 🟡 手动‡ | 原生 docker；脚本端到端能跑通，但 **CI 只验 docker 层** |
| **macOS**（Apple Silicon / Intel） | ✅ CI | 🟡 手动† | 🟡 手动 | 需要 Docker Desktop / OrbStack / Rancher Desktop |
| **Windows 10/11** | ✅ CI | 🟡 手动† | 🟡 手动 | 需要 WSL2 + Docker Desktop |

CI 徽章是 `main` 分支最新 commit 的实时结果。

### CI 究竟验证了什么（实事求是）

| Job | 它做了什么 | 它**没**做什么 |
|---|---|---|
| **Lint**（3 平台） | bash `bash -n` 语法检查、env 模板 KEY=VALUE 格式、`docker compose config -q`（仅 Linux） | 不启动任何容器 |
| **Infra-only**（Linux） | `bash scripts/bootstrap.sh --infra-only` 启动 MySQL/Redis/ES 容器 + ping 检查 | **不** clone 3 个服务仓，**不** 启 backend/agent/frontend，**不** 导入数据，**不** 测 API |
| **Full**（Linux，gated） | 本意：clone 全部仓 + 启 backend/agent/frontend + 端到端冒烟测试 | **当前禁用** — 需要在 dev-env 仓 Settings 加 `BACKEND_REPO_TOKEN` / `FRONTEND_REPO_TOKEN` / `AGENT_REPO_TOKEN` GitHub secret 才能 clone 私仓。详见 [docs/ci-roadmap.md](docs/ci-roadmap.md) |

> **底线**：上方绿 ✅ 的意思是 "docker compose 服务在 Linux 上能干净起来"。它**不代表** "整个 Neobanker 应用在每个 PR 都跑通"。后者要做请见 [CI Roadmap](docs/ci-roadmap.md)。

> † macOS/Windows 上 infra-only 是"手动"是因为 GitHub Actions 免费 runner 在 macOS 上不装 docker，
> 在 Windows 上只支持 Windows-container 模式。**脚本本身在 macOS/Windows 本地能跑** —
> 详见 [docs/troubleshooting.md](docs/troubleshooting.md#why-no-cidocker-on-macos-and-windows)。
>
> ‡ Linux 完整启动也是"手动"是因为 CI 里 clone 私仓需要 GitHub PAT secret，目前还没配。
> 开发者本地有 SSH 权限的话脚本是能跑通的 — 2026-05-09 已手动验证。

---

## 启动后你能用到的服务

| 服务 | 端口 | 访问地址 |
|---|---:|---|
| 前端（Next.js 14） | 3000 | http://localhost:3000/homepage |
| 后端（Spring Boot 3.1） | 8080 | http://localhost:8080/actuator/health |
| Agent（FastAPI） | 8000 | http://localhost:8000/docs |
| MySQL 8.0 | 3307 | `mysql -uroot -proot neobanker` |
| Elasticsearch 7.17 | 9200 | http://localhost:9200/_cat/indices?v |
| Redis 7 | 6379 | `redis-cli` |
| phpMyAdmin（可选） | 8088 | http://localhost:8088 |

外加预导入的演示数据：**3.5 万+ 条**分布在 11 张业务表（575 家银行、27,500 条新闻、2,700 个产品等），**575 家公司已索引到 Elasticsearch**。

---

## 前置依赖（在宿主机安装）

| 工具 | 版本 | 安装方式 |
|---|---|---|
| Docker | 20+ 含 `docker compose` 插件 | https://docs.docker.com/get-docker/ |
| Git | 任意 | 通常已预装 |
| Java | **17**（Temurin 或 OpenJDK） | `sdk install java 17.0.10-tem` 或包管理器 |
| Node | **20** | https://nodejs.org 或 `nvm install 20` |
| Python | **3.12** | https://python.org 或 `pyenv install 3.12` |
| `uv` | 最新 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

> 版本与各服务仓库 `deploy.yml` 对齐（Java 17 / Node 20 / Python 3.12）。

---

## 快速开始 — 三选一

### 路径 A — 一键启动（最快）

```bash
git clone git@github.com:neo-banker/neobanker-dev-env.git
cd neobanker-dev-env
bash scripts/bootstrap.sh
```

**会发生什么**（首次约 5–15 分钟）:
1. 检查前置工具
2. clone 3 个服务子仓到 `./repos/`
3. `docker compose up` 启动 MySQL + Redis + Elasticsearch
4. 用 `templates/env/` 模板补 `.env` 文件
5. `npm install` + `uv sync`（仅首次）
6. 启动 Spring Boot 后端，环境变量覆盖跨容器网络
7. 导入 CSV 数据（约 30 秒）
8. 把 575 家公司批量灌进 Elasticsearch
9. seed `search_logs` 让首页热搜 chip 有内容
10. 启动 Agent + 前端
11. 打印健康表 + 就绪 URL

### 路径 B — 手动一步步装（理解每一步）

参见 [`docs/manual-install.md`](docs/manual-install.md) — 所有命令逐条列出。

### 路径 C — 让 AI Agent 帮你装

在 Claude Code / Copilot CLI / Cursor 里说：

> 读 `https://github.com/neo-banker/neobanker-dev-env/blob/main/docs/ai-agent-prompt.md` 然后照里面步骤帮我搭起 Neobanker 本地开发环境。

prompt 文件是给 AI 友好的精简版配方。详见 [`docs/ai-agent-prompt.md`](docs/ai-agent-prompt.md)。

---

## 常用操作

```bash
bash scripts/bootstrap.sh                  # 完整启动
bash scripts/bootstrap.sh --infra-only     # 只启 docker compose，不跑应用
bash scripts/bootstrap.sh --skip-clone     # repos/ 已有就跳过 clone
bash scripts/bootstrap.sh --branch chatbot # clone chatbot 分支而非 main

bash scripts/teardown.sh                   # 停应用 + 容器（保留数据 volume）
bash scripts/teardown.sh -v                # 同时删 volume（下次 bootstrap 会重新导数据）
```

---

## 文档地图

| 文档 | 何时读 |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | 想了解容器拓扑、端口、容器互访方式 |
| [`docs/database-schema.md`](docs/database-schema.md) / [`.html`](docs/database-schema.html) | 想知道 65 张 MySQL 表里哪 11 张真正有数据，以及它们怎么关联 |
| [`docs/troubleshooting.md`](docs/troubleshooting.md) | 启动出错了 — 16 个已知问题 + 修复方案 |
| [`docs/verification-handbook.md`](docs/verification-handbook.md) | 想验证 L1（进程）/ L2（数据库）/ L3（前后端集成）/ L4（ES 搜索）每层都对 |
| [`docs/ai-agent-prompt.md`](docs/ai-agent-prompt.md) | 想让 AI 编程 agent 帮你跑 bootstrap |
| [`docs/manual-install.md`](docs/manual-install.md) | 想理解每一步，或一键脚本失败时排查 |

---

## 仓库结构

```
neobanker-dev-env/
├── docker/
│   └── docker-compose.yml        # MySQL/Redis/ES + 可选 MinIO/phpMyAdmin
├── scripts/
│   ├── bootstrap.sh              # 一键启动
│   └── teardown.sh               # 一键关闭
├── templates/env/                # 各服务 .env 模板（bootstrap 拷给对应仓）
│   ├── frontend.env.example
│   ├── backend.env.example
│   ├── agent.env.example
│   └── dependencies.env.example
├── docs/                         # 全部文档
├── repos/                        # clone 出来的服务子仓（gitignored）
└── .runtime/                     # 日志 + pid（gitignored）
```

---

## 已知限制

- **Clerk 认证**：dev 用的是 CI 占位 publishable key，登录按钮点了无反应。匿名浏览不影响。需要真 key 去 https://dashboard.clerk.com 注册。
- **部分第三方 CDN logo**（Wikimedia 等）可能因上游 URL 变更而 404 — 详见 `docs/troubleshooting.md` §9。
- **`/es/*` reindex 接口** 需要 JWT — bootstrap 绕过这个，直接写 ES。
- **macOS / Windows** 完整 bootstrap（含后端运行）只能 **手动** — CI 在这两个平台只验证 infra-only。

---

## 贡献

Fork → branch → PR。在 PR 标题加 `[full-ci]` 触发 Linux 完整 bootstrap CI 任务。

---

## 许可

内部 — 仅 Neobanker 团队使用。
