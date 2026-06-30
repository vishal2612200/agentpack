from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class ToonParseError(ValueError):
    pass


def parse_toon(text: str) -> Any:
    parser = _ToonParser(text)
    return parser.parse()


def load_toon(path: Path) -> Any:
    return parse_toon(path.read_text(encoding="utf-8"))


class _ToonParser:
    def __init__(self, text: str) -> None:
        self.lines = [
            line.rstrip("\n")
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("@")
        ]
        self.index = 0

    def parse(self) -> Any:
        if not self.lines:
            raise ToonParseError("empty TOON input")
        first = self._content(0)
        if first.startswith("-"):
            return self._parse_list(0)
        return self._parse_object(0)

    def _parse_object(self, indent: int) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        while self.index < len(self.lines):
            line_indent = self._indent(self.index)
            if line_indent < indent:
                break
            if line_indent > indent:
                raise ToonParseError(f"unexpected indent at line {self.index + 1}")
            content = self._content(self.index)
            if content in {"{}", "[]"} or content.startswith("-"):
                break

            table_match = re.match(r"^(?P<key>.+)\[(?P<cols>.+)\]:$", content)
            list_match = re.match(r"^(?P<key>.+)\[\]:$", content)
            scalar_match = re.match(r"^(?P<key>[^:]+):\s(?P<value>.+)$", content)
            object_match = re.match(r"^(?P<key>[^:]+):$", content)

            if table_match:
                key = table_match.group("key")
                columns = [part.strip() for part in table_match.group("cols").split("|")]
                self.index += 1
                obj[key] = self._parse_table(indent + 2, columns)
                continue
            if list_match:
                key = list_match.group("key")
                self.index += 1
                obj[key] = self._parse_list_body(indent + 2)
                continue
            if scalar_match:
                obj[scalar_match.group("key")] = _parse_scalar(scalar_match.group("value"))
                self.index += 1
                continue
            if object_match:
                key = object_match.group("key")
                self.index += 1
                if self._peek(indent + 2) == "{}":
                    self.index += 1
                    obj[key] = {}
                else:
                    obj[key] = self._parse_object(indent + 2)
                continue
            raise ToonParseError(f"unrecognized object entry at line {self.index + 1}: {content}")
        return obj

    def _parse_list_body(self, indent: int) -> list[Any]:
        if self._peek(indent) == "[]":
            self.index += 1
            return []
        return self._parse_list(indent)

    def _parse_table(self, indent: int, columns: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        while self.index < len(self.lines):
            line_indent = self._indent(self.index)
            if line_indent < indent:
                break
            if line_indent != indent:
                raise ToonParseError(f"unexpected indent in table at line {self.index + 1}")
            content = self._content(self.index)
            if not content.startswith("- "):
                break
            parts = [part.strip() for part in content[2:].split(" | ")]
            if len(parts) != len(columns):
                raise ToonParseError(f"table column mismatch at line {self.index + 1}")
            rows.append({column: _parse_scalar(value) for column, value in zip(columns, parts, strict=True)})
            self.index += 1
        return rows

    def _parse_list(self, indent: int) -> list[Any]:
        items: list[Any] = []
        while self.index < len(self.lines):
            line_indent = self._indent(self.index)
            if line_indent < indent:
                break
            if line_indent != indent:
                raise ToonParseError(f"unexpected indent in list at line {self.index + 1}")
            content = self._content(self.index)
            if not content.startswith("-"):
                break

            if content == "-":
                self.index += 1
                next_content = self._peek(indent + 2)
                if next_content == "{}":
                    self.index += 1
                    items.append({})
                elif next_content == "[]":
                    self.index += 1
                    items.append([])
                elif next_content and next_content.startswith("-"):
                    items.append(self._parse_list(indent + 2))
                else:
                    items.append(self._parse_object(indent + 2))
                continue

            items.append(_parse_scalar(content[2:].strip()))
            self.index += 1
        return items

    def _peek(self, indent: int) -> str | None:
        if self.index >= len(self.lines):
            return None
        if self._indent(self.index) != indent:
            return None
        return self._content(self.index)

    def _indent(self, index: int) -> int:
        return len(self.lines[index]) - len(self.lines[index].lstrip(" "))

    def _content(self, index: int) -> str:
        return self.lines[index].strip()


def _parse_scalar(value: str) -> Any:
    if value == "null":
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith(('"', "{", "[")):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ToonParseError(f"invalid JSON scalar: {exc.msg}") from exc
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value
