from __future__ import annotations

import pytest
from click.testing import CliRunner

from neoctl.cli import main


@pytest.mark.parametrize(
    ("args", "usage_prefix"),
    [
        (["--help"], "Usage: main [OPTIONS] COMMAND [ARGS]..."),
        (["deploy", "--help"], "Usage: main deploy [OPTIONS] COMMAND [ARGS]..."),
        (["deploy", "llm", "--help"], "Usage: main deploy llm [OPTIONS]"),
        (["doctor", "--help"], "Usage: main doctor [OPTIONS]"),
    ],
)
def test_help_surfaces_are_available(args: list[str], usage_prefix: str):
    result = CliRunner().invoke(main, args)

    assert result.exit_code == 0
    assert usage_prefix in result.output
