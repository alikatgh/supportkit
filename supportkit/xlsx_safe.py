#!/usr/bin/env python3
"""Open .xlsx files that openpyxl refuses, because of a malformed stylesheet.

Built for a real-world workbook (exported by an internal tool) that openpyxl 3.1.5
cannot open at all — not read slowly, not read partially: `load_workbook`
raises before a single cell is reached.

    TypeError: expected <class 'openpyxl.styles.fills.Fill'>

The cause is in the *file*, not in openpyxl's version or in Python's. That
workbook's `xl/styles.xml` declares 17 fills and one of them is the empty
element `<fill/>`. `Fill.from_tree` dispatches on the first child element
(`patternFill` -> PatternFill, otherwise GradientFill) and returns None when
there are no children. The `Sequence(expected_type=Fill)` descriptor then
tries to coerce that None with `Fill(None)`, and `Fill` is an abstract base
that takes no arguments. Whatever exporter produced the file emitted a fill
slot it never filled in; Excel and LibreOffice both tolerate it.

The repair is to give that empty slot the no-fill pattern it means anyway,
in a *copy*. The original file is never modified — source data is read-only.

What this does NOT do: it does not fix any other kind of corrupt workbook.
The rewrite is scoped to empty `<fill/>` elements inside the `<fills>` block
of `xl/styles.xml`. If openpyxl raises for some other reason, that exception
propagates unchanged, because a loader that swallows unknown breakage would
hide the next surprise this file has in store.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

# An empty fill slot, with and without a separating whitespace/closing form.
_EMPTY_FILL = re.compile(r"<fill\s*/>|<fill>\s*</fill>")
_NO_FILL = "<fill><patternFill patternType=\"none\"/></fill>"


def styles_need_repair(path: Path) -> bool:
    """True if ``path`` contains the empty-`<fill/>` defect this module repairs."""
    with zipfile.ZipFile(path) as z:
        if "xl/styles.xml" not in z.namelist():
            return False
        styles = z.read("xl/styles.xml").decode("utf-8", "replace")
    return _repair_count(styles) > 0


def _repair_count(styles_xml: str) -> int:
    start = styles_xml.find("<fills")
    end = styles_xml.find("</fills>")
    if start < 0 or end < 0:
        return 0
    return len(_EMPTY_FILL.findall(styles_xml[start:end]))


def repaired_copy(src: Path, dest: Path) -> tuple[Path, int]:
    """Write a copy of ``src`` at ``dest`` with empty `<fill/>` slots filled in.

    Only `xl/styles.xml` is rewritten; every other member of the zip is copied
    byte for byte, so cell values, shared strings and drawings are untouched.

    Returns:
        ``(dest, n_repaired)``. ``n_repaired`` is 0 when the file was already
        clean, in which case ``dest`` is a plain copy.
    """
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        if "xl/styles.xml" not in names:
            shutil.copyfile(src, dest)
            return dest, 0
        styles = z.read("xl/styles.xml").decode("utf-8")
        n = _repair_count(styles)
        if n == 0:
            shutil.copyfile(src, dest)
            return dest, 0
        start = styles.find("<fills")
        end = styles.find("</fills>")
        patched = styles[:start] + _EMPTY_FILL.sub(_NO_FILL, styles[start:end]) + styles[end:]
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as out:
            for info in z.infolist():
                data = patched.encode("utf-8") if info.filename == "xl/styles.xml" else z.read(info.filename)
                out.writestr(info, data)
    return dest, n


def safe_workbook_path(src: Path, work_dir: Path) -> Path:
    """Return a path to ``src`` that openpyxl can open, repairing into ``work_dir`` if needed.

    Returns ``src`` itself when no repair is required, so callers pay nothing
    for well-formed files.
    """
    if not styles_need_repair(src):
        return src
    work_dir.mkdir(parents=True, exist_ok=True)
    dest = work_dir / f"{src.stem}.repaired.xlsx"
    repaired_copy(src, dest)
    return dest
