from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import click
from rich.console import Console
from rich.table import Table

from neoctl import __version__
from neoctl.config import load_config, save_config
from neoctl.deploy.agent import deploy_agent
from neoctl.deploy.backend import deploy_backend
from neoctl.deploy.frontend import deploy_frontend
from neoctl.detect import detect_connectivity, detect_gpu_server_state
from neoctl.doctor import check_all_services
from neoctl.llm_setup import (
    install_ollama,
    install_vllm,
    pull_ollama_model,
    start_ollama,
    start_vllm,
)
from neoctl.ssh import SSHClient
from neoctl.tunnel import build_reverse_tunnel_command, start_forward_tunnel

console = Console()
CONFIG_PATH = Path("config.yaml")
DEFAULT_BACKEND_SERVICE_NAME = "neobanker-backend"
DEFAULT_FRONTEND_PM2_NAME = "neobanker-frontend-app"
DEFAULT_AGENT_SERVICE_NAME = "neobanker-agent"


def _load_cfg() -> dict:
    return load_config(CONFIG_PATH)


def _save_cfg(cfg: dict) -> None:
    save_config(cfg, CONFIG_PATH)


def _get_app_ssh(cfg: dict) -> SSHClient:
    app = cfg["servers"]["app"]
    return SSHClient(
        host=app.get("host", ""),
        port=int(app.get("ssh_port", 22)),
        user=app.get("user", ""),
        key_path=app.get("key_path", ""),
    )


def _get_gpu_ssh(cfg: dict) -> SSHClient:
    gpu = cfg["servers"]["gpu"]
    return SSHClient(
        host=gpu.get("host", ""),
        port=int(gpu.get("ssh_port", 22)),
        user=gpu.get("user", ""),
        key_path=gpu.get("key_path", ""),
    )


def _service_path(cfg: dict, service: str, default_dir: str) -> str:
    base_path = str(cfg["servers"]["app"].get("deploy_path", "~/neobanker")).rstrip("/")
    rel = str(cfg.get("deploy", {}).get("services", {}).get(service, {}).get("dir", default_dir)).lstrip("/")
    return f"{base_path}/{rel}"


def _service_runtime_name(cfg: dict, service: str, key: str, default: str) -> str:
    configured = str(cfg.get("deploy", {}).get("services", {}).get(service, {}).get(key, "")).strip()
    return configured or default


def _backend_port(cfg: dict) -> int:
    backend = cfg["llm"]["local"].get("backend", "ollama")
    gpu_cfg = cfg["servers"]["gpu"]
    if backend == "vllm":
        return int(gpu_cfg.get("vllm_port", 38000))
    return int(gpu_cfg.get("ollama_port", 37434))


def _forward_local_port(cfg: dict) -> int:
    local_port = int(cfg["llm"]["connectivity"].get("local_port", 0) or 0)
    if local_port:
        return local_port
    return _backend_port(cfg)


def _record_connectivity(cfg: dict, url: str, method: str) -> None:
    cfg["llm"]["local"]["base_url"] = url
    if method:
        cfg["llm"]["connectivity"]["method"] = method
    if method == "forward_tunnel":
        parsed = urlsplit(url)
        if parsed.port:
            cfg["llm"]["connectivity"]["local_port"] = parsed.port
    _save_cfg(cfg)


def _print_service_result(service: str, result: object, dry_run: bool) -> None:
    if getattr(result, "success", False):
        mode = "planned" if dry_run else "completed"
        steps = ", ".join(getattr(result, "steps", [])) or "no-op"
        console.print(f"[green]{service} {mode}[/green]: {steps}")
        if dry_run:
            for command in getattr(result, "commands", []):
                console.print(f"  {command}")
        return

    error = getattr(result, "error", "unknown error")
    raise click.ClickException(f"{service} failed: {error}")


