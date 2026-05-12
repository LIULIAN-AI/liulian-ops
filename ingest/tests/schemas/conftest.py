from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def java_columns() -> list[dict[str, Any]]:
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "scan_java_columns.py")],
        check=True,
        cwd=ROOT,
    )
    with (ROOT / "tools" / "_java_columns.json").open(encoding="utf-8") as file:
        rows = json.load(file)
    return [row for row in rows if isinstance(row, dict)]
