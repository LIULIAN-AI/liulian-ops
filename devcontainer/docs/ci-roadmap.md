# CI Roadmap

What CI verifies today, what it doesn't, and what would take to close the gap.

---

## Today's CI matrix

| Workflow | What it actually does | Run cost | Trigger |
|---|---|---|---|
| `lint.yml` | bash syntax + env template format + `docker compose config -q` (Linux only) | ~16s × 3 OSes = 48s | every push |
| `infra.yml` | `bootstrap.sh --infra-only` → MySQL + Redis + Elasticsearch containers up, ping each | ~50s on Linux | every push |
| `full.yml` | **Disabled.** Would clone 3 service repos + start backend/agent/frontend + end-to-end smoke test | est. 12-15 min | manual `workflow_dispatch` or `[full-ci]` PR label |

## What `--infra-only` ≠ Full

| Aspect | `--infra-only` | Full bootstrap |
|---|---|---|
| MySQL container | ✅ start, ping | ✅ start, ping, **import 38k rows from CSV**, ✅ verify `SELECT COUNT(*) FROM company = 555` |
| Redis container | ✅ start, ping | ✅ same + backend connects |
| Elasticsearch container | ✅ start, cluster green | ✅ same + **bulk-load 575 companies** + verify search returns hits |
| Backend Spring Boot | ❌ not started | ✅ Maven downloads ~300MB deps, app starts on :8080, JPA creates 65 tables, `/actuator/health` reports all 5 components UP |
| Frontend Next.js | ❌ not started | ✅ npm install, `npm run dev` on :3000, hot reload server ready |
| Agent FastAPI | ❌ not started | ✅ uv sync, `uv run uvicorn` on :8000 |
| **Cross-stack integration** | ❌ never tested | ✅ frontend → backend `/homepage/getBanksStatistics` returns real data, `/homepage/esSearch?keyword=ZA` returns 3 hits |
| Time | ~50s | ~12-15 min |

**Summary**: `--infra-only` is "the docker layer is healthy". Full is "a developer running this on a fresh machine would actually see banks on the homepage".

---

## Gap to close

To make Full CI run automatically, we need:

### 1. Three GitHub PAT secrets

The 3 service repos (`neobanker-frontend-MVP-V3`, `neobanker-backend-MVP-V2`, `neobanker-agent`) are **private**. CI runners need credentials to clone them.

**Option A — single PAT with read access to all 3**
```bash
# In GitHub: Settings > Developer settings > Personal access tokens > Fine-grained
# - Repository access: select these 3 repos only
# - Permissions: Contents (Read)
# - Expiration: 1 year, set calendar reminder to rotate
```
Then add as a single secret:
```bash
gh secret set REPO_CLONE_TOKEN -R neo-banker/neobanker-dev-env
# (paste the PAT value)
```

**Option B — 3 separate deploy keys** (more secure, more setup)
- One ssh-keygen per repo
- Add public key to each repo's Settings > Deploy keys
- Add private key as `<REPO>_DEPLOY_KEY` secret in dev-env

### 2. Activate `full.yml`

In `.github/workflows/full.yml`, the backend smoke-test step has `if: false`. Once secret is configured:

```yaml
# Replace this:
- name: Backend smoke test (only if cloned)
  if: false  # enable when repo cloning is set up
  run: |
    curl -sS http://127.0.0.1:8080/actuator/health | grep -q '"db":{"status":"UP"'

# With this:
- name: Clone service repos
  env:
    GH_TOKEN: ${{ secrets.REPO_CLONE_TOKEN }}
  run: |
    git clone "https://${GH_TOKEN}@github.com/neo-banker/neobanker-frontend-MVP-V3.git" repos/neobanker-frontend-MVP-V3
    git clone "https://${GH_TOKEN}@github.com/neo-banker/neobanker-backend-MVP-V2.git" repos/neobanker-backend-MVP-V2
    git clone "https://${GH_TOKEN}@github.com/neo-banker/neobanker-agent.git" repos/neobanker-agent

- name: Full bootstrap
  run: bash scripts/bootstrap.sh --skip-clone

- name: End-to-end smoke test
  run: |
    curl -sS http://127.0.0.1:8080/actuator/health | python3 -c "
      import json, sys
      d = json.load(sys.stdin)
      assert d['components']['db']['status'] == 'UP', 'DB down'
      assert d['components']['elasticsearch']['status'] == 'UP', 'ES down'
      assert d['components']['redis']['status'] == 'UP', 'Redis down'
      print('all 3 backend deps UP')
    "
    curl -sS 'http://127.0.0.1:8080/homepage/esSearch?keyword=ZA&type=company&page=0&size=3' \
      | python3 -c "
        import json, sys
        d = json.load(sys.stdin)
        assert d['totalElements'] >= 1, 'ES search returned no hits'
        print(f'ES search OK — {d[\"totalElements\"]} hits')
      "
    curl -sI http://127.0.0.1:3000/homepage | grep -q 'HTTP/1.1 200' && echo 'frontend OK'
    curl -sS http://127.0.0.1:8000/health && echo 'agent OK'
```

### 3. Trigger frequency

| Trigger | Pros | Cons |
|---|---|---|
| Every `push` | Fast feedback | Burns 12-15 min × every push, GHA quota concern |
| Daily cron | Catches drift on dependencies | Slow signal for breaking PRs |
| `[full-ci]` PR label | Opt-in, cheap default | Easy to forget, breaks land on main |
| **Recommended**: weekly cron + manual `workflow_dispatch` | Cheap regular signal + on-demand | Doesn't gate every PR |

### 4. Estimated GHA cost

- Free tier: 2000 min/month for private repos
- Full job: ~15 min
- 30 PRs/month × 15 min = 450 min — **fits comfortably** in free tier
- Plus weekly cron: 4 × 15 = 60 min/month — negligible

---

## Cross-platform CI (mac/Windows)

Currently we only run lint on macOS/Windows. Reasons documented in
[`troubleshooting.md`](troubleshooting.md#17-why-cidocker-doesnt-work-on-macos--windows-gha-runners).

If we ever need real cross-platform CI:

| Provider | Cost | Notes |
|---|---|---|
| **GitHub larger macOS runner** | ~$0.16/min | Has nested virt, can run colima/Docker Desktop |
| **Namespace.so** | Free tier OK | Provides macOS docker, popular for OSS |
| **Cirrus CI** | Free tier | Same niche |
| **BuildJet** | Paid | Faster Linux runners + macOS |
| **Self-hosted runner on a team Mac mini** | Hardware cost only | One-time setup |

Not pursuing today — local Mac/Win developers run `bootstrap.sh` themselves on their real docker, which is the same code path.

---

## Status legend

- ✅ **CI** — Verified by automated test on every commit
- 🟡 **manual** — Works in practice but not automated
- ❌ **disabled** — Code exists but flag-gated off
- ⏳ **planned** — On this roadmap

---

## Decision log

| Date | Decision | Why |
|---|---|---|
| 2026-05-09 | Don't pursue mac/Win CI | Free GHA runners can't run docker; not worth paid runner cost |
| 2026-05-09 | Defer full-stack CI | Needs PAT secret config; user opted to do it later |
| 2026-05-09 | Keep lint on 3 OSes | Cheap (16s each) + catches Windows-specific path issues |