def _deploy_llm(cfg: dict, dry_run: bool = False) -> bool:
    first_probe = detect_connectivity(cfg)
    if first_probe.reachable:
        _record_connectivity(cfg, first_probe.url, first_probe.method)
        console.print(f"[green]LLM reachable via {first_probe.method} at {first_probe.url}[/green]")
        return True

    if dry_run:
        console.print("[yellow]LLM dry-run: would install/start on GPU and then try forward tunnel fallback.[/yellow]")
        return True

    gpu_ssh = _get_gpu_ssh(cfg)
    if not gpu_ssh.can_connect():
        console.print("[red]Cannot SSH to GPU server. Configure servers.gpu and retry.[/red]")
        return False

    backend = cfg["llm"]["local"].get("backend", "ollama")
    gpu_cfg = cfg["servers"]["gpu"]
    backend_port = _backend_port(cfg)

    state = detect_gpu_server_state(
        gpu_ssh,
        backend=backend,
        ollama_port=int(gpu_cfg.get("ollama_port", 37434)),
        vllm_port=int(gpu_cfg.get("vllm_port", 38000)),
    )

    if not state.llm_running and not state.llm_installed:
        console.print(f"[bold]Installing {backend} on GPU...[/bold]")
        installed = install_ollama(gpu_ssh, internet=state.internet_available) if backend == "ollama" else install_vllm(
            gpu_ssh,
            internet=state.internet_available,
        )
        if not installed:
            console.print(f"[red]Failed to install {backend}.[/red]")
            return False

    if not state.llm_running:
        console.print(f"[bold]Starting {backend} on GPU...[/bold]")
        if backend == "ollama":
            started = start_ollama(
                gpu_ssh,
                port=int(gpu_cfg.get("ollama_port", 37434)),
                model_storage=str(gpu_cfg.get("model_storage", "/nfshdd/models")),
            )
        else:
            models = cfg["llm"]["local"].get("models", [])
            model_name = str(models[0].get("name", "Qwen/Qwen3.5-7B-Instruct")) if models else "Qwen/Qwen3.5-7B-Instruct"
            started = start_vllm(
                gpu_ssh,
                model_name=model_name,
                port=int(gpu_cfg.get("vllm_port", 38000)),
                hf_home=str(gpu_cfg.get("model_storage", "/nfshdd/models")),
            )
        if not started:
            console.print(f"[red]Failed to start {backend}.[/red]")
            return False

    if backend == "ollama":
        for model_cfg in cfg["llm"]["local"].get("models", []):
            model_name = str(model_cfg.get("name", "")).strip()
            if not model_name:
                continue
            if pull_ollama_model(gpu_ssh, model_name=model_name, internet=state.internet_available):
                console.print(f"[green]Model ready: {model_name}[/green]")
            else:
                console.print(f"[yellow]Model unavailable (continuing): {model_name}[/yellow]")

    second_probe = detect_connectivity(cfg)
    if second_probe.reachable:
        _record_connectivity(cfg, second_probe.url, second_probe.method)
        console.print(f"[green]LLM reachable via {second_probe.method} at {second_probe.url}[/green]")
        return True

    local_port = _forward_local_port(cfg)
    console.print(f"[bold]Trying forward tunnel fallback on localhost:{local_port}...[/bold]")
    tunneled = start_forward_tunnel(
        gpu_host=str(gpu_cfg.get("host", "")),
        gpu_port=int(gpu_cfg.get("ssh_port", 22)),
        gpu_user=str(gpu_cfg.get("user", "")),
        gpu_key=str(gpu_cfg.get("key_path", "")),
        local_port=local_port,
        remote_port=backend_port,
    )
    if tunneled:
        cfg["llm"]["connectivity"]["local_port"] = local_port
        tunneled_probe = detect_connectivity(cfg)
        if tunneled_probe.reachable:
            _record_connectivity(cfg, tunneled_probe.url, tunneled_probe.method)
            console.print(f"[green]LLM reachable via {tunneled_probe.method} at {tunneled_probe.url}[/green]")
            return True

    console.print("[yellow]Automatic LLM setup failed. Reverse tunnel remains docs-only/manual.[/yellow]")
    app_cfg = cfg["servers"].get("app", {})
    app_host = str(app_cfg.get("host", "")).strip()
    if app_host:
        reverse_hint = build_reverse_tunnel_command(
            app_host=app_host,
            app_port=int(app_cfg.get("ssh_port", 22)),
            app_user=str(app_cfg.get("user", "")),
            app_key=str(app_cfg.get("key_path", "")),
            tunnel_port=backend_port,
            target_port=backend_port,
        )
        console.print("Manual reverse tunnel command (run on GPU server):")
        console.print("  " + " ".join(reverse_hint))
    else:
        console.print("Set servers.app.* in config.yaml to print a reverse tunnel hint command.")

    return False


@click.group()
@click.version_option(__version__)
def main() -> None:
    """Neobanker deployment CLI."""


@main.command("doctor")
def doctor_cmd() -> None:
    """Run service health checks."""
    cfg = _load_cfg()
    statuses = check_all_services(cfg)

    table = Table(show_header=True)
    table.add_column("Status")
    table.add_column("Service")
    table.add_column("Detail")

    for status in statuses:
        table.add_row("OK" if status.ok else "FAIL", status.name, status.detail)

    console.print(table)
    passed = sum(1 for status in statuses if status.ok)
    console.print(f"{passed}/{len(statuses)} checks passed")


@main.group("deploy")
def deploy_group() -> None:
    """Deploy and setup commands."""


@deploy_group.command("llm")
@click.option("--dry-run", is_flag=True, help="Assemble actions without executing remote commands.")
def deploy_llm_cmd(dry_run: bool) -> None:
    """Deploy or repair LLM connectivity."""
    cfg = _load_cfg()
    if not _deploy_llm(cfg, dry_run=dry_run):
        raise click.ClickException("deploy llm failed")


