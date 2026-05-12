from __future__ import annotations

import httpx

from neoctl.detect import ConnectivityResult
from neoctl.doctor import ServiceStatus, check_all_services, check_service_http
from neoctl.tunnel import TunnelInfo


def _cfg() -> dict:
    return {
        "servers": {
            "gpu": {
                "host": "10.0.0.12",
                "ollama_port": 37434,
                "vllm_port": 38000,
            }
        },
        "llm": {
            "local": {
                "backend": "ollama",
                "base_url": "",
            },
            "connectivity": {
                "method": "auto",
                "local_port": 40100,
                "priority": ["direct", "forward_tunnel", "reverse_tunnel"],
            },
            "cloud": [
                {"name": "gemini", "enabled": True, "api_key_env": "GEMINI_API_KEY"},
                {"name": "openai", "enabled": True, "api_key_env": "OPENAI_API_KEY"},
                {"name": "openrouter", "enabled": False, "api_key_env": "OPENROUTER_API_KEY"},
            ],
        },
    }


def test_check_service_http_ok(monkeypatch):
    monkeypatch.setattr(
        "neoctl.doctor.httpx.get",
        lambda *_args, **_kwargs: type("Resp", (), {"status_code": 204})(),
    )

    status = check_service_http("Backend", "http://localhost:8080/health")
    assert status == ServiceStatus(name="Backend", ok=True, detail="HTTP 204")


def test_check_service_http_404_is_failure(monkeypatch):
    monkeypatch.setattr(
        "neoctl.doctor.httpx.get",
        lambda *_args, **_kwargs: type("Resp", (), {"status_code": 404})(),
    )

    status = check_service_http("Backend", "http://localhost:8080/health")
    assert status == ServiceStatus(name="Backend", ok=False, detail="HTTP 404")


def test_check_service_http_failure(monkeypatch):
    def fake_get(url: str, timeout: float):  # noqa: ARG001
        raise httpx.RequestError("connection refused", request=httpx.Request("GET", url))

    monkeypatch.setattr("neoctl.doctor.httpx.get", fake_get)

    status = check_service_http("Backend", "http://localhost:8080/health")
    assert status.ok is False
    assert "connection refused" in status.detail


def test_check_all_services_aggregates_expected_sections(monkeypatch):
    monkeypatch.setattr(
        "neoctl.doctor.check_service_http",
        lambda name, *_args, **_kwargs: ServiceStatus(name=name, ok=True, detail="ok"),
    )
    monkeypatch.setattr(
        "neoctl.doctor.detect_connectivity",
        lambda _cfg: ConnectivityResult(
            reachable=True,
            backend="ollama",
            url="http://10.0.0.12:37434",
            models=["qwen3.5:9b"],
            method="direct",
            attempted_urls=["http://10.0.0.12:37434"],
        ),
    )
    monkeypatch.setattr(
        "neoctl.doctor.check_tunnel_process",
        lambda _local_port: TunnelInfo(pid=777, local_port=40100, tunnel_type="forward"),
    )
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    results = check_all_services(_cfg())
    by_name = {entry.name: entry for entry in results}

    assert by_name["LLM Connectivity"].ok is True
    assert "direct" in by_name["LLM Connectivity"].detail
    assert by_name["SSH Tunnel (PID 777)"].ok is True
    assert by_name["Cloud: gemini"].ok is True
    assert by_name["Cloud: openai"].ok is False
    assert "OPENAI_API_KEY" in by_name["Cloud: openai"].detail
    assert by_name["Cloud: openrouter"].ok is False
    assert by_name["Cloud: openrouter"].detail == "disabled"


def test_check_all_services_reports_reverse_as_docs_only_when_unreachable(monkeypatch):
    monkeypatch.setattr(
        "neoctl.doctor.check_service_http",
        lambda name, *_args, **_kwargs: ServiceStatus(name=name, ok=False, detail="down"),
    )
    monkeypatch.setattr(
        "neoctl.doctor.detect_connectivity",
        lambda _cfg: ConnectivityResult(
            reachable=False,
            attempted_urls=["http://10.0.0.12:37434", "http://localhost:40100"],
            next_step="reverse_tunnel_doc_only",
        ),
    )
    monkeypatch.setattr("neoctl.doctor.check_tunnel_process", lambda _local_port: None)

    results = check_all_services(_cfg())
    llm_status = next(item for item in results if item.name == "LLM Connectivity")

    assert llm_status.ok is False
    assert "reverse tunnel docs only" in llm_status.detail.lower()
