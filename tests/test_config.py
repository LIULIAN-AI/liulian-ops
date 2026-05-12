from __future__ import annotations

from pathlib import Path

import yaml

from neoctl.config import DEFAULT_CONFIG, load_config, save_config


def test_load_config_returns_defaults_when_file_missing(tmp_path: Path):
    cfg = load_config(tmp_path / "nonexistent.yaml")
    assert cfg["llm"]["local"]["backend"] == "ollama"
    assert cfg["servers"]["gpu"]["ssh_port"] == 10022


def test_save_and_reload_config(tmp_path: Path):
    path = tmp_path / "config.yaml"
    cfg = {
        "servers": {"gpu": {"host": "10.0.0.1"}},
    }
    save_config(cfg, path)

    loaded = load_config(path)
    assert loaded["servers"]["gpu"]["host"] == "10.0.0.1"


def test_load_config_merges_with_defaults(tmp_path: Path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump({"servers": {"gpu": {"host": "10.0.0.1"}}}))
    loaded = load_config(path)
    # Custom value
    assert loaded["servers"]["gpu"]["host"] == "10.0.0.1"
    # Defaults preserved
    assert loaded["llm"]["local"]["backend"] == "ollama"
    assert loaded["servers"]["gpu"]["ssh_port"] == 10022


def test_default_config_has_updated_ports():
    """Design update 2026-04-10: direct-TCP high ports as defaults."""
    assert DEFAULT_CONFIG["servers"]["gpu"]["ollama_port"] == 37434
    assert DEFAULT_CONFIG["servers"]["gpu"]["vllm_port"] == 38000


def test_default_config_connectivity_priority():
    """Design update 2026-04-10: direct first, reverse tunnel last."""
    priority = DEFAULT_CONFIG["llm"]["connectivity"]["priority"]
    assert priority == ["direct", "forward_tunnel", "reverse_tunnel"]


def test_default_config_runtime_names():
    services = DEFAULT_CONFIG["deploy"]["services"]
    assert services["backend"]["service_name"] == "neobanker-backend"
    assert services["frontend"]["pm2_name"] == "neobanker-frontend-app"
    assert services["agent"]["service_name"] == "neobanker-agent"
