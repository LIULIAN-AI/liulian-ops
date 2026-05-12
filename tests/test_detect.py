from __future__ import annotations

from unittest.mock import MagicMock

from neoctl.detect import (
    ConnectivityResult,
    detect_connectivity,
    detect_gpu_server_state,
    detect_local_tools,
    probe_llm_endpoint,
)
from neoctl.ssh import SSHResult


def _cfg(
    *,
    backend: str = "ollama",
    gpu_host: str = "10.0.0.9",
    local_port: int = 40100,
    base_url: str = "",
) -> dict:
    return {
        "servers": {
            "gpu": {
                "host": gpu_host,
                "ollama_port": 37434,
                "vllm_port": 38000,
            }
        },
        "llm": {
            "local": {
                "backend": backend,
                "base_url": base_url,
            },
            "connectivity": {
                "method": "auto",
                "priority": ["direct", "forward_tunnel", "reverse_tunnel"],
                "local_port": local_port,
            },
        },
    }


def test_probe_llm_endpoint_detects_ollama(monkeypatch):
    calls: list[str] = []

    def fake_get(url: str, timeout: float):  # noqa: ARG001
        calls.append(url)
        return MagicMock(status_code=200, json=lambda: {"models": [{"name": "qwen3.5:9b"}]})

    monkeypatch.setattr("neoctl.detect.httpx.get", fake_get)

    result = probe_llm_endpoint("http://localhost:37434")

    assert result.reachable is True
    assert result.backend == "ollama"
    assert result.models == ["qwen3.5:9b"]
    assert calls == ["http://localhost:37434/api/tags"]


def test_probe_llm_endpoint_falls_back_to_vllm(monkeypatch):
    calls: list[str] = []

    def fake_get(url: str, timeout: float):  # noqa: ARG001
        calls.append(url)
        if url.endswith("/api/tags"):
            return MagicMock(status_code=404)
        return MagicMock(status_code=200, json=lambda: {"data": [{"id": "qwen3.5:9b"}]})

    monkeypatch.setattr("neoctl.detect.httpx.get", fake_get)

    result = probe_llm_endpoint("http://localhost:38000")

    assert result.reachable is True
    assert result.backend == "vllm"
    assert result.models == ["qwen3.5:9b"]
    assert calls == [
        "http://localhost:38000/api/tags",
        "http://localhost:38000/v1/models",
    ]


def test_detect_connectivity_uses_direct_before_forward(monkeypatch):
    calls: list[str] = []
    cfg = _cfg(local_port=40123)

    def fake_probe(url: str, timeout: float = 5.0):  # noqa: ARG001
        calls.append(url)
        if url == "http://10.0.0.9:37434":
            return ConnectivityResult(
                reachable=True,
                backend="ollama",
                url=url,
                models=["qwen3.5:9b"],
            )
        return ConnectivityResult(reachable=False)

    monkeypatch.setattr("neoctl.detect.probe_llm_endpoint", fake_probe)

    result = detect_connectivity(cfg)

    assert result.reachable is True
    assert result.method == "direct"
    assert calls == ["http://10.0.0.9:37434"]
    assert result.attempted_urls == ["http://10.0.0.9:37434"]


def test_detect_connectivity_falls_back_to_forward_tunnel(monkeypatch):
    calls: list[str] = []
    cfg = _cfg(local_port=40123)

    def fake_probe(url: str, timeout: float = 5.0):  # noqa: ARG001
        calls.append(url)
        if url == "http://localhost:40123":
            return ConnectivityResult(
                reachable=True,
                backend="ollama",
                url=url,
                models=["qwen3.5:9b"],
            )
        return ConnectivityResult(reachable=False)

    monkeypatch.setattr("neoctl.detect.probe_llm_endpoint", fake_probe)

    result = detect_connectivity(cfg)

    assert result.reachable is True
    assert result.method == "forward_tunnel"
    assert calls == [
        "http://10.0.0.9:37434",
        "http://localhost:40123",
    ]
    assert result.attempted_urls == calls


def test_detect_connectivity_uses_direct_forward_even_with_configured_url(monkeypatch):
    calls: list[str] = []
    cfg = _cfg(base_url="http://127.0.0.1:39999", local_port=40123)

    def fake_probe(url: str, timeout: float = 5.0):  # noqa: ARG001
        calls.append(url)
        if url == "http://localhost:40123":
            return ConnectivityResult(
                reachable=True,
                backend="ollama",
                url=url,
                models=["qwen3.5:9b"],
            )
        return ConnectivityResult(reachable=False)

    monkeypatch.setattr("neoctl.detect.probe_llm_endpoint", fake_probe)

    result = detect_connectivity(cfg)

    assert result.reachable is True
    assert result.method == "forward_tunnel"
    assert calls == [
        "http://10.0.0.9:37434",
        "http://localhost:40123",
    ]
    assert result.attempted_urls == calls


