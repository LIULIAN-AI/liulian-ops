from __future__ import annotations

import shlex
from dataclasses import dataclass, field

from neoctl.ssh import SSHClient


@dataclass
class BackendDeployResult:
    success: bool
    steps: list[str] = field(default_factory=list)
    error: str = ""
    commands: list[str] = field(default_factory=list)


def _run_steps(ssh: SSHClient, steps: list[tuple[str, str, int]], dry_run: bool) -> BackendDeployResult:
    completed: list[str] = []
    commands = [command for _, command, _ in steps]

    if dry_run:
        return BackendDeployResult(success=True, steps=[name for name, _, _ in steps], commands=commands)

    for step_name, command, timeout in steps:
        result = ssh.run(command, timeout=timeout)
        if not result.ok:
            detail = result.stderr.strip() or result.stdout.strip() or f"{step_name} failed"
            return BackendDeployResult(success=False, steps=completed, error=detail, commands=commands)
        completed.append(step_name)

    return BackendDeployResult(success=True, steps=completed, commands=commands)


def deploy_backend(
    ssh: SSHClient,
    deploy_path: str,
    branch: str = "main",
    service_name: str = "liulian-api",
    dry_run: bool = False,
) -> BackendDeployResult:
    path = shlex.quote(deploy_path)
    branch_ref = shlex.quote(f"origin/{branch}")
    service = shlex.quote(service_name)

    steps = [
        ("git fetch", f"cd {path} && git fetch origin", 60),
        ("git reset", f"cd {path} && git reset --hard {branch_ref}", 60),
        (
            "maven package",
            " && ".join(
                [
                    f"cd {path}",
                    "export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64",
                    "./mvnw -B package -DskipTests --no-transfer-progress",
                ]
            ),
            300,
        ),
        ("service restart", f"sudo systemctl restart {service}", 60),
        (
            "health check",
            " && ".join(
                [
                    "sleep 5",
                    "STATUS=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/homepage/hot-search-words)",
                    "[ \"$STATUS\" = \"200\" ]",
                ]
            ),
            30,
        ),
    ]

    return _run_steps(ssh, steps, dry_run=dry_run)
