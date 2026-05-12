from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

from neoctl.ssh import SSHClient, SSHResult


def test_ssh_result_ok_property():
    success = SSHResult(exit_code=0, stdout="ok", stderr="")
    failure = SSHResult(exit_code=1, stdout="", stderr="boom")

    assert success.ok is True
    assert failure.ok is False


def test_build_ssh_command_includes_key_and_port():
    client = SSHClient(host="10.0.0.5", port=10022, user="deploy", key_path="~/.ssh/id_rsa")
    cmd = client._build_ssh_command("echo hello")

    assert cmd[0] == "ssh"
    assert cmd[cmd.index("-p") + 1] == "10022"
    assert cmd[cmd.index("-i") + 1] == "~/.ssh/id_rsa"
    assert "deploy@10.0.0.5" in cmd
    assert cmd[-1] == "echo hello"


def test_build_scp_command_omits_key_when_not_set():
    client = SSHClient(host="10.0.0.5", port=22, user="deploy", key_path="")
    cmd = client._build_scp_command("local.file", "~/remote.file")

    assert cmd[0] == "scp"
    assert "-i" not in cmd
    assert cmd[cmd.index("-P") + 1] == "22"
    assert cmd[-1] == "deploy@10.0.0.5:~/remote.file"


def test_run_returns_process_result(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(cmd, capture_output, text, timeout):  # noqa: ANN001
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        return MagicMock(returncode=0, stdout="done\n", stderr="")

    monkeypatch.setattr("neoctl.ssh.subprocess.run", fake_run)

    client = SSHClient(host="10.0.0.5", user="deploy", key_path="")
    result = client.run("echo done", timeout=9)

    assert result.ok is True
    assert result.stdout == "done\n"
    assert captured["timeout"] == 9
    assert str(captured["cmd"][-1]) == "echo done"


def test_run_handles_timeout(monkeypatch):
    def fake_run(*_args, **kwargs):  # noqa: ANN001
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=kwargs["timeout"])

    monkeypatch.setattr("neoctl.ssh.subprocess.run", fake_run)

    client = SSHClient(host="10.0.0.5", user="deploy", key_path="")
    result = client.run("sleep 99", timeout=5)

    assert result.ok is False
    assert result.exit_code == -1
    assert "timed out" in result.stderr.lower()


def test_scp_handles_missing_binary(monkeypatch):
    def fake_run(*_args, **_kwargs):  # noqa: ANN001
        raise FileNotFoundError("scp not found")

    monkeypatch.setattr("neoctl.ssh.subprocess.run", fake_run)

    client = SSHClient(host="10.0.0.5", user="deploy", key_path="")
    result = client.scp("local.file", "~/remote.file")

    assert result.ok is False
    assert result.exit_code == -1
    assert "not found" in result.stderr.lower()


def test_can_connect_requires_expected_echo(monkeypatch):
    client = SSHClient(host="10.0.0.5", user="deploy", key_path="")

    monkeypatch.setattr(
        client,
        "run",
        lambda *_args, **_kwargs: SSHResult(exit_code=0, stdout="wrong-marker\n", stderr=""),
    )
    assert client.can_connect() is False

    monkeypatch.setattr(
        client,
        "run",
        lambda *_args, **_kwargs: SSHResult(exit_code=0, stdout="neoctl-ssh-ok\n", stderr=""),
    )
    assert client.can_connect() is True
