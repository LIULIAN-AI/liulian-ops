from __future__ import annotations

import shlex
import time

from neoctl.ssh import SSHClient


def _wait_for_startup(
    ssh: SSHClient,
    check_cmd: str,
    attempts: int = 4,
    initial_backoff_sec: float = 1.0,
    max_backoff_sec: float = 4.0,
) -> bool:
    backoff = initial_backoff_sec
    for attempt in range(attempts):
        verify = ssh.run(check_cmd)
        if verify.ok and verify.stdout.strip():
            return True
        if attempt < attempts - 1:
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff_sec)
    return False


def install_ollama(
    ssh: SSHClient,
    internet: bool = True,
    local_binary_path: str = "./ollama-linux-amd64",
) -> bool:
    present = ssh.run("which ollama")
    if present.ok:
        return True

    if internet:
        install = ssh.run("curl -fsSL https://ollama.com/install.sh | sh", timeout=180)
        if install.ok:
            return True

    upload = ssh.scp(local_binary_path, "~/neoctl-ollama")
    if not upload.ok:
        return False
    finalize = ssh.run("sudo install -m 755 ~/neoctl-ollama /usr/local/bin/ollama")
    return finalize.ok


def install_vllm(ssh: SSHClient, internet: bool = True) -> bool:
    present = ssh.run("python3 -c 'import vllm' 2>/dev/null")
    if present.ok:
        return True
    if not internet:
        return False
    install = ssh.run("pip install vllm", timeout=600)
    return install.ok


def pull_ollama_model(ssh: SSHClient, model_name: str, internet: bool = True) -> bool:
    listed = ssh.run("ollama list")
    if listed.ok and model_name in listed.stdout:
        return True
    if not internet:
        return False
    pulled = ssh.run(f"ollama pull {shlex.quote(model_name)}", timeout=900)
    return pulled.ok


def start_ollama(ssh: SSHClient, port: int = 37434, model_storage: str = "/nfshdd/models") -> bool:
    already = ssh.run(f"curl -s --connect-timeout 3 http://localhost:{port}/api/tags")
    if already.ok and already.stdout.strip():
        return True

    launched = ssh.run(
        f"OLLAMA_HOST=0.0.0.0:{port} OLLAMA_MODELS={shlex.quote(model_storage)} "
        f"nohup ollama serve > ~/neoctl-ollama.log 2>&1 &",
        timeout=10,
    )
    if not launched.ok:
        return False

    return _wait_for_startup(ssh, f"curl -s --connect-timeout 5 http://localhost:{port}/api/tags")


def start_vllm(
    ssh: SSHClient,
    model_name: str,
    port: int = 38000,
    hf_home: str = "/nfshdd/models",
) -> bool:
    already = ssh.run(f"curl -s --connect-timeout 3 http://localhost:{port}/v1/models")
    if already.ok and already.stdout.strip():
        return True

    launched = ssh.run(
        f"HF_HOME={shlex.quote(hf_home)} nohup vllm serve {shlex.quote(model_name)} "
        f"--host 0.0.0.0 --port {port} > ~/neoctl-vllm.log 2>&1 &",
        timeout=10,
    )
    if not launched.ok:
        return False

    return _wait_for_startup(ssh, f"curl -s --connect-timeout 5 http://localhost:{port}/v1/models")


def create_ollama_modelfile(gguf_path: str) -> str:
    return f"FROM {gguf_path}\n"


def ensure_port_open_hint(port: int) -> str:
    return f"sudo ufw allow {port}/tcp"
