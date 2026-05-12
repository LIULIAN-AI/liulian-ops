from __future__ import annotations

import shutil
from dataclasses import dataclass, field

import httpx

from neoctl.ssh import SSHClient


@dataclass
class ConnectivityResult:
    reachable: bool
    backend: str = ""
    url: str = ""
    models: list[str] = field(default_factory=list)
    method: str = ""
    attempted_urls: list[str] = field(default_factory=list)
    next_step: str = ""


@dataclass
class GpuServerState:
    ssh_ok: bool = False
    llm_running: bool = False
    llm_installed: bool = False
    gpu_available: bool = False
    gpu_name: str = ""
    gpu_vram_mb: int = 0
    internet_available: bool = False
    backend_detected: str = ""


def _safe_json(response: httpx.Response) -> dict:
    try:
        parsed = response.json()
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def probe_llm_endpoint(base_url: str, timeout: float = 5.0) -> ConnectivityResult:
    root = base_url.rstrip("/")

    try:
        response = httpx.get(f"{root}/api/tags", timeout=timeout)
    except httpx.RequestError:
        response = None
    if response is not None and response.status_code == 200:
        payload = _safe_json(response)
        models = [model.get("name", "") for model in payload.get("models", []) if isinstance(model, dict)]
        return ConnectivityResult(reachable=True, backend="ollama", url=root, models=[m for m in models if m])

    try:
        response = httpx.get(f"{root}/v1/models", timeout=timeout)
    except httpx.RequestError:
        response = None
    if response is not None and response.status_code == 200:
        payload = _safe_json(response)
        models = [model.get("id", "") for model in payload.get("data", []) if isinstance(model, dict)]
        return ConnectivityResult(reachable=True, backend="vllm", url=root, models=[m for m in models if m])

    return ConnectivityResult(reachable=False)


def _backend_port(cfg: dict) -> int:
    backend = cfg["llm"]["local"].get("backend", "ollama")
    gpu_cfg = cfg["servers"]["gpu"]
    if backend == "vllm":
        return int(gpu_cfg.get("vllm_port", 38000))
    return int(gpu_cfg.get("ollama_port", 37434))


def _connectivity_targets(cfg: dict) -> list[tuple[str, str]]:
    gpu_cfg = cfg["servers"]["gpu"]
    conn_cfg = cfg["llm"]["connectivity"]
    backend_port = _backend_port(cfg)
    targets: list[tuple[str, str]] = []

    gpu_host = str(gpu_cfg.get("host", "")).strip()
    if gpu_host:
        targets.append(("direct", f"http://{gpu_host}:{backend_port}"))

    local_port = int(conn_cfg.get("local_port", 0) or 0)
    forward_port = local_port if local_port else backend_port
    targets.append(("forward_tunnel", f"http://localhost:{forward_port}"))

    return targets


def detect_connectivity(cfg: dict) -> ConnectivityResult:
    attempted: list[str] = []

    for method, url in _connectivity_targets(cfg):
        if url in attempted:
            continue
        attempted.append(url)
        probed = probe_llm_endpoint(url)
        if probed.reachable:
            probed.method = method
            probed.attempted_urls = attempted.copy()
            return probed

    return ConnectivityResult(
        reachable=False,
        attempted_urls=attempted,
        next_step="reverse_tunnel_doc_only",
    )


def detect_gpu_server_state(
    ssh: SSHClient,
    backend: str = "ollama",
    ollama_port: int = 37434,
    vllm_port: int = 38000,
) -> GpuServerState:
    state = GpuServerState(ssh_ok=ssh.can_connect())
    if not state.ssh_ok:
        return state

    if backend == "vllm":
        running = ssh.run(
            f"curl -s --connect-timeout 3 -o /dev/null -w '%{{http_code}}' http://localhost:{vllm_port}/v1/models"
        )
        if running.ok and running.stdout.strip() == "200":
            state.llm_running = True
            state.backend_detected = "vllm"
        else:
            installed = ssh.run("python3 -c 'import vllm' 2>/dev/null")
            state.llm_installed = installed.ok
            if installed.ok:
                state.backend_detected = "vllm"
    else:
        running = ssh.run(
            f"curl -s --connect-timeout 3 -o /dev/null -w '%{{http_code}}' http://localhost:{ollama_port}/api/tags"
        )
        if running.ok and running.stdout.strip() == "200":
            state.llm_running = True
            state.backend_detected = "ollama"
        else:
            installed = ssh.run("which ollama")
            state.llm_installed = installed.ok
            if installed.ok:
                state.backend_detected = "ollama"

    gpu = ssh.run("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null")
    if gpu.ok and gpu.stdout.strip():
        first_line = gpu.stdout.splitlines()[0]
        parts = [part.strip() for part in first_line.split(",", maxsplit=1)]
        if parts:
            state.gpu_available = True
            state.gpu_name = parts[0]
        if len(parts) == 2:
            try:
                state.gpu_vram_mb = int(parts[1])
            except ValueError:
                state.gpu_vram_mb = 0

    internet = ssh.run("curl -s --connect-timeout 5 -o /dev/null -w '%{http_code}' https://ollama.com")
    state.internet_available = internet.ok and internet.stdout.strip() in {"200", "301", "302"}

    return state


def detect_local_tools() -> dict[str, bool]:
    tools = [
        "ssh",
        "autossh",
        "scp",
        "curl",
        "uv",
        "git",
        "gh",
        "docker",
        "node",
        "npm",
        "python3",
    ]
    return {tool: shutil.which(tool) is not None for tool in tools}
