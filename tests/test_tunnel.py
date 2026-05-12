from __future__ import annotations

from unittest.mock import MagicMock

import httpx

from neoctl.tunnel import (
    TunnelInfo,
    build_forward_tunnel_command,
    build_reverse_tunnel_command,
    check_direct_reachable,
    check_tunnel_process,
    start_forward_tunnel,
)


def test_build_forward_tunnel_command_prefers_autossh(monkeypatch):
    monkeypatch.setattr(
        "neoctl.tunnel.shutil.which",
        lambda name: "/usr/bin/autossh" if name == "autossh" else "/usr/bin/ssh",
    )

    cmd = build_forward_tunnel_command(
        gpu_host="10.0.0.8",
        gpu_port=10022,
        gpu_user="ubuntu",
        gpu_key="~/.ssh/id_gpu",
        local_port=37434,
        remote_port=37434,
    )

    assert cmd[0] == "autossh"
    assert cmd[cmd.index("-L") + 1] == "37434:localhost:37434"
    assert "ubuntu@10.0.0.8" in cmd


def test_build_forward_tunnel_command_falls_back_to_ssh(monkeypatch):
    monkeypatch.setattr(
        "neoctl.tunnel.shutil.which",
        lambda name: None if name == "autossh" else "/usr/bin/ssh",
    )

    cmd = build_forward_tunnel_command(
        gpu_host="10.0.0.8",
        gpu_port=10022,
        gpu_user="ubuntu",
        gpu_key="",
        local_port=38000,
        remote_port=38000,
    )

    assert cmd[0] == "ssh"
    assert cmd[cmd.index("-L") + 1] == "38000:localhost:38000"
    assert "-i" not in cmd


def test_build_reverse_tunnel_command_is_available_for_docs(monkeypatch):
    monkeypatch.setattr("neoctl.tunnel.shutil.which", lambda _name: "/usr/bin/autossh")

    cmd = build_reverse_tunnel_command(
        app_host="10.0.0.2",
        app_port=10022,
        app_user="deploy",
        app_key="~/.ssh/id_app",
        tunnel_port=37434,
        target_port=37434,
    )

    assert cmd[0] == "autossh"
    assert cmd[cmd.index("-R") + 1] == "37434:localhost:37434"


def test_check_tunnel_process_parses_forward_process(monkeypatch):
    output = "neo      4321  0.0  autossh -f -N -L 37434:localhost:37434 deploy@10.0.0.8\n"
    monkeypatch.setattr(
        "neoctl.tunnel.subprocess.run",
        lambda *_args, **_kwargs: MagicMock(returncode=0, stdout=output, stderr=""),
    )

    info = check_tunnel_process(local_port=37434)

    assert info == TunnelInfo(pid=4321, local_port=37434, tunnel_type="forward")


def test_check_tunnel_process_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(
        "neoctl.tunnel.subprocess.run",
        lambda *_args, **_kwargs: MagicMock(returncode=0, stdout="", stderr=""),
    )
    assert check_tunnel_process(local_port=37434) is None


def test_start_forward_tunnel_skips_spawn_when_existing(monkeypatch):
    monkeypatch.setattr(
        "neoctl.tunnel.check_tunnel_process",
        lambda _port: TunnelInfo(pid=999, local_port=37434, tunnel_type="forward"),
    )
    called = {"spawned": False}

    def fake_run(*_args, **_kwargs):  # noqa: ANN001
        called["spawned"] = True
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("neoctl.tunnel.subprocess.run", fake_run)

    ok = start_forward_tunnel(
        gpu_host="10.0.0.8",
        gpu_port=10022,
        gpu_user="ubuntu",
        gpu_key="",
        local_port=37434,
        remote_port=37434,
    )
    assert ok is True
    assert called["spawned"] is False


def test_start_forward_tunnel_does_not_treat_reverse_as_success(monkeypatch):
    monkeypatch.setattr(
        "neoctl.tunnel.check_tunnel_process",
        lambda _port: TunnelInfo(pid=999, local_port=37434, tunnel_type="reverse"),
    )
    called = {"spawned": False}

    def fake_run(*_args, **_kwargs):  # noqa: ANN001
        called["spawned"] = True
        return MagicMock(returncode=1, stdout="", stderr="bind failed")

    monkeypatch.setattr("neoctl.tunnel.subprocess.run", fake_run)

    ok = start_forward_tunnel(
        gpu_host="10.0.0.8",
        gpu_port=10022,
        gpu_user="ubuntu",
        gpu_key="",
        local_port=37434,
        remote_port=37434,
    )

    assert ok is False
    assert called["spawned"] is True


def test_check_direct_reachable_tries_ollama_then_vllm(monkeypatch):
    seen: list[str] = []

    def fake_get(url: str, timeout: float):  # noqa: ARG001
        seen.append(url)
        request = httpx.Request("GET", url)
        if url.endswith("/api/tags"):
            raise httpx.ConnectError("connection refused", request=request)
        return MagicMock(status_code=200)

    monkeypatch.setattr("neoctl.tunnel.httpx.get", fake_get)

    assert check_direct_reachable("10.0.0.8", 38000) is True
    assert seen == [
        "http://10.0.0.8:38000/api/tags",
        "http://10.0.0.8:38000/v1/models",
    ]
