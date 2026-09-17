"""Dotted-path access into arbitrary decoded JSON.

Supports ``a.b.c`` and list indexes ``a.b.0.c``. Returns a sentinel rather than
None for "absent", because a legitimately-null field and a missing field mean
different things when you're deciding whether a mapping matched.
"""

from __future__ import annotations

from typing import Any, Iterator

MISSING = object()


def resolve(payload: Any, path: str) -> Any:
    """Follow `path` into `payload`. Returns MISSING if any hop fails."""
    if not path:
        return MISSING
    current = payload
    for part in path.split("."):
        if isinstance(current, dict):
            if part not in current:
                return MISSING
            current = current[part]
        elif isinstance(current, (list, tuple)):
            if not part.isdigit():
                return MISSING
            index = int(part)
            if index >= len(current):
                return MISSING
            current = current[index]
        else:
            return MISSING
    return current


def resolve_first(payload: Any, paths: list[str] | str | None) -> tuple[Any, str | None]:
    """Try each path in order; return (value, winning_path).

    Empty strings and empty containers count as absent -- a blank
    ``specialRequests`` should not win over a populated fallback.
    """
    if paths is None:
        return MISSING, None
    if isinstance(paths, str):
        paths = [paths]
    for path in paths:
        value = resolve(payload, path)
        if value is MISSING or value is None:
            continue
        if isinstance(value, (str, list, dict, tuple)) and len(value) == 0:
            continue
        return value, path
    return MISSING, None


def walk(payload: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Yield every (dotted_path, scalar_value) leaf in a nested structure."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            yield from walk(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            yield from walk(value, f"{prefix}.{index}" if prefix else str(index))
    else:
        if prefix:
            yield prefix, payload
