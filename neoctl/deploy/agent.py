from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from neoctl.ssh import SSHClient


@dataclass
class AgentDeployResult:
    success: bool
    steps: list[str] = field(default_factory=list)
    error: str = ""
    commands: list[str] = field(default_factory=list)


def _run_steps(ssh: SSHClient, steps: list[tuple[str, str, int]], dry_run: bool) -> AgentDeployResult:
    completed: list[str] = []
    commands = [command for _, command, _ in steps]

    if dry_run:
        return AgentDeployResult(success=True, steps=[name for name, _, _ in steps], commands=commands)

    for step_name, command, timeout in steps:
        result = ssh.run(command, timeout=timeout)
        if not result.ok:
            detail = result.stderr.strip() or result.stdout.strip() or f"{step_name} failed"
            return AgentDeployResult(success=False, steps=completed, error=detail, commands=commands)
        completed.append(step_name)

    return AgentDeployResult(success=True, steps=completed, commands=commands)


def deploy_agent(
    ssh: SSHClient,
    deploy_path: str,
    branch: str = "main",
    service_name: str = "liulian-agent",
    dry_run: bool = False,
) -> AgentDeployResult:
    path = shlex.quote(deploy_path)
    branch_ref = shlex.quote(f"origin/{branch}")
    service = shlex.quote(service_name)

    steps = [
        ("git fetch", f"cd {path} && git fetch origin", 60),
        ("git reset", f"cd {path} && git reset --hard {branch_ref}", 60),
        ("uv sync", f"cd {path} && uv sync", 180),
        ("pytest", f"cd {path} && uv run pytest -q", 300),
        (
            "service restart + health",
            f"sudo systemctl restart {service} && sleep 3 && curl -fsS http://localhost:8000/health",
            45,
        ),
    ]

    return _run_steps(ssh, steps, dry_run=dry_run)
