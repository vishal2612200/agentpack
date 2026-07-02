from agentpack.router.models import RouteResult
from agentpack.router.prompt_builder import build_agent_prompt, render_plain


def test_route_prompt_includes_claim_grounding_contract():
    result = RouteResult(
        task="fix auth token expiry",
        selected_files=[{"path": "src/auth.py", "include_mode": "full"}],
        selection_explanations=[{
            "path": "src/auth.py",
            "why_selected": ["path terms overlap task"],
            "top_reasons": ["filename keyword match"],
        }],
        omitted_files=[{
            "path": ".gitignore",
            "why_not_selected": ["filtered as noisy agent/config metadata"],
        }],
        evidence_checklist=["Inspect token expiry handling."],
    )

    prompt = build_agent_prompt(result)
    plain = render_plain(result)

    assert "Evidence contract:" in prompt
    assert "path:line" in prompt
    assert "Why these files:" in prompt
    assert "Why not selected:" in prompt
    assert ".agentpack/citations.json" in prompt
    assert "Why selected:" in plain
    assert "filtered as noisy" in plain
    assert "repo-code claims require `path:line`" in plain
