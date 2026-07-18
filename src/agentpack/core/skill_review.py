from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agentpack.core.config import load_config
from agentpack.router.discovery import discover_inventory
from agentpack.router.models import SkillArtifact
from agentpack.router.parser import parse_skill_file


@dataclass(frozen=True)
class SkillReviewWorkspace:
    skill_path: Path
    output_dir: Path
    review_path: Path
    evals_path: Path
    manifest_path: Path
    findings_path: Path
    eval_count: int


def create_skill_review_workspace(
    root: Path,
    skill_ref: str,
    *,
    output: str = "",
    eval_count: int = 20,
    force: bool = False,
) -> SkillReviewWorkspace:
    if eval_count < 4 or eval_count > 40 or eval_count % 2:
        raise ValueError("eval_count must be an even number between 4 and 40")

    skill_path = resolve_skill_path(root, skill_ref)
    skill = parse_skill_file(skill_path, root=root, source="skill-review")
    slug = _slug(skill.name or skill_path.parent.name)
    output_dir = Path(output).expanduser() if output else root / ".agentpack" / "skill-reviews" / slug / "iteration-1"
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    output_dir = output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not force:
        raise ValueError(f"output directory is not empty: {output_dir}; use --force to overwrite generated files")
    output_dir.mkdir(parents=True, exist_ok=True)

    findings = _audit_skill(skill_path, skill)
    evals = _generate_eval_set(skill, eval_count)
    generated_at = datetime.now(timezone.utc).isoformat()
    skill_hash = hashlib.sha256(skill.raw_text.encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": 1,
        "generated_at": generated_at,
        "skill_name": skill.name,
        "skill_path": skill.path,
        "skill_sha256": skill_hash,
        "iteration": 1,
        "eval_count": eval_count,
        "review_status": "candidate_eval_set_requires_human_review",
    }

    manifest_path = output_dir / "manifest.json"
    findings_path = output_dir / "findings.json"
    evals_path = output_dir / "evals.json"
    review_path = output_dir / "review.md"
    _write_json(manifest_path, manifest)
    _write_json(findings_path, {"schema_version": 1, "skill": _skill_summary(skill), "findings": findings})
    _write_json(evals_path, {"skill_name": skill.name, "skill_description": skill.description, "evals": evals})
    review_path.write_text(_render_runbook(root, skill, output_dir, findings, evals), encoding="utf-8")

    return SkillReviewWorkspace(
        skill_path=skill_path,
        output_dir=output_dir,
        review_path=review_path,
        evals_path=evals_path,
        manifest_path=manifest_path,
        findings_path=findings_path,
        eval_count=eval_count,
    )


def resolve_skill_path(root: Path, skill_ref: str) -> Path:
    ref = skill_ref.strip()
    if not ref:
        raise ValueError("provide a skill path or name with --skill")

    candidate = Path(ref).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_dir():
        candidate = candidate / "SKILL.md"
    if candidate.is_file():
        return candidate.resolve()

    inventory = discover_inventory(root, load_config(root).skills.paths)
    normalized = ref.lower().removesuffix("/skill.md").removesuffix("/skill")
    matches = [
        item
        for item in inventory.skills
        if item.name.lower() == normalized
        or item.path.lower().removesuffix("/skill.md") == normalized
        or Path(item.path).parent.name.lower() == normalized
        or Path(item.path).stem.lower() == normalized
    ]
    if len(matches) == 1:
        match = matches[0]
        path = Path(match.path).expanduser()
        return (path if path.is_absolute() else root / path).resolve()
    if len(matches) > 1:
        choices = ", ".join(sorted(item.path for item in matches))
        raise ValueError(f"skill name is ambiguous; use a path: {choices}")
    raise ValueError(f"skill not found: {skill_ref}")


def _audit_skill(path: Path, skill: SkillArtifact) -> list[dict[str, str]]:
    text = skill.raw_text
    lines = text.splitlines()
    has_frontmatter = text.startswith("---\n") and "\n---" in text[4:]
    checks = [
        ("frontmatter", has_frontmatter, "SKILL.md has YAML frontmatter." if has_frontmatter else "Add YAML frontmatter with name and description."),
        ("name", bool(re.search(r"^name:\s*\S+", text, re.MULTILINE)), "Skill name is declared." if re.search(r"^name:\s*\S+", text, re.MULTILINE) else "Declare a stable name in frontmatter."),
        ("description", bool(re.search(r"^description:\s*\S+", text, re.MULTILINE)), "Trigger description is present." if re.search(r"^description:\s*\S+", text, re.MULTILINE) else "Add a specific trigger description that says what the skill does and when it applies."),
        ("body-size", len(lines) <= 500, f"Skill body is {len(lines)} lines." if len(lines) <= 500 else f"Skill body is {len(lines)} lines; move detailed material into references."),
        ("workflow", bool(re.search(r"(^|\n)#+\s+.*(step|workflow|process|instructions|usage)", text, re.IGNORECASE)), "A workflow-oriented section is present." if re.search(r"(^|\n)#+\s+.*(step|workflow|process|instructions|usage)", text, re.IGNORECASE) else "Add a concise workflow or usage section."),
        ("output-contract", bool(re.search(r"(^|\n)#+\s+.*(output|result|format|report)", text, re.IGNORECASE)), "An output contract is described." if re.search(r"(^|\n)#+\s+.*(output|result|format|report)", text, re.IGNORECASE) else "State the expected output shape so evals can assert it."),
        ("trigger-surface", bool(skill.triggers), f"Detected {len(skill.triggers)} trigger term(s)." if skill.triggers else "No useful trigger terms were detected; improve the description."),
    ]
    findings: list[dict[str, str]] = []
    for check, passed, message in checks:
        findings.append({
            "check": check,
            "severity": "pass" if passed else ("error" if check in {"frontmatter", "name", "description"} else "warning"),
            "status": "pass" if passed else "fail",
            "message": message,
            "evidence": str(path),
        })
    return findings


