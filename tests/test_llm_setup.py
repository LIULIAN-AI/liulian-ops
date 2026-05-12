from __future__ import annotations

from unittest.mock import MagicMock

from neoctl.llm_setup import (
    create_ollama_modelfile,
    ensure_port_open_hint,
    install_ollama,
    install_vllm,
    pull_ollama_model,
    start_ollama,
    start_vllm,
)
from neoctl.ssh import SSHResult


def test_install_ollama_noop_when_binary_exists():
    ssh = MagicMock()
    ssh.run.return_value = SSHResult(exit_code=0, stdout="/usr/local/bin/ollama\n", stderr="")

    assert install_ollama(ssh, internet=True) is True
    ssh.run.assert_called_once_with("which ollama")


def test_install_ollama_online_install():
    ssh = MagicMock()
    ssh.run.side_effect = [
        SSHResult(exit_code=1, stdout="", stderr=""),
        SSHResult(exit_code=0, stdout="", stderr=""),
    ]

    assert install_ollama(ssh, internet=True) is True
    assert "curl -fsSL https://ollama.com/install.sh | sh" in ssh.run.call_args_list[1].args[0]


def test_install_ollama_offline_upload_and_install():
    ssh = MagicMock()
    ssh.run.side_effect = [
        SSHResult(exit_code=1, stdout="", stderr=""),
        SSHResult(exit_code=0, stdout="", stderr=""),
    ]
    ssh.scp.return_value = SSHResult(exit_code=0, stdout="", stderr="")

    ok = install_ollama(ssh, internet=False, local_binary_path="./bin/ollama-linux-amd64")

    assert ok is True
    ssh.scp.assert_called_once_with("./bin/ollama-linux-amd64", "~/neoctl-ollama")
    assert "sudo install -m 755 ~/neoctl-ollama /usr/local/bin/ollama" in ssh.run.call_args_list[1].args[0]


def test_install_ollama_offline_fails_when_scp_fails():
    ssh = MagicMock()
    ssh.run.return_value = SSHResult(exit_code=1, stdout="", stderr="")
    ssh.scp.return_value = SSHResult(exit_code=1, stdout="", stderr="scp failed")

    assert install_ollama(ssh, internet=False, local_binary_path="./bin/ollama-linux-amd64") is False


def test_install_vllm_installs_when_missing():
    ssh = MagicMock()
    ssh.run.side_effect = [
        SSHResult(exit_code=1, stdout="", stderr=""),
        SSHResult(exit_code=0, stdout="ok", stderr=""),
    ]

    assert install_vllm(ssh, internet=True) is True
    assert "pip install vllm" in ssh.run.call_args_list[1].args[0]


def test_pull_ollama_model_skips_when_present():
    ssh = MagicMock()
    ssh.run.return_value = SSHResult(exit_code=0, stdout="qwen3.5:9b 5.6GB\n", stderr="")

    assert pull_ollama_model(ssh, model_name="qwen3.5:9b", internet=True) is True
    ssh.run.assert_called_once_with("ollama list")


def test_pull_ollama_model_quotes_model_name_when_missing():
    ssh = MagicMock()
    ssh.run.side_effect = [
        SSHResult(exit_code=0, stdout="llama3:8b 4.7GB\n", stderr=""),
        SSHResult(exit_code=0, stdout="", stderr=""),
    ]

    ok = pull_ollama_model(ssh, model_name="qwen3.5:9b; echo hacked", internet=True)

    assert ok is True
    assert ssh.run.call_args_list[1].args[0] == "ollama pull 'qwen3.5:9b; echo hacked'"


def test_start_ollama_uses_direct_tcp_defaults():
    ssh = MagicMock()
    ssh.run.side_effect = [
        SSHResult(exit_code=1, stdout="", stderr=""),  # not running
        SSHResult(exit_code=0, stdout="", stderr=""),  # launch command
        SSHResult(exit_code=0, stdout='{"models":[]}', stderr=""),  # running now
    ]

    assert start_ollama(ssh, port=37434, model_storage="/nfshdd/models") is True
    launch_cmd = ssh.run.call_args_list[1].args[0]
    assert "OLLAMA_HOST=0.0.0.0:37434" in launch_cmd
    assert "OLLAMA_MODELS=/nfshdd/models" in launch_cmd
    assert "ollama serve" in launch_cmd


def test_start_ollama_retries_until_ready(monkeypatch):
    delays: list[float] = []
    monkeypatch.setattr("neoctl.llm_setup.time.sleep", lambda seconds: delays.append(seconds))

    ssh = MagicMock()
    ssh.run.side_effect = [
        SSHResult(exit_code=1, stdout="", stderr=""),  # not running
        SSHResult(exit_code=0, stdout="", stderr=""),  # launch command
        SSHResult(exit_code=1, stdout="", stderr=""),  # verify #1
        SSHResult(exit_code=1, stdout="", stderr=""),  # verify #2
        SSHResult(exit_code=0, stdout='{"models":[]}', stderr=""),  # verify #3
    ]

    assert start_ollama(ssh, port=37434, model_storage="/nfshdd/models") is True
    assert delays == [1.0, 2.0]


def test_start_vllm_uses_direct_tcp_defaults():
    ssh = MagicMock()
    ssh.run.side_effect = [
        SSHResult(exit_code=1, stdout="", stderr=""),  # not running
        SSHResult(exit_code=0, stdout="", stderr=""),  # launch command
        SSHResult(exit_code=0, stdout='{"data":[{"id":"qwen3.5"}]}', stderr=""),  # running now
    ]

    ok = start_vllm(
        ssh,
        model_name="Qwen/Qwen3.5-7B-Instruct",
        port=38000,
        hf_home="/nfshdd/models",
    )

    assert ok is True
    launch_cmd = ssh.run.call_args_list[1].args[0]
    assert "--host 0.0.0.0" in launch_cmd
    assert "--port 38000" in launch_cmd


def test_start_vllm_quotes_model_name_and_bounds_retries(monkeypatch):
    delays: list[float] = []
    monkeypatch.setattr("neoctl.llm_setup.time.sleep", lambda seconds: delays.append(seconds))

    ssh = MagicMock()
    ssh.run.side_effect = [
        SSHResult(exit_code=1, stdout="", stderr=""),  # not running
        SSHResult(exit_code=0, stdout="", stderr=""),  # launch command
        SSHResult(exit_code=1, stdout="", stderr=""),  # verify #1
        SSHResult(exit_code=1, stdout="", stderr=""),  # verify #2
        SSHResult(exit_code=1, stdout="", stderr=""),  # verify #3
        SSHResult(exit_code=1, stdout="", stderr=""),  # verify #4
    ]

    ok = start_vllm(
        ssh,
        model_name="Qwen/Qwen3.5-7B-Instruct; echo hacked",
        port=38000,
        hf_home="/nfshdd/models",
    )

    assert ok is False
    launch_cmd = ssh.run.call_args_list[1].args[0]
    assert "vllm serve 'Qwen/Qwen3.5-7B-Instruct; echo hacked'" in launch_cmd
    assert delays == [1.0, 2.0, 4.0]


def test_create_ollama_modelfile():
    assert create_ollama_modelfile("/nfshdd/models/model.gguf") == "FROM /nfshdd/models/model.gguf\n"


def test_ensure_port_open_hint():
    assert ensure_port_open_hint(37434) == "sudo ufw allow 37434/tcp"
