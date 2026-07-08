from __future__ import annotations

import subprocess
from pathlib import Path

from agentpack.core.code_discipline import assess_code_discipline


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True)


def test_code_discipline_flags_missing_python_definition_anchor(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "src" / "app.py").write_text(
        "VALUE = 1\n\n"
        "def load_config(path):\n"
        "    data = path.read_text()\n"
        "    return data.strip()\n",
        encoding="utf-8",
    )

    report = assess_code_discipline(tmp_path)

    assert any(issue.kind == "missing-intent-anchor" and issue.symbol == "load_config" for issue in report.issues)


def test_code_discipline_accepts_meaningful_definition_anchor(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "src" / "app.py").write_text(
        "VALUE = 1\n\n"
        "def load_config(path):\n"
        "    \"\"\"Centralize runtime config loading so callers share one env contract.\"\"\"\n"
        "    data = path.read_text()\n"
        "    return data.strip()\n",
        encoding="utf-8",
    )

    report = assess_code_discipline(tmp_path)

    assert not any(issue.kind == "missing-intent-anchor" for issue in report.issues)


def test_code_discipline_flags_large_diff_without_tests(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    lines = ["VALUE = 1", "", "# Keep generated route table shape stable for clients.", "ROUTES = {"]
    lines.extend(f'    "route_{index}": "/v1/{index}",' for index in range(180))
    lines.append("}")
    (tmp_path / "src" / "app.py").write_text("\n".join(lines) + "\n", encoding="utf-8")

    report = assess_code_discipline(tmp_path)

    kinds = {issue.kind for issue in report.issues}
    assert "large-diff" in kinds
    assert "missing-tests" in kinds


def test_code_discipline_flags_javascript_definition_anchor(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "src" / "widget.ts").write_text(
        "export function createWidget(input: string) {\n"
        "  return { input };\n"
        "}\n",
        encoding="utf-8",
    )

    report = assess_code_discipline(tmp_path)

    assert any(issue.path == "src/widget.ts" and issue.symbol == "createWidget" for issue in report.issues)


def test_code_discipline_counts_untracked_tests(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "src" / "app.py").write_text(
        "VALUE = 1\n\n"
        "def load_config(path):\n"
        "    \"\"\"Centralize runtime config loading so callers share one env contract.\"\"\"\n"
        "    data = path.read_text()\n"
        "    return data.strip()\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_app.py").write_text("def test_load_config():\n    assert True\n", encoding="utf-8")

    report = assess_code_discipline(tmp_path)

    assert report.test_files_changed == 1
    assert not any(issue.kind == "missing-tests" for issue in report.issues)
