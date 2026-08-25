"""Small shared helpers that earned promotion by breaking twice.

Rule of thumb, carried from the projects this grew out of: a pattern is
promoted into shared code on its second occurrence, not its fifth.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def short_path(path: Path, root: Path) -> Path:
    """``path`` relative to ``root`` when it is inside it, otherwise unchanged.

    ``Path.relative_to`` RAISES for a path outside the root, and every use of
    it here is cosmetic — shortening a filename in a success message. Twice a
    bare ``relative_to(root)`` crashed a script *after* its real work finished,
    because an output directory legitimately pointed outside the repo (an
    upload folder, a pytest tmp_path). Never let a cosmetic path-formatting
    call sit after the real work with no guard.
    """
    try:
        return path.relative_to(root)
    except ValueError:
        return path


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract a JSON object from a possibly fenced / prose-wrapped LLM reply.

    This is the *canonical* version because it raises on failure, which is what
    enables caller retry loops. Silent ``{}`` fallbacks defeat retries and hide
    transient errors — the projects this grew out of shipped that bug once and
    spent a day on it.

    Raises:
        json.JSONDecodeError: when no valid JSON is present.
        ValueError: when the JSON parses but is not an object — a bare array
            or scalar must fire the retry, not silently reach callers that
            index by key.
    """
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        s = s[start : end + 1]
    obj = json.loads(s)
    if not isinstance(obj, dict):
        raise ValueError(f"expected a JSON object, got {type(obj).__name__}")
    return obj
