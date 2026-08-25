"""The AST guard, guarded — and applied to this package's own code."""

from __future__ import annotations

from pathlib import Path

from supportkit.hygiene import check_tree, find_bare_fstrings

PACKAGE = Path(__file__).resolve().parent.parent / "supportkit"


def test_the_package_itself_is_clean() -> None:
    assert check_tree(PACKAGE) == {}


def test_it_detects_a_bare_fstring() -> None:
    """A check that cannot fail is not a check."""
    src = 'def r():\n    out = []\n    w = out.append\n    w("a")\n    f"dropped {1}"\n'
    assert find_bare_fstrings(src) == [5]


def test_a_real_docstring_is_not_flagged() -> None:
    assert find_bare_fstrings('def r():\n    """A docstring."""\n    return 1\n') == []


def test_an_fstring_that_is_used_is_not_flagged() -> None:
    src = 'def r(x):\n    y = f"used {x}"\n    print(f"also {x}")\n    return y\n'
    assert find_bare_fstrings(src) == []


def test_check_tree_skips_pycache(tmp_path: Path) -> None:
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "junk.py").write_text('f"{1}"\n', encoding="utf-8")
    (tmp_path / "real.py").write_text('f"{1}"\n', encoding="utf-8")
    assert list(check_tree(tmp_path)) == [str(tmp_path / "real.py")]
