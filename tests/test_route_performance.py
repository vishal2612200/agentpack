from __future__ import annotations

import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agentpack.application.pack_service import PackPlanner
from agentpack.core.token_estimator import estimator_mode
from agentpack.router.service import RouteService

SOURCE_ROOT = Path(__file__).resolve().parents[1]
TASK = "fix payment retry handling"
OWNER = "src/payments/retry.py"
# Calibrate on healthy runners; never derive limits from candidate timings.
LIMITS = {"cold": 10, "same_task": 5, "new_task": 5, "edit": 5, "add": 5, "delete": 5, "cli": 10}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", f"core.hooksPath={root / '.no-hooks'}", *args],
        cwd=root, check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()


def _fixture(root: Path) -> None:
    root.mkdir()
    (root / ".agentpack").mkdir()
    (root / ".agentpack/config.toml").write_text(
        '[skills]\npaths = [".agentpack/skills"]\n', encoding="utf-8",
    )
    (root / ".gitignore").write_text(".agentpack/\n.no-hooks/\n", encoding="utf-8")
    for index in range(1400):
        path = root / "src" / f"module_{index // 50}" / f"file_{index}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"def handler_{index}():\n    return {index}\n", encoding="utf-8")
    for path, content in {
        OWNER: "def retry_payment():\n    return 'pending'\n",
        "src/inventory/reservations.py": "def reserve_inventory():\n    return 1\n",
        "tests/test_payment_retry.py": "from src.payments.retry import retry_payment\n",
    }.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Route test", "-c", "user.email=route@example.test",
         "-c", "commit.gpgsign=false", "commit", "-qm", "fixture")


@pytest.mark.slow
@pytest.mark.parametrize("scenario", list(LIMITS))
def test_route_scenarios(tmp_path: Path, monkeypatch, scenario: str) -> None:
    plans = []
    original_plan = PackPlanner.plan

    def measured_plan(self, request, **kwargs):
        result = original_plan(self, request, **kwargs)
        plans.append(result)
        return result

    monkeypatch.setattr(PackPlanner, "plan", measured_plan)
    report = {
        "scenario": scenario, "source_sha": _git(SOURCE_ROOT, "rev-parse", "HEAD"),
        "python": platform.python_version(), "platform": platform.platform(),
        "estimator": estimator_mode(), "free_disk_bytes": shutil.disk_usage(tmp_path).free,
        "limit_seconds": LIMITS[scenario], "samples": [],
    }
    try:
        for index in range(3):
            root = tmp_path / f"sample-{index}"
            _fixture(root)
            service = RouteService()
            task, owner = TASK, OWNER
            warm = None
            if scenario != "cold":
                warm = service.route_task(root, task, timeout_s=30)
                assert owner in [item["path"] for item in warm.selected_files]
            if scenario == "new_task":
                task, owner = "fix inventory reservation", "src/inventory/reservations.py"
            elif scenario == "edit":
                (root / owner).write_text("def retry_payment_updated():\n    return 'settled'\n", encoding="utf-8")
            elif scenario == "add":
                owner = "src/payments/retry_policy.py"
                (root / owner).write_text("def payment_retry_policy():\n    return 2\n", encoding="utf-8")
            elif scenario == "delete":
                (root / owner).unlink()

            calls_before = len(plans)
            started = time.perf_counter()
            if scenario == "cli":
                result = subprocess.run(
                    [sys.executable, "-m", "agentpack.cli", "route", "--task", task, "--json"],
                    cwd=root, env={**os.environ, "PYTHONPATH": str(SOURCE_ROOT / "src")},
                    capture_output=True, text=True, timeout=30, check=True,
                )
                payload = json.loads(result.stdout)
                paths = [item["path"] for item in payload["selected_files"]]
                phases = json.loads((root / ".agentpack/metrics.jsonl").read_text().splitlines()[-1])["phases"]
            else:
                result = service.route_task(root, task, timeout_s=30)
                paths = [item["path"] for item in result.selected_files]
                phases = plans[-1].phase_times if len(plans) > calls_before else {}
            elapsed = time.perf_counter() - started
            report["samples"].append({"seconds": elapsed, "selected_paths": paths, "phases": phases})

            if scenario == "delete":
                assert owner not in paths
                assert "tests/test_payment_retry.py" in paths
            else:
                assert owner in paths
            assert all((root / path).is_file() for path in paths)
            if scenario == "same_task":
                assert result is warm
                assert len(plans) == calls_before
            elif scenario != "cli":
                assert len(plans) == calls_before + 1
                if scenario == "edit":
                    assert "retry_payment_updated" in plans[-1].summaries[OWNER]["defines"]

        durations = [sample["seconds"] for sample in report["samples"]]
        report["median_seconds"] = statistics.median(durations)
        assert report["median_seconds"] <= LIMITS[scenario], report
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        output = os.environ.get("AGENTPACK_ROUTE_PERF_OUTPUT")
        if output:
            target = Path(output)
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{scenario}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
