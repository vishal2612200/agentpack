from __future__ import annotations

import statistics
import time
from pathlib import Path

import pytest

from agentpack.router.service import RouteService


@pytest.mark.slow
def test_warm_route_median_stays_under_five_seconds(tmp_path: Path) -> None:
    (tmp_path / ".agentpack").mkdir()
    (tmp_path / ".agentpack" / "config.toml").write_text(
        "[skills]\npaths = [\".agentpack/skills\"]\n", encoding="utf-8"
    )
    for index in range(1400):
        path = tmp_path / "src" / f"module_{index // 50}" / f"file_{index}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"def handler_{index}():\n    return {index}\n", encoding="utf-8")

    service = RouteService()
    service.route_task(tmp_path, "improve handler behavior")
    durations: list[float] = []
    for _ in range(3):
        started = time.perf_counter()
        service.route_task(tmp_path, "improve handler behavior")
        durations.append(time.perf_counter() - started)

    assert statistics.median(durations) <= 5.0, durations
