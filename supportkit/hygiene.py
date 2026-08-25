"""Mechanical guards for mistakes that lint does not catch.

Each helper exists because a bug happened, was written up, and then happened
AGAIN — proof that a written lesson alone is not a guard. When a documented
bug recurs, the fix is a check that fails, not a better-remembered note.
"""

from __future__ import annotations

import ast
from pathlib import Path


def find_bare_fstrings(source: str, filename: str = "<string>") -> list[int]:
    """Line numbers of f-strings evaluated as statements and thrown away.

    In a string-builder (``w = out.append``), writing ``f"..."`` instead of
    ``w(f"...")`` silently drops the sentence from the output — the code reads
    fine and the bug is only visible in the rendered document. It happened
    twice in one week across two report generators.

    ruff cannot catch it: B018 exempts strings because a bare constant string
    may be a docstring, and an f-string looks like a string to that rule. An
    f-string can never be a docstring, so any ``Expr(JoinedStr)`` is a
    discarded value.
    """
    tree = ast.parse(source, filename)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.JoinedStr)
    ]


def check_tree(root: Path, pattern: str = "*.py") -> dict[str, list[int]]:
    """Every offending file under ``root``, mapped to its line numbers.

    Wire it into a consumer repo as one test:

        def test_no_bare_fstrings():
            assert check_tree(Path("scripts")) == {}
    """
    found: dict[str, list[int]] = {}
    for path in sorted(root.rglob(pattern)):
        if "__pycache__" in path.parts:
            continue
        lines = find_bare_fstrings(path.read_text(encoding="utf-8"), str(path))
        if lines:
            found[str(path)] = lines
    return found
