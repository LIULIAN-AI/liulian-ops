from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    """Return path to a temporary config.yaml."""
    return tmp_path / "config.yaml"
