from __future__ import annotations

from unittest.mock import MagicMock

from click.testing import CliRunner

from neoctl.cli import main
from neoctl.deploy.backend import BackendDeployResult, deploy_backend
from neoctl.doctor import ServiceStatus
from neoctl.ssh import SSHResult


def _cfg() -> dict:
    return {
        "servers": {
            "app": {
                "host": "10.0.0.2",
                "ssh_port": 10022,
                "user": "deploy",
                "key_path": "~/.ssh/id_ed25519",
                "deploy_path": "~/neobanker",
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


def test_deploy_backend_all_steps_succeed():
    ssh = MagicMock()
    ssh.run.return_value = SSHResult(exit_code=0, stdout="ok", stderr="")

    result = deploy_backend(ssh, deploy_path="~/neobanker/backend", branch="main", service_name="neobanker-backend")

    assert result.success is True
    assert len(result.steps) == 5


def test_deploy_backend_build_fails():
    ssh = MagicMock()
    ssh.run.side_effect = [
        SSHResult(exit_code=0, stdout="", stderr=""),
        SSHResult(exit_code=0, stdout="", stderr=""),
        SSHResult(exit_code=1, stdout="", stderr="BUILD FAILURE"),
    ]

    result = deploy_backend(ssh, deploy_path="~/neobanker/backend", branch="main", service_name="neobanker-backend")

    assert result.success is False
    assert "BUILD FAILURE" in result.error


def test_deploy_backend_dry_run_assembles_safe_commands():
    ssh = MagicMock()

    result = deploy_backend(
        ssh,
        deploy_path="~/neo banker/backend",
        branch="main; echo hacked",
        service_name="neo;shutdown",
        dry_run=True,
    )

    assert result.success is True
    assert len(result.commands) == 5
    assert "cd '~/neo banker/backend'" in result.commands[0]
    assert "git reset --hard 'origin/main; echo hacked'" in result.commands[1]
    assert "systemctl restart 'neo;shutdown'" in result.commands[3]
    ssh.run.assert_not_called()


def test_cli_doctor_routes_to_checks(monkeypatch):
    monkeypatch.setattr("neoctl.cli._load_cfg", lambda: _cfg())
    calls = {"count": 0}

    def fake_check_all_services(_cfg: dict):
        calls["count"] += 1
        return [ServiceStatus(name="Backend", ok=True, detail="ok")]

    monkeypatch.setattr("neoctl.cli.check_all_services", fake_check_all_services)

    result = CliRunner().invoke(main, ["doctor"])

    assert result.exit_code == 0
    assert calls["count"] == 1
    assert "Backend" in result.output


def test_cli_deploy_backend_routes_to_deployer(monkeypatch):
    monkeypatch.setattr("neoctl.cli._load_cfg", lambda: _cfg())
    app_ssh = object()
    monkeypatch.setattr("neoctl.cli._get_app_ssh", lambda _cfg: app_ssh)

    recorded: dict[str, object] = {}

    def fake_deploy_backend(ssh, deploy_path, branch="main", service_name="neobanker-backend", dry_run=False):
        recorded["ssh"] = ssh
        recorded["deploy_path"] = deploy_path
        recorded["branch"] = branch
        recorded["service_name"] = service_name
        recorded["dry_run"] = dry_run
        return BackendDeployResult(success=True, steps=["ok"])

    monkeypatch.setattr("neoctl.cli.deploy_backend", fake_deploy_backend)

    result = CliRunner().invoke(main, ["deploy", "backend", "--dry-run"])

    assert result.exit_code == 0
    assert recorded["ssh"] is app_ssh
    assert recorded["deploy_path"] == "~/neobanker/backend"
    assert recorded["service_name"] == "neobanker-backend"
    assert recorded["dry_run"] is True
