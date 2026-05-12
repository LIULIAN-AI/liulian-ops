from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass
class SSHResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass
class SSHClient:
    host: str
    port: int = 22
    user: str = ""
    key_path: str = ""
    connect_timeout: int = 10

    def _target(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    def _build_ssh_command(self, remote_cmd: str) -> list[str]:
        cmd = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={self.connect_timeout}",
            "-p",
            str(self.port),
        ]
        if self.key_path:
            cmd.extend(["-i", self.key_path])
        cmd.append(self._target())
        cmd.append(remote_cmd)
        return cmd

    def _build_scp_command(self, local_path: str, remote_path: str) -> list[str]:
        cmd = [
            "scp",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={self.connect_timeout}",
            "-P",
            str(self.port),
        ]
        if self.key_path:
            cmd.extend(["-i", self.key_path])
        cmd.append(local_path)
        cmd.append(f"{self._target()}:{remote_path}")
        return cmd

    def run(self, remote_cmd: str, timeout: int = 60) -> SSHResult:
        cmd = self._build_ssh_command(remote_cmd)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return SSHResult(exit_code=-1, stdout="", stderr=f"SSH command timed out after {timeout}s")
        except FileNotFoundError:
            return SSHResult(exit_code=-1, stdout="", stderr="ssh binary not found")
        return SSHResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def scp(self, local_path: str, remote_path: str, timeout: int = 300) -> SSHResult:
        cmd = self._build_scp_command(local_path, remote_path)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return SSHResult(exit_code=-1, stdout="", stderr=f"SCP command timed out after {timeout}s")
        except FileNotFoundError:
            return SSHResult(exit_code=-1, stdout="", stderr="scp binary not found")
        return SSHResult(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    def can_connect(self) -> bool:
        marker = "neoctl-ssh-ok"
        result = self.run(f"echo {marker}", timeout=self.connect_timeout + 5)
        return result.ok and marker in result.stdout
