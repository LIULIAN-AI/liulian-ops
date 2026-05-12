from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from neoctl.detect import detect_connectivity
from neoctl.tunnel import check_tunnel_process


@dataclass
class ServiceStatus:
    name: str
    ok: bool
    detail: str = ""


def check_service_http(name: str, url: str, timeout: float = 5.0) -> ServiceStatus:
    try:
        response = httpx.get(url, timeout=timeout)
    except httpx.RequestError as exc:
        return ServiceStatus(name=name, ok=False, detail=str(exc))

    ok = 200 <= response.status_code < 400
    return ServiceStatus(name=name, ok=ok, detail=f"HTTP {response.status_code}")


def _resolve_tunnel_port(cfg: dict) -> int:
    port = int(cfg["llm"]["connectivity"].get("local_port", 0) or 0)
    if port:
        return port
    backend = cfg["llm"]["local"].get("backend", "ollama")
    if backend == "vllm":
        return int(cfg["servers"]["gpu"].get("vllm_port", 38000))
    return int(cfg["servers"]["gpu"].get("ollama_port", 37434))


def check_all_services(cfg: dict) -> list[ServiceStatus]:
    results: list[ServiceStatus] = []

    results.append(check_service_http("Backend (Spring Boot :8080)", "http://localhost:8080/homepage/hot-search-words"))
    results.append(check_service_http("Frontend (Next.js :3000)", "http://localhost:3000/homepage"))
    results.append(check_service_http("Agent (FastAPI :8000)", "http://localhost:8000/health"))

    connectivity = detect_connectivity(cfg)
    if connectivity.reachable:
        detail = f"{connectivity.backend} reachable via {connectivity.method} at {connectivity.url}"
        if connectivity.models:
            detail += f" (models: {', '.join(connectivity.models)})"
        results.append(ServiceStatus(name="LLM Connectivity", ok=True, detail=detail))
    else:
        detail = "No LLM endpoint reachable."
        if connectivity.next_step == "reverse_tunnel_doc_only":
            detail += " Next step: reverse tunnel docs only (manual setup)."
        if connectivity.attempted_urls:
            detail += f" Tried: {', '.join(connectivity.attempted_urls)}"
        results.append(ServiceStatus(name="LLM Connectivity", ok=False, detail=detail))

    tunnel = check_tunnel_process(_resolve_tunnel_port(cfg))
    if tunnel:
        results.append(
            ServiceStatus(
                name=f"SSH Tunnel (PID {tunnel.pid})",
                ok=True,
                detail=f"{tunnel.tunnel_type} tunnel on port {tunnel.local_port}",
            )
        )

    for provider in cfg["llm"].get("cloud", []):
        provider_name = provider.get("name", "unknown")
        enabled = bool(provider.get("enabled", False))
        key_env = provider.get("api_key_env", "")
        has_key = bool(key_env and os.environ.get(key_env))
        if not enabled:
            results.append(ServiceStatus(name=f"Cloud: {provider_name}", ok=False, detail="disabled"))
        elif has_key:
            results.append(ServiceStatus(name=f"Cloud: {provider_name}", ok=True, detail="configured (key set)"))
        else:
            results.append(ServiceStatus(name=f"Cloud: {provider_name}", ok=False, detail=f"no key ({key_env})"))

    return results
