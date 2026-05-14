from __future__ import annotations

from unittest.mock import MagicMock

from click.testing import CliRunner

from neoctl.cli import main
from neoctl.deploy.agent import AgentDeployResult, deploy_agent
from neoctl.detect import ConnectivityResult, GpuServerState
from neoctl.ssh import SSHResult


def _cfg() -> dict:
    return {
        "servers": {
            "app": {
                "host": "10.0.0.2",
                "ssh_port": 10022,
                "user": "deploy",
                "key_path": "~/.ssh/id_ed25519",
                "deploy_path": "~/liulian",
            },
            "gpu": {
                "host": "10.0.0.9",
                "ssh_port": 10022,
                "user": "gpu",
                "key_path": "~/.ssh/id_gpu",
                "ollama_port": 37434,
                "vllm_port": 38000,
                "model_storage": "/nfshdd/models",
            },
        },
        "llm": {
            "local": {
                "backend": "ollama",
                "base_url": "",
                "models": [{"name": "qwen3.5:9b"}],
            },
            "connectivity": {
                "method": "auto",
                "priority": ["direct", "forward_tunnel", "reverse_tunnel"],
                "local_port": 40100,
            },
            "cloud": [],
        },
        "deploy": {
            "services": {
                "backend": {"dir": "backend"},
                "frontend": {"dir": "frontend"},
                "agent": {"dir": "agent"},
            }
        },
    }


def test_deploy_agent_all_steps_succeed():
    ssh = MagicMock()
    ssh.run.return_value = SSHResult(exit_code=0, stdout="ok", stderr="")

    result = deploy_agent(ssh, deploy_path="~/liulian/agent", service_name="liulian-agent")

    assert result.success is True
    assert len(result.steps) == 5


def test_deploy_agent_test_fails():
    ssh = MagicMock()
    ssh.run.side_effect = [
        SSHResult(exit_code=0, stdout="", stderr=""),
        SSHResult(exit_code=0, stdout="", stderr=""),
        SSHResult(exit_code=0, stdout="", stderr=""),
        SSHResult(exit_code=1, stdout="", stderr="FAILED tests"),
    ]

    result = deploy_agent(ssh, deploy_path="~/liulian/agent", service_name="liulian-agent")

    assert result.success is False
    assert "FAILED" in result.error


def test_deploy_agent_dry_run_assembles_safe_commands():
    ssh = MagicMock()

    result = deploy_agent(
        ssh,
        deploy_path="~/neo banker/agent",
        service_name="agent;shutdown",
        dry_run=True,
    )

    assert result.success is True
    assert len(result.commands) == 5
    assert "cd '~/neo banker/agent'" in result.commands[0]
    assert "systemctl restart 'agent;shutdown'" in result.commands[4]
    ssh.run.assert_not_called()


def test_cli_deploy_agent_routes_to_deployer(monkeypatch):
    monkeypatch.setattr("neoctl.cli._load_cfg", lambda: _cfg())
    app_ssh = object()
    monkeypatch.setattr("neoctl.cli._get_app_ssh", lambda _cfg: app_ssh)

    recorded: dict[str, object] = {}

    def fake_deploy_agent(ssh, deploy_path, branch="main", service_name="liulian-agent", dry_run=False):
        recorded["ssh"] = ssh
        recorded["deploy_path"] = deploy_path
        recorded["branch"] = branch
        recorded["service_name"] = service_name
        recorded["dry_run"] = dry_run
        return AgentDeployResult(success=True, steps=["ok"])

    monkeypatch.setattr("neoctl.cli.deploy_agent", fake_deploy_agent)

    result = CliRunner().invoke(main, ["deploy", "agent", "--dry-run"])

    assert result.exit_code == 0
    assert recorded["ssh"] is app_ssh
    assert recorded["deploy_path"] == "~/liulian/agent"
    assert recorded["service_name"] == "liulian-agent"
    assert recorded["dry_run"] is True


def test_cli_deploy_llm_reports_direct_method_when_reachable(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr("neoctl.cli._load_cfg", lambda: cfg)
    monkeypatch.setattr("neoctl.cli._save_cfg", lambda _cfg: None)
    monkeypatch.setattr(
        "neoctl.cli.detect_connectivity",
        lambda _cfg: ConnectivityResult(
            reachable=True,
            backend="ollama",
            url="http://10.0.0.9:37434",
            method="direct",
            models=["qwen3.5:9b"],
            attempted_urls=["http://10.0.0.9:37434"],
        ),
    )

    install_called = {"value": False}
    monkeypatch.setattr("neoctl.cli.install_ollama", lambda *_args, **_kwargs: install_called.__setitem__("value", True))

    result = CliRunner().invoke(main, ["deploy", "llm"])

    assert result.exit_code == 0
    assert "direct" in result.output.lower()
    assert cfg["llm"]["local"]["base_url"] == "http://10.0.0.9:37434"
    assert install_called["value"] is False


def test_cli_deploy_llm_runs_setup_then_forward_fallback(monkeypatch):
    cfg = _cfg()
    monkeypatch.setattr("neoctl.cli._load_cfg", lambda: cfg)
    monkeypatch.setattr("neoctl.cli._save_cfg", lambda _cfg: None)

    detect_calls = {"count": 0}

    def fake_detect(_cfg: dict):
        detect_calls["count"] += 1
        if detect_calls["count"] < 3:
            return ConnectivityResult(
                reachable=False,
                attempted_urls=["http://10.0.0.9:37434", "http://localhost:40100"],
                next_step="reverse_tunnel_doc_only",
            )
        return ConnectivityResult(
            reachable=True,
            backend="ollama",
            url="http://localhost:40100",
            method="forward_tunnel",
            models=["qwen3.5:9b"],
            attempted_urls=["http://10.0.0.9:37434", "http://localhost:40100"],
        )

    monkeypatch.setattr("neoctl.cli.detect_connectivity", fake_detect)

    gpu_ssh = MagicMock()
    gpu_ssh.can_connect.return_value = True
    monkeypatch.setattr("neoctl.cli._get_gpu_ssh", lambda _cfg: gpu_ssh)
    monkeypatch.setattr(
        "neoctl.cli.detect_gpu_server_state",
        lambda *_args, **_kwargs: GpuServerState(
            ssh_ok=True,
            llm_running=False,
            llm_installed=False,
            gpu_available=True,
            gpu_name="NVIDIA L4",
            gpu_vram_mb=22528,
            internet_available=False,
            backend_detected="",
        ),
    )

    install_calls = {"count": 0}
    start_calls = {"count": 0}
    tunnel_calls = {"count": 0}

    def fake_install(*_args, **_kwargs):
        install_calls["count"] += 1
        return True

    def fake_start(*_args, **_kwargs):
        start_calls["count"] += 1
        return True

    def fake_tunnel(**_kwargs):
        tunnel_calls["count"] += 1
        return True

    monkeypatch.setattr("neoctl.cli.install_ollama", fake_install)
    monkeypatch.setattr("neoctl.cli.start_ollama", fake_start)
    monkeypatch.setattr("neoctl.cli.start_forward_tunnel", fake_tunnel)

    result = CliRunner().invoke(main, ["deploy", "llm"])

    assert result.exit_code == 0
    assert install_calls["count"] == 1
    assert start_calls["count"] == 1
    assert tunnel_calls["count"] == 1
    assert "forward" in result.output.lower()