@deploy_group.command("backend")
@click.option("--branch", default="main", show_default=True)
@click.option(
    "--service-name",
    default=None,
    show_default=f"deploy.services.backend.service_name or {DEFAULT_BACKEND_SERVICE_NAME}",
)
@click.option("--dry-run", is_flag=True, help="Assemble actions without executing remote commands.")
def deploy_backend_cmd(branch: str, service_name: str | None, dry_run: bool) -> None:
    """Deploy backend service."""
    cfg = _load_cfg()
    app_ssh = _get_app_ssh(cfg)
    resolved_service_name = (
        service_name
        if service_name is not None
        else _service_runtime_name(cfg, "backend", "service_name", DEFAULT_BACKEND_SERVICE_NAME)
    )
    result = deploy_backend(
        app_ssh,
        deploy_path=_service_path(cfg, "backend", "backend"),
        branch=branch,
        service_name=resolved_service_name,
        dry_run=dry_run,
    )
    _print_service_result("backend", result, dry_run=dry_run)


@deploy_group.command("frontend")
@click.option("--branch", default="main", show_default=True)
@click.option(
    "--pm2-name",
    default=None,
    show_default=f"deploy.services.frontend.pm2_name or {DEFAULT_FRONTEND_PM2_NAME}",
)
@click.option("--dry-run", is_flag=True, help="Assemble actions without executing remote commands.")
def deploy_frontend_cmd(branch: str, pm2_name: str | None, dry_run: bool) -> None:
    """Deploy frontend service."""
    cfg = _load_cfg()
    app_ssh = _get_app_ssh(cfg)
    resolved_pm2_name = (
        pm2_name
        if pm2_name is not None
        else _service_runtime_name(cfg, "frontend", "pm2_name", DEFAULT_FRONTEND_PM2_NAME)
    )
    result = deploy_frontend(
        app_ssh,
        deploy_path=_service_path(cfg, "frontend", "frontend"),
        branch=branch,
        pm2_name=resolved_pm2_name,
        dry_run=dry_run,
    )
    _print_service_result("frontend", result, dry_run=dry_run)


@deploy_group.command("agent")
@click.option("--branch", default="main", show_default=True)
@click.option(
    "--service-name",
    default=None,
    show_default=f"deploy.services.agent.service_name or {DEFAULT_AGENT_SERVICE_NAME}",
)
@click.option("--dry-run", is_flag=True, help="Assemble actions without executing remote commands.")
def deploy_agent_cmd(branch: str, service_name: str | None, dry_run: bool) -> None:
    """Deploy agent service."""
    cfg = _load_cfg()
    app_ssh = _get_app_ssh(cfg)
    resolved_service_name = (
        service_name
        if service_name is not None
        else _service_runtime_name(cfg, "agent", "service_name", DEFAULT_AGENT_SERVICE_NAME)
    )
    result = deploy_agent(
        app_ssh,
        deploy_path=_service_path(cfg, "agent", "agent"),
        branch=branch,
        service_name=resolved_service_name,
        dry_run=dry_run,
    )
    _print_service_result("agent", result, dry_run=dry_run)


@deploy_group.command("all")
@click.option("--dry-run", is_flag=True, help="Assemble actions without executing remote commands.")
def deploy_all_cmd(dry_run: bool) -> None:
    """Run llm + backend + frontend + agent in order."""
    cfg = _load_cfg()

    if not _deploy_llm(cfg, dry_run=dry_run):
        raise click.ClickException("deploy llm failed")

    app_ssh = _get_app_ssh(cfg)
    backend_service_name = _service_runtime_name(cfg, "backend", "service_name", DEFAULT_BACKEND_SERVICE_NAME)
    frontend_pm2_name = _service_runtime_name(cfg, "frontend", "pm2_name", DEFAULT_FRONTEND_PM2_NAME)
    agent_service_name = _service_runtime_name(cfg, "agent", "service_name", DEFAULT_AGENT_SERVICE_NAME)

    backend_result = deploy_backend(
        app_ssh,
        deploy_path=_service_path(cfg, "backend", "backend"),
        service_name=backend_service_name,
        dry_run=dry_run,
    )
    _print_service_result("backend", backend_result, dry_run=dry_run)

    frontend_result = deploy_frontend(
        app_ssh,
        deploy_path=_service_path(cfg, "frontend", "frontend"),
        pm2_name=frontend_pm2_name,
        dry_run=dry_run,
    )
    _print_service_result("frontend", frontend_result, dry_run=dry_run)

    agent_result = deploy_agent(
        app_ssh,
        deploy_path=_service_path(cfg, "agent", "agent"),
        service_name=agent_service_name,
        dry_run=dry_run,
    )
    _print_service_result("agent", agent_result, dry_run=dry_run)

    console.print("[green]All deploy stages completed.[/green]")


if __name__ == "__main__":
    main()
