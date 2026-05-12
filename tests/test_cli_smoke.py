from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from neoctl import __version__
from neoctl.cli import main
from neoctl.detect import ConnectivityResult
from neoctl.doctor import ServiceStatus

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _format_cmd(args: list[str], program: str = "neoctl") -> str:
    return f"{program} {' '.join(args)}".strip()


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _assert_markers(args: list[str], output: str, markers: list[str], *, program: str = "neoctl") -> None:
    command = _format_cmd(args, program=program)
    normalized_output = _normalize(output)
    for marker in markers:
        assert _normalize(marker) in normalized_output, f"{command} missing marker {marker!r}.\nOutput:\n{output}"


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


@pytest.mark.parametrize(
    ("args", "markers"),
    [
        (
            ["--help"],
            [
                "Usage: main [OPTIONS] COMMAND [ARGS]...",
                "Commands:",
                "deploy",
                "doctor",
            ],
        ),
        (["--version"], ["version", __version__]),
        (["doctor", "--help"], ["Usage: main doctor [OPTIONS]", "Run service health checks."]),
        (["deploy", "--help"], ["Usage: main deploy [OPTIONS] COMMAND [ARGS]...", "Commands:"]),
        (["deploy", "llm", "--help"], ["Usage: main deploy llm [OPTIONS]", "--dry-run"]),
        (["deploy", "backend", "--help"], ["Usage: main deploy backend [OPTIONS]", "--branch"]),
        (["deploy", "frontend", "--help"], ["Usage: main deploy frontend [OPTIONS]", "--pm2-name"]),
        (["deploy", "agent", "--help"], ["Usage: main deploy agent [OPTIONS]", "--service-name"]),
        (["deploy", "all", "--help"], ["Usage: main deploy all [OPTIONS]", "--dry-run"]),
    ],
)
def test_smoke_command_availability_and_help(args: list[str], markers: list[str]):
    result = CliRunner().invoke(main, args)

    command = _format_cmd(args)
    assert result.exit_code == 0, f"{command} exited with {result.exit_code}.\nOutput:\n{result.output}"
    _assert_markers(args, result.output, markers)


def test_packaged_entrypoint_help_surface() -> None:
    args = ["--help"]
    result = subprocess.run(
        ["uv", "run", "neoctl", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = f"{result.stdout}{result.stderr}"

    command = _format_cmd(args, program="uv run neoctl")
    assert result.returncode == 0, f"{command} exited with {result.returncode}.\nOutput:\n{output}"
    _assert_markers(
        args,
        output,
        [
            "Usage: neoctl [OPTIONS] COMMAND [ARGS]...",
            "Commands:",
            "deploy",
            "doctor",
        ],
        program="uv run neoctl",
    )


@pytest.fixture
def smoke_runtime_patches(monkeypatch):
    monkeypatch.setattr("neoctl.cli._load_cfg", lambda: _cfg())
    monkeypatch.setattr(
        "neoctl.cli.check_all_services",
        lambda _cfg: [
            ServiceStatus(name="Backend", ok=True, detail="ok"),
            ServiceStatus(name="LLM Connectivity", ok=True, detail="ok"),
        ],
    )
    monkeypatch.setattr(
        "neoctl.cli.detect_connectivity",
        lambda _cfg: ConnectivityResult(
            reachable=False,
            attempted_urls=["http://10.0.0.9:37434", "http://localhost:40100"],
            next_step="reverse_tunnel_doc_only",
        ),
    )
    monkeypatch.setattr("neoctl.cli._get_app_ssh", lambda _cfg: object())


@pytest.mark.parametrize(
    ("args", "markers"),
    [
        (
            ["doctor"],
            [
                "Status",
                "Service",
                "Backend",
                "LLM Connectivity",
                "2/2 checks passed",
            ],
        ),
        (
            ["deploy", "llm", "--dry-run"],
            ["LLM dry-run: would install/start on GPU and then try forward tunnel fallback."],
        ),
        (
            ["deploy", "backend", "--dry-run"],
            [
                "backend planned: git fetch, git reset, maven package, service restart, health check",
                "cd '~/neobanker/backend' && git fetch origin",
                "sudo systemctl restart neobanker-backend",
            ],
        ),
        (
            ["deploy", "frontend", "--dry-run"],
            [
                "frontend planned: git fetch, git reset, npm ci, npm build, pm2 restart",
                "npm ci --prefer-offline",
                "pm2 restart neobanker-frontend-app",
            ],
        ),
        (
            ["deploy", "agent", "--dry-run"],
            [
                "agent planned: git fetch, git reset, uv sync, pytest, service restart + health",
                "uv run pytest -q",
                "curl -fsS http://localhost:8000/health",
            ],
        ),
        (
            ["deploy", "all", "--dry-run"],
            [
                "LLM dry-run: would install/start on GPU and then try forward tunnel fallback.",
                "backend planned: git fetch, git reset, maven package, service restart, health check",
                "frontend planned: git fetch, git reset, npm ci, npm build, pm2 restart",
                "agent planned: git fetch, git reset, uv sync, pytest, service restart + health",
                "All deploy stages completed.",
            ],
        ),
    ],
)
def test_smoke_runtime_commands_do_not_crash(smoke_runtime_patches, args: list[str], markers: list[str]):
    result = CliRunner().invoke(main, args)

    command = _format_cmd(args)
    assert result.exit_code == 0, f"{command} exited with {result.exit_code}.\nOutput:\n{result.output}"
    _assert_markers(args, result.output, markers)
