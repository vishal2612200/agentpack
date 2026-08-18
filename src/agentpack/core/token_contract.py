from __future__ import annotations

from typing import Any, cast

from agentpack.core.token_estimator import estimator_mode


def build_token_contract(
    *,
    budget: int,
    token_estimate: int,
    raw_repo_tokens: int = 0,
    after_ignore_tokens: int = 0,
    selected_files: list[dict[str, Any]] | None = None,
    context_path: str = "",
    mode: str = "",
    estimator: str | None = None,
) -> dict[str, Any]:
    selected = [item for item in (selected_files or []) if isinstance(item, dict)]
    mode_counts: dict[str, int] = {}
    for item in selected:
        mode_value = item.get("mode")
        if isinstance(mode_value, str) and mode_value:
            mode_counts[mode_value] = mode_counts.get(mode_value, 0) + 1

    largest: list[dict[str, Any]] = sorted(
        (
            {
                "path": str(item.get("path") or ""),
                "mode": str(item.get("mode") or ""),
                "tokens": int(item.get("tokens") or 0),
            }
            for item in selected
            if item.get("path")
        ),
        key=lambda item: (-cast(int, item["tokens"]), cast(str, item["path"])),
    )[:8]
    usage_ratio = round(token_estimate / budget, 4) if budget > 0 else 0.0
    trimmed_modes = {
        mode_name: count
        for mode_name, count in sorted(mode_counts.items())
        if mode_name in {"summary", "symbols", "skeleton", "diff"}
    }
    recommendation = _token_recommendation(
        budget=budget,
        token_estimate=token_estimate,
        selected_count=len(selected),
        summary_count=mode_counts.get("summary", 0),
    )
    return {
        "schema_version": 1,
        "budget": budget,
        "estimated_tokens": token_estimate,
        "usage_ratio": usage_ratio,
        "raw_repo_tokens": raw_repo_tokens,
        "after_ignore_tokens": after_ignore_tokens,
        "mode": mode,
        "estimator_mode": estimator or estimator_mode(),
        "context_path": context_path,
        "selected_count": len(selected),
        "mode_counts": mode_counts,
        "largest_sections": largest,
        "trimmed_sections": trimmed_modes,
        "recommended_next_context": recommendation,
    }


def token_contract_from_metadata(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not metadata:
        return None
    existing = metadata.get("token_contract")
    if isinstance(existing, dict) and existing:
        return existing
    return build_token_contract(
        budget=int(metadata.get("budget") or 0),
        token_estimate=int(metadata.get("token_estimate") or 0),
        selected_files=metadata.get("selected_files_meta") if isinstance(metadata.get("selected_files_meta"), list) else [],
        context_path=str(metadata.get("context_path") or ""),
        mode=str(metadata.get("mode") or ""),
    )


def _token_recommendation(*, budget: int, token_estimate: int, selected_count: int, summary_count: int) -> str:
    if token_estimate <= 0:
        return "No context has been packed yet; call get_context or run agentpack next --fix."
    if budget > 0 and token_estimate > budget:
        return "Context exceeds the configured budget; narrow the task or use get_delta_context before full context."
    if budget > 0 and token_estimate >= int(budget * 0.85):
        return "Context is near the budget; use get_delta_context for follow-up reads and only refresh full context after task changes."
    if selected_count > 0 and summary_count / selected_count >= 0.7:
        return "Context is mostly summaries; keep direct rg/git evidence as source of truth and tighten task wording if selection feels broad."
    return "Context is within budget; use get_context when task/context freshness matters and get_delta_context for small follow-up reads."
