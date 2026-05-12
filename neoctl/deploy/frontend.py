from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from neoctl.ssh import SSHClient


@dataclass
class FrontendDeployResult:
    success: bool
    steps: list[str] = field(default_factory=list)
    error: str = ""
    commands: list[str] = field(default_factory=list)


def _run_steps(ssh: SSHClient, steps: list[tuple[str, str, int]], dry_run: bool) -> FrontendDeployResult:
    completed: list[str] = []
    commands = [command for _, command, _ in steps]

    if dry_run:
        return FrontendDeployResult(success=True, steps=[name for name, _, _ in steps], commands=commands)

    for step_name, command, timeout in steps:
        result = ssh.run(command, timeout=timeout)
        if not result.ok:
            detail = result.stderr.strip() or result.stdout.strip() or f"{step_name} failed"
            return FrontendDeployResult(success=False, steps=completed, error=detail, commands=commands)
        completed.append(step_name)

    return FrontendDeployResult(success=True, steps=completed, commands=commands)


def deploy_frontend(
    ssh: SSHClient,
    deploy_path: str,
    branch: str = "main",
    pm2_name: str = "neobanker-frontend-app",
    dry_run: bool = False,
) -> FrontendDeployResult:
    path = shlex.quote(deploy_path)
    branch_ref = shlex.quote(f"origin/{branch}")
    process_name = shlex.quote(pm2_name)

    steps = [
        ("git fetch", f"cd {path} && git fetch origin", 60),
        ("git reset", f"cd {path} && git reset --hard {branch_ref}", 60),
        ("npm ci", f"cd {path} && npm ci --prefer-offline", 180),
        ("npm build", f"cd {path} && npm run build", 240),
        ("pm2 restart", f"pm2 restart {process_name} && pm2 save", 60),
    ]

    return _run_steps(ssh, steps, dry_run=dry_run)
