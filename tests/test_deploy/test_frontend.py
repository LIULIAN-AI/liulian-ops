from __future__ import annotations

from unittest.mock import MagicMock

from click.testing import CliRunner

from neoctl.cli import main
from neoctl.deploy.frontend import FrontendDeployResult, deploy_frontend
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


def test_deploy_frontend_all_steps_succeed():
    ssh = MagicMock()
    ssh.run.return_value = SSHResult(exit_code=0, stdout="ok", stderr="")

    result = deploy_frontend(ssh, deploy_path="~/liulian/frontend", pm2_name="liulian-web-app")

    assert result.success is True
    assert len(result.steps) == 5


def test_deploy_frontend_npm_build_fails():
    ssh = MagicMock()
    ssh.run.side_effect = [
        SSHResult(exit_code=0, stdout="", stderr=""),
        SSHResult(exit_code=0, stdout="", stderr=""),
        SSHResult(exit_code=0, stdout="", stderr=""),
        SSHResult(exit_code=1, stdout="", stderr="Build error"),
    ]

    result = deploy_frontend(ssh, deploy_path="~/liulian/frontend", pm2_name="liulian-web-app")

    assert result.success is False
    assert "Build error" in result.error


def test_deploy_frontend_dry_run_assembles_safe_commands():
    ssh = MagicMock()

    result = deploy_frontend(
        ssh,
        deploy_path="~/neo banker/frontend",
        pm2_name="frontend;shutdown",
        dry_run=True,
    )

    assert result.success is True
    assert len(result.commands) == 5
    assert "cd '~/neo banker/frontend'" in result.commands[0]
    assert "pm2 restart 'frontend;shutdown'" in result.commands[4]
    ssh.run.assert_not_called()


def test_cli_deploy_frontend_routes_to_deployer(monkeypatch):
    monkeypatch.setattr("neoctl.cli._load_cfg", lambda: _cfg())
    app_ssh = object()
    monkeypatch.setattr("neoctl.cli._get_app_ssh", lambda _cfg: app_ssh)

    recorded: dict[str, object] = {}

    def fake_deploy_frontend(ssh, deploy_path, branch="main", pm2_name="liulian-web-app", dry_run=False):
        recorded["ssh"] = ssh
        recorded["deploy_path"] = deploy_path
        recorded["branch"] = branch
        recorded["pm2_name"] = pm2_name
        recorded["dry_run"] = dry_run
        return FrontendDeployResult(success=True, steps=["ok"])

    monkeypatch.setattr("neoctl.cli.deploy_frontend", fake_deploy_frontend)

    result = CliRunner().invoke(main, ["deploy", "frontend", "--dry-run"])

    assert result.exit_code == 0
    assert recorded["ssh"] is app_ssh
    assert recorded["deploy_path"] == "~/liulian/frontend"
    assert recorded["pm2_name"] == "liulian-web-app"
    assert recorded["dry_run"] is True


def test_cli_deploy_all_runs_in_defined_order(monkeypatch):
    monkeypatch.setattr("neoctl.cli._load_cfg", lambda: _cfg())
    monkeypatch.setattr("neoctl.cli._get_app_ssh", lambda _cfg: object())

    call_order: list[str] = []

    def fake_deploy_llm(_cfg: dict, dry_run: bool = False) -> bool:
        call_order.append("llm")
        return True

    def ok_result(step: str):
        return type("DeployResult", (), {"success": True, "steps": [step], "error": ""})()

    monkeypatch.setattr("neoctl.cli._deploy_llm", fake_deploy_llm)
    monkeypatch.setattr(
        "neoctl.cli.deploy_backend",
        lambda *_args, **_kwargs: (call_order.append("backend") or ok_result("backend")),
    )
    monkeypatch.setattr(
        "neoctl.cli.deploy_frontend",
        lambda *_args, **_kwargs: (call_order.append("frontend") or ok_result("frontend")),
    )
    monkeypatch.setattr(
        "neoctl.cli.deploy_agent",
        lambda *_args, **_kwargs: (call_order.append("agent") or ok_result("agent")),
    )

    result = CliRunner().invoke(main, ["deploy", "all", "--dry-run"])

    assert result.exit_code == 0
    assert call_order == ["llm", "backend", "frontend", "agent"]


