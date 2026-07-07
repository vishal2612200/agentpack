from agentpack.application.pack_service import _selected_file_metadata
from agentpack.core.models import SelectedFile, Symbol


def test_selected_file_metadata_includes_bounded_symbols() -> None:
    symbols = [
        Symbol(
            name=f"handler_{index}",
            kind="function",
            start_line=index + 1,
            end_line=index + 3,
            signature=f"def handler_{index}() -> None",
            summary=f"Handler {index}.",
            node_id=f"node-{index}",
            signature_hash=f"sig-{index}",
            source_hash=f"symbol-hash-{index}",
        )
        for index in range(25)
    ]
    selected = [
        SelectedFile(
            path="src/handlers.py",
            score=140.0,
            include_mode="symbols",
            reasons=["symbol keyword match"],
            symbols=symbols,
            source_hash="file-hash",
        )
    ]

    rows = _selected_file_metadata(selected)

    assert len(rows[0]["symbols"]) == 20
    assert rows[0]["symbols"][0] == {
        "name": "handler_0",
        "kind": "function",
        "start_line": 1,
        "end_line": 3,
        "signature": "def handler_0() -> None",
        "summary": "Handler 0.",
        "node_id": "node-0",
        "signature_hash": "sig-0",
        "source_hash": "symbol-hash-0",
    }
