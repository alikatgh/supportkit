"""One chokepoint for every outbound LLM call, with PII scrubbing on by default.

The first project this served scrubbed inside its extraction stage only, which
left **nine other scripts** sending raw ticket text to the API — including its
canonical taxonomy tagger. The lesson generalises: put a cross-cutting concern
where it cannot be bypassed, not at each call site.

The contract, pinned by four tests in ``tests/test_pii.py``:

1. every call is scrubbed by default;
2. the **system** prompt is never rewritten — it is project-authored
   instructions, and rewriting a rule containing a long number would change
   model behaviour;
3. a caller may opt out per call, for payloads where a placeholder would
   corrupt the *output* rather than protect anything (translations, say);
4. the environment can turn scrubbing **off**, never force it **on** — a caller
   that opted out did so for a reason an env var must not silently undo.

Known limitation, measured on a real bilingual corpus: ``pii.py`` pseudonymises
identifiers, not names. Phone numbers in many written forms are handled;
personal names in any script are not. Anything leaving the machine still
carries first names.
"""

from __future__ import annotations

import http.client
import json
import os
import random
import time
import urllib.error
import urllib.request
from typing import Any

from .common import parse_json_object  # the canonical parser; do not re-implement
from .pii import Scrubber

DEFAULT_BASE_URL = "https://api.deepseek.com"

#: Set to 0/false/no/off to disable scrubbing globally. It can only turn it OFF.
PII_SCRUB_ENV = "SUPPORTKIT_PII_SCRUB"

# The whole transport family. urllib.error.* and ConnectionResetError subclass
# OSError; IncompleteRead / RemoteDisconnected subclass http.client.HTTPException
# and NOT URLError — a urllib-only tuple once let a mid-stream disconnect kill a
# full run in the predecessor. json.JSONDecodeError is included so a garbled or
# fenced reply is retried rather than silently becoming an empty dict.
_TRANSIENT = (OSError, http.client.HTTPException, TimeoutError, json.JSONDecodeError)


def pii_scrub_enabled(explicit: bool = True) -> bool:
    """Whether this call should pseudonymise its payload.

    ``explicit`` is the caller's own choice; the environment can only turn
    scrubbing OFF, never on. The asymmetry is deliberate — see property 4 in
    the module docstring.
    """
    if not explicit:
        return False
    return os.environ.get(PII_SCRUB_ENV, "1").strip().lower() not in {"0", "false", "no", "off"}


def resolve_base_url(base_url: str | None = None) -> str:
    return base_url or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL


def resolve_api_key(api_key: str | None = None) -> str:
    key = api_key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(
            "No API key. Set DEEPSEEK_API_KEY (or pass one explicitly). "
            "Nothing in this repo spends money without it."
        )
    return key


def call_deepseek(
    api_key: str | None,
    model: str,
    user_content: str,
    timeout: int = 60,
    *,
    system: str | None = None,
    base_url: str | None = None,
    retries: int = 3,
    max_tokens: int | None = 4096,
    scrub_pii: bool = True,
) -> dict:
    """One JSON-mode chat call. Returns the parsed JSON object.

    Args:
        api_key: explicit key, or None to read $DEEPSEEK_API_KEY / $OPENAI_API_KEY.
        model: e.g. "deepseek-chat".
        user_content: the user message. Pseudonymised before sending unless
            ``scrub_pii=False``. Keep any static prefix first — DeepSeek gives a
            prefix-cache discount on a repeated leading block.
        scrub_pii: Replace UIDs, phone numbers, e-mails, URLs, card-like runs
            and IPs with stable typed placeholders before the request leaves the
            machine. Dates and money amounts are preserved — they are analysis
            signal, not identifiers. Set False only when a placeholder would
            corrupt the *output*, e.g. a translation call that returns a
            rewritten copy of its input.

            Scrubs ``user_content`` only, never ``system``.
        retries: total attempts. Transient transport errors and truncation-free
            parse failures back off exponentially; a 429 honours Retry-After.
        max_tokens: response ceiling. Truncation raises a distinct RuntimeError
            so a caller can tell "raise max_tokens" from a real parse problem —
            and, in the predecessor, so a deterministic length failure could be
            answered by splitting the chunk instead of retrying it three times.
    """
    key = resolve_api_key(api_key)
    url = resolve_base_url(base_url) + "/chat/completions"
    if pii_scrub_enabled(scrub_pii):
        # A fresh Scrubber per call: these callers are batched and often
        # threaded, so a shared mapping would both race and link contacts that
        # have no reason to be linkable.
        user_content = Scrubber().scrub(user_content)

    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_content})

    payload: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}

    attempts = max(1, retries)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
            choice = (body.get("choices") or [{}])[0]
            if choice.get("finish_reason") == "length":
                raise RuntimeError(
                    "DeepSeek response truncated (finish_reason=length). Increase max_tokens, "
                    "or split the batch — retrying an identical over-long prompt cannot succeed."
                )
            content = (choice.get("message") or {}).get("content")
            if not content:
                raise json.JSONDecodeError("empty completion content", "", 0)
            return parse_json_object(content)
        except urllib.error.HTTPError as exc:
            # HTTPError subclasses OSError, so it must be caught BEFORE _TRANSIENT
            # for the 429 Retry-After header to be read at all.
            last_exc = exc
            if attempt == attempts - 1:
                raise
            retry_after = exc.headers.get("Retry-After") if (exc.code == 429 and exc.headers) else None
            try:
                delay = float(retry_after) if retry_after is not None else float(2 ** attempt)
            except (TypeError, ValueError):
                delay = float(2 ** attempt)
            time.sleep(delay + random.random())
        except _TRANSIENT as exc:
            last_exc = exc
            if attempt == attempts - 1:
                raise
            time.sleep((2 ** attempt) + random.random())
    raise last_exc if last_exc is not None else RuntimeError("call_deepseek: no attempt made")
