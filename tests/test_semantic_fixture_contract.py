import json
from pathlib import Path


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "semantic_graph"
LANGUAGES = {"python", "javascript", "typescript", "go", "rust", "java", "kotlin", "ruby", "php"}


def test_each_core_language_has_line_grounded_fixture() -> None:
    for language in LANGUAGES:
        fixture_dir = FIXTURE_ROOT / language
        expected = json.loads((fixture_dir / "expected.json").read_text(encoding="utf-8"))
        source_files = list(fixture_dir.glob("source.*"))
        assert len(source_files) == 1, language
        assert expected["fixture_version"]
        assert expected["language"] == language
        assert isinstance(expected.get("ambiguity_cases"), list)
        assert isinstance(expected.get("unresolved_cases"), list)
        deletion = expected.get("deletion")
        assert isinstance(deletion, dict)
        assert deletion.get("path") == source_files[0].name
        assert source_files[0].name in deletion.get("expected_absent", [])
        for relationship in expected.get("relationships", []):
            assert relationship["path"].startswith("source.")
            assert relationship["start_line"] >= 1
            assert relationship.get("end_line", relationship["start_line"]) >= relationship["start_line"]
        for path_case in expected.get("paths", []):
            assert path_case["source"]
            assert path_case["target"]
            assert isinstance(path_case.get("relationships"), list)
