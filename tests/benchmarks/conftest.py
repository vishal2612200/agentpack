"""Gate the slow ranking-benchmark tests behind an explicit opt-in.

Benchmark tests clone real public repos (rails, laravel, vite, etc.),
sample commits, and run the full pack/rank pipeline per case. That takes
minutes. Skipped unless `--run-benchmarks` is passed OR the env var
`AGENTPACK_RUN_BENCHMARKS=1` is set.

Usage:
    pytest tests/benchmarks/ --run-benchmarks
    pytest tests/benchmarks/test_php.py --run-benchmarks -v
    AGENTPACK_RUN_BENCHMARKS=1 pytest tests/benchmarks/
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib


def pytest_addoption(parser):
    parser.addoption(
        "--run-benchmarks",
        action="store_true",
        default=False,
        help="Run the per-language ranking-quality benchmark suite (slow: several minutes).",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-benchmarks") or os.environ.get("AGENTPACK_RUN_BENCHMARKS") == "1":
        return
    skip_marker = pytest.mark.skip(
        reason="use --run-benchmarks or AGENTPACK_RUN_BENCHMARKS=1 to run"
    )
    for item in items:
        if "benchmarks" in item.keywords or "benchmarks" in str(item.fspath):
            item.add_marker(skip_marker)


@pytest.fixture(scope="session")
def thresholds() -> dict:
    """Load thresholds.toml once per session."""
    path = Path(__file__).parent / "thresholds.toml"
    return tomllib.loads(path.read_text())
