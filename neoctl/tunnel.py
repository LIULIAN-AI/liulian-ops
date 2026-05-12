from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

import httpx


@dataclass
class TunnelInfo:
    pid: int
    local_port: int
    tunnel_type: str = ""


def _pick_ssh_binary() -> str:
    return "autossh" if shutil.which("autossh") else "ssh"


def _ssh_target(user: str, host: str) -> str:
    return f"{user}@{host}" if user else host


def build_forward_tunnel_command(
    gpu_host: str,
    gpu_port: int,
    gpu_user: str,
    gpu_key: str,
    local_port: int,
    remote_port: int,
) -> list[str]:
    binary = _pick_ssh_binary()
    cmd = [binary]
    if binary == "autossh":
        cmd.extend(["-M", "0"])
    cmd.extend(
        [
            "-f",
            "-N",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-L",
            f"{local_port}:localhost:{remote_port}",
            "-p",
            str(gpu_port),
        ]
    )
    if gpu_key:
        cmd.extend(["-i", gpu_key])
    cmd.append(_ssh_target(gpu_user, gpu_host))
    return cmd


def build_reverse_tunnel_command(
    app_host: str,
    app_port: int,
    app_user: str,
    app_key: str,
    tunnel_port: int,
    target_port: int,
) -> list[str]:
    binary = _pick_ssh_binary()
    cmd = [binary]
    if binary == "autossh":
        cmd.extend(["-M", "0"])
    cmd.extend(
        [
            "-f",
            "-N",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-o",
            "ExitOnForwardFailure=yes",
            "-R",
            f"{tunnel_port}:localhost:{target_port}",
            "-p",
            str(app_port),
        ]
    )
    if app_key:
        cmd.extend(["-i", app_key])
    cmd.append(_ssh_target(app_user, app_host))
    return cmd


def check_tunnel_process(local_port: int) -> TunnelInfo | None:
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        return None

    if result.returncode != 0:
        return None

    marker = f"{local_port}:localhost:"
    for line in result.stdout.splitlines():
        if marker not in line:
            continue
        if "ssh" not in line and "autossh" not in line:
            continue

        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue

        if f"-L {local_port}:" in line:
            return TunnelInfo(pid=pid, local_port=local_port, tunnel_type="forward")
        if f"-R {local_port}:" in line:
            return TunnelInfo(pid=pid, local_port=local_port, tunnel_type="reverse")

    return None


def start_forward_tunnel(
    gpu_host: str,
    gpu_port: int,
    gpu_user: str,
    gpu_key: str,
    local_port: int,
    remote_port: int,
) -> bool:
    existing = check_tunnel_process(local_port)
    if existing and existing.tunnel_type == "forward":
        return True

    cmd = build_forward_tunnel_command(
        gpu_host=gpu_host,
        gpu_port=gpu_port,
        gpu_user=gpu_user,
        gpu_key=gpu_key,
        local_port=local_port,
        remote_port=remote_port,
    )
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return False
    except FileNotFoundError:
        return False
    return result.returncode == 0


def check_direct_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    base = f"http://{host}:{port}"
    try:
        ollama = httpx.get(f"{base}/api/tags", timeout=timeout)
    except httpx.RequestError:
        ollama = None
    if ollama is not None and ollama.status_code == 200:
        return True

    try:
        vllm = httpx.get(f"{base}/v1/models", timeout=timeout)
    except httpx.RequestError:
        return False
    return vllm.status_code == 200