def test_detect_connectivity_reachable_configured_url_does_not_bypass_direct(monkeypatch):
    calls: list[str] = []
    cfg = _cfg(base_url="http://127.0.0.1:39999", local_port=40123)

    def fake_probe(url: str, timeout: float = 5.0):  # noqa: ARG001
        calls.append(url)
        if url in {"http://10.0.0.9:37434", "http://127.0.0.1:39999"}:
            return ConnectivityResult(
                reachable=True,
                backend="ollama",
                url=url,
                models=["qwen3.5:9b"],
            )
        return ConnectivityResult(reachable=False)

    monkeypatch.setattr("neoctl.detect.probe_llm_endpoint", fake_probe)

    result = detect_connectivity(cfg)

    assert result.reachable is True
    assert result.method == "direct"
    assert calls == ["http://10.0.0.9:37434"]
    assert result.attempted_urls == calls


def test_detect_connectivity_returns_reverse_doc_only_hint(monkeypatch):
    calls: list[str] = []
    cfg = _cfg(local_port=40123)

    def fake_probe(url: str, timeout: float = 5.0):  # noqa: ARG001
        calls.append(url)
        return ConnectivityResult(reachable=False)

    monkeypatch.setattr("neoctl.detect.probe_llm_endpoint", fake_probe)

    result = detect_connectivity(cfg)

    assert result.reachable is False
    assert result.next_step == "reverse_tunnel_doc_only"
    assert calls == [
        "http://10.0.0.9:37434",
        "http://localhost:40123",
    ]


def test_detect_gpu_server_state_when_ollama_running():
    ssh = MagicMock()
    ssh.can_connect.return_value = True
    ssh.run.side_effect = [
        SSHResult(exit_code=0, stdout="200", stderr=""),
        SSHResult(exit_code=0, stdout="NVIDIA L4, 22528\n", stderr=""),
        SSHResult(exit_code=0, stdout="200", stderr=""),
    ]

    state = detect_gpu_server_state(ssh, backend="ollama", ollama_port=37434, vllm_port=38000)

    assert state.ssh_ok is True
    assert state.llm_running is True
    assert state.backend_detected == "ollama"
    assert state.gpu_available is True
    assert state.gpu_name == "NVIDIA L4"
    assert state.gpu_vram_mb == 22528
    assert state.internet_available is True


def test_detect_gpu_server_state_ollama_non_200_is_not_running():
    ssh = MagicMock()
    ssh.can_connect.return_value = True
    ssh.run.side_effect = [
        SSHResult(exit_code=0, stdout="404", stderr=""),
        SSHResult(exit_code=0, stdout="/usr/bin/ollama\n", stderr=""),
        SSHResult(exit_code=0, stdout="", stderr=""),
        SSHResult(exit_code=0, stdout="200", stderr=""),
    ]

    state = detect_gpu_server_state(ssh, backend="ollama", ollama_port=37434, vllm_port=38000)

    assert state.ssh_ok is True
    assert state.llm_running is False
    assert state.llm_installed is True
    assert state.backend_detected == "ollama"


def test_detect_gpu_server_state_vllm_non_200_is_not_running():
    ssh = MagicMock()
    ssh.can_connect.return_value = True
    ssh.run.side_effect = [
        SSHResult(exit_code=0, stdout="503", stderr=""),
        SSHResult(exit_code=0, stdout="", stderr=""),
        SSHResult(exit_code=0, stdout="", stderr=""),
        SSHResult(exit_code=0, stdout="200", stderr=""),
    ]

    state = detect_gpu_server_state(ssh, backend="vllm", ollama_port=37434, vllm_port=38000)

    assert state.ssh_ok is True
    assert state.llm_running is False
    assert state.llm_installed is True
    assert state.backend_detected == "vllm"


def test_detect_gpu_server_state_when_ssh_unavailable():
    ssh = MagicMock()
    ssh.can_connect.return_value = False

    state = detect_gpu_server_state(ssh, backend="ollama", ollama_port=37434, vllm_port=38000)

    assert state.ssh_ok is False
    assert state.llm_running is False
    assert state.llm_installed is False
    assert state.gpu_available is False
    assert state.internet_available is False
    ssh.run.assert_not_called()


def test_detect_local_tools_reports_bool_map(monkeypatch):
    monkeypatch.setattr(
        "neoctl.detect.shutil.which",
        lambda tool: "/usr/bin/ssh" if tool in {"ssh", "curl"} else None,
    )

    result = detect_local_tools()

    assert result["ssh"] is True
    assert result["curl"] is True
    assert result["autossh"] is False