def _generate_eval_set(skill: SkillArtifact, count: int) -> list[dict]:
    positive_count = count // 2
    terms = skill.triggers[:6] or skill.domains[:6] or [skill.name]
    intent = _clean_sentence(skill.description) or f"a task covered by {skill.name}"
    positive_templates = [
        "I need help with {term}. Please follow the {name} workflow and explain the result.",
        "Can you handle this request using the {name} skill: {intent}?",
        "Please apply the specialized {name} process to a real repository task involving {term}.",
        "I am working on {term}; use the relevant skill instructions, including their checks and output format.",
        "Use {name} for this multi-step request and make the expected deliverable explicit: {intent}.",
    ]
    negative_templates = [
        "Give me a short general explanation of {term}; do not apply a specialized repository skill.",
        "Answer this as a simple one-step question about {term}; do not inspect files or run a skill workflow.",
        "I need an unrelated coding answer, not the {name} process. Keep the response to one paragraph.",
        "Summarize the concept of {term} for a beginner without changing files or invoking a skill.",
        "This is a general discussion of {term}, not a request to perform the {name} workflow.",
    ]
    evals: list[dict] = []
    for index in range(positive_count):
        template = positive_templates[index % len(positive_templates)]
        term = terms[index % len(terms)]
        evals.append(_eval_case(index + 1, template.format(term=term, name=skill.name, intent=intent), True, skill))
    for index in range(count - positive_count):
        template = negative_templates[index % len(negative_templates)]
        term = terms[index % len(terms)]
        evals.append(_eval_case(positive_count + index + 1, template.format(term=term, name=skill.name), False, skill))
    return evals


def _eval_case(number: int, query: str, should_trigger: bool, skill: SkillArtifact) -> dict:
    return {
        "id": f"eval-{number:02d}",
        "prompt": query,
        "files": [],
        "should_trigger": should_trigger,
        "expected_output": (
            f"The agent uses {skill.name}, follows its workflow, and produces the documented output."
            if should_trigger
            else "The agent answers directly without invoking this specialized skill."
        ),
        "assertions": [
            "trigger decision matches should_trigger",
            "response follows the expected output contract",
        ],
        "review_status": "candidate",
    }


def _render_runbook(root: Path, skill: SkillArtifact, output: Path, findings: list[dict], evals: list[dict]) -> str:
    passed = sum(item["status"] == "pass" for item in findings)
    triggered = sum(item["should_trigger"] for item in evals)
    return f"""# AgentPack Skill Review

This workspace was generated for `{skill.name}`. It is a candidate review and eval set, not proof that the skill works.

## Target

- Skill: `{skill.name}`
- Source: `{skill.path}`
- Candidate evals: `{len(evals)}` ({triggered} should-trigger, {len(evals) - triggered} should-not-trigger)
- Deterministic checks passing: `{passed}/{len(findings)}`

## Workflow

1. Read the target `SKILL.md`, `findings.json`, and `evals.json`.
2. Review and edit the candidate queries so positives are realistic and negatives are genuine near-misses.
3. Run every eval with the current skill and a baseline without it. Keep each run's prompt, transcript, output, timing, and token metadata under a new `iteration-N/` directory.
4. Grade assertions programmatically where possible; record human feedback separately from formal grades.
5. Compare pass rate, trigger precision/recall, latency, and token cost. Do not claim improvement from a single example.
6. Apply only targeted skill changes, then repeat with the previous iteration as the comparison point.

## Artifacts

- `manifest.json`: immutable target hash and run metadata.
- `findings.json`: deterministic SKILL.md audit.
- `evals.json`: candidate trigger/non-trigger eval set for human review.
- `review.md`: this runbook.

## Boundary

AgentPack generates local evidence and candidate cases. The host agent owns model execution, grading, human review, and any skill edits. No hosted model API is called by this command.

Output directory: `{output}`
Repository: `{root}`
"""


def _skill_summary(skill: SkillArtifact) -> dict:
    return {
        "name": skill.name,
        "path": skill.path,
        "description": skill.description,
        "triggers": skill.triggers,
        "anti_triggers": skill.anti_triggers,
        "side_effect_level": skill.side_effect_level,
    }


def _clean_sentence(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().rstrip("."))


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "skill"


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
