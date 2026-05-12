from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "servers": {
        "app": {
            "host": "",
            "ssh_port": 10022,
            "user": "",
            "key_path": "",
            "deploy_path": "~/neobanker",
        },
        "gpu": {
            "host": "",
            "ssh_port": 10022,
            "user": "",
            "key_path": "",
            "ollama_port": 37434,
            "vllm_port": 38000,
            "model_storage": "/nfshdd/models",
            "model_cache": "/localnvme/models",
        },
    },
    "llm": {
        "local": {
            "backend": "ollama",
            "base_url": "",
            "models": [
                {
                    "name": "qwen3.5-9b-opus-distilled-v2:Q8_0",
                    "hf_repo": "Jackrong/Qwen3.5-9B-Claude-4.6-Opus-Reasoning-Distilled-v2-GGUF",
                    "hf_file": "Qwen3.5-9B-Claude-4.6-Opus-Reasoning-Distilled-v2-Q8_0.gguf",
                    "label": "Opus-Distilled v2 Q8 (quality)",
                },
                {
                    "name": "qwen3.5:9b",
                    "label": "Qwen3.5-9B Official (general)",
                },
            ],
        },
        "connectivity": {
            "method": "auto",
            "priority": ["direct", "forward_tunnel", "reverse_tunnel"],
            "local_port": 0,
        },
        "lazy_install": True,
        "cloud": [
            {"name": "gemini", "enabled": True, "model": "gemini-2.0-flash", "api_key_env": "GEMINI_API_KEY"},
            {"name": "claude", "enabled": True, "model": "claude-haiku-4-5-20251001", "api_key_env": "ANTHROPIC_API_KEY"},
            {"name": "copilot", "enabled": True, "model": "copilot-chat", "api_key_env": "GITHUB_COPILOT_TOKEN"},
            {"name": "openai", "enabled": True, "model": "gpt-4o-mini", "api_key_env": "OPENAI_API_KEY"},
            {"name": "openrouter", "enabled": False, "model": "auto", "api_key_env": "OPENROUTER_API_KEY"},
        ],
    },
    "deploy": {
        "mode": "native",
        "services": {
            "backend": {
                "repo": "neo-banker/neobanker-backend-MVP-V2",
                "dir": "backend",
                "service_name": "neobanker-backend",
            },
            "frontend": {
                "repo": "neo-banker/neobanker-frontend-MVP-V3",
                "dir": "frontend",
                "pm2_name": "neobanker-frontend-app",
            },
            "agent": {
                "repo": "neo-banker/neobanker-agent",
                "dir": "agent",
                "service_name": "neobanker-agent",
            },
        },
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: Path) -> dict[str, Any]:
    """Load config from YAML file, merging with defaults."""
    if not path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    with open(path) as f:
        user_cfg = yaml.safe_load(f) or {}
    return _deep_merge(DEFAULT_CONFIG, user_cfg)


def save_config(cfg: dict[str, Any], path: Path) -> None:
    """Save config to YAML file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