def test_cli_deploy_all_uses_configured_runtime_names(monkeypatch):
    cfg = _cfg()
    cfg["deploy"]["services"]["backend"]["service_name"] = "custom-backend-service"
    cfg["deploy"]["services"]["frontend"]["pm2_name"] = "custom-frontend-process"
    cfg["deploy"]["services"]["agent"]["service_name"] = "custom-agent-service"

    app_ssh = object()
    monkeypatch.setattr("neoctl.cli._load_cfg", lambda: cfg)
    monkeypatch.setattr("neoctl.cli._get_app_ssh", lambda _cfg: app_ssh)
    monkeypatch.setattr("neoctl.cli._deploy_llm", lambda _cfg, dry_run=False: True)

    recorded: dict[str, object] = {}

    def ok_result(step: str):
        return type("DeployResult", (), {"success": True, "steps": [step], "error": ""})()

    def fake_deploy_backend(ssh, deploy_path, branch="main", service_name="liulian-api", dry_run=False):
        recorded["backend_ssh"] = ssh
        recorded["backend_service_name"] = service_name
        recorded["backend_dry_run"] = dry_run
        return ok_result("backend")

    def fake_deploy_frontend(ssh, deploy_path, branch="main", pm2_name="liulian-web-app", dry_run=False):
        recorded["frontend_ssh"] = ssh
        recorded["frontend_pm2_name"] = pm2_name
        recorded["frontend_dry_run"] = dry_run
        return ok_result("frontend")

    def fake_deploy_agent(ssh, deploy_path, branch="main", service_name="liulian-agent", dry_run=False):
        recorded["agent_ssh"] = ssh
        recorded["agent_service_name"] = service_name
        recorded["agent_dry_run"] = dry_run
        return ok_result("agent")

    monkeypatch.setattr("neoctl.cli.deploy_backend", fake_deploy_backend)
    monkeypatch.setattr("neoctl.cli.deploy_frontend", fake_deploy_frontend)
    monkeypatch.setattr("neoctl.cli.deploy_agent", fake_deploy_agent)

    result = CliRunner().invoke(main, ["deploy", "all", "--dry-run"])

    assert result.exit_code == 0
    assert recorded["backend_ssh"] is app_ssh
    assert recorded["frontend_ssh"] is app_ssh
    assert recorded["agent_ssh"] is app_ssh
    assert recorded["backend_service_name"] == "custom-backend-service"
    assert recorded["frontend_pm2_name"] == "custom-frontend-process"
    assert recorded["agent_service_name"] == "custom-agent-service"
    assert recorded["backend_dry_run"] is True
    assert recorded["frontend_dry_run"] is True
    assert recorded["agent_dry_run"] is True


def test_cli_deploy_all_stops_on_hard_failure(monkeypatch):
    monkeypatch.setattr("neoctl.cli._load_cfg", lambda: _cfg())
    monkeypatch.setattr("neoctl.cli._get_app_ssh", lambda _cfg: object())

    call_order: list[str] = []

    def fake_deploy_llm(_cfg: dict, dry_run: bool = False) -> bool:
        call_order.append("llm")
        return True

    def ok_result(step: str):
        return type("DeployResult", (), {"success": True, "steps": [step], "error": ""})()

    def fail_result(step: str):
        return type("DeployResult", (), {"success": False, "steps": [step], "error": "boom"})()

    monkeypatch.setattr("neoctl.cli._deploy_llm", fake_deploy_llm)
    monkeypatch.setattr(
        "neoctl.cli.deploy_backend",
        lambda *_args, **_kwargs: (call_order.append("backend") or ok_result("backend")),
    )
    monkeypatch.setattr(
        "neoctl.cli.deploy_frontend",
        lambda *_args, **_kwargs: (call_order.append("frontend") or fail_result("frontend")),
    )
    monkeypatch.setattr(
        "neoctl.cli.deploy_agent",
        lambda *_args, **_kwargs: (call_order.append("agent") or ok_result("agent")),
    )

    result = CliRunner().invoke(main, ["deploy", "all"])

    assert result.exit_code != 0
    assert call_order == ["llm", "backend", "frontend"]
