# Bug journal — supportkit

Two sections. Read section 1 before debugging anything; append to section 2 in
the same commit as every fix.

---

## 1. Patterns to scan for FIRST

Seeded from the parent projects — every bullet is a generalised lesson whose
specific fix lives in a module here.

- **A file that "can't be read" may be malformed, not your environment.**
  Check the input against its own format before rebuilding a venv. (`xlsx_safe`)
- **Two implementations of one formula need a parity test, not trust.**
  Grid of inputs through both, fail on disagreement. A 7e-9 drift was caught
  this way; drifts grow. (`statlib` vs scipy)
- **A silent fallback defeats the retry loop it sits under.** Raise on bad
  LLM output; `{}` hides transient errors. (`common.parse_json_object`)
- **A cosmetic call after the real work still crashes the script.**
  `relative_to()` raises outside the root. (`common.short_path`)
- **When a documented bug recurs, the fix is a mechanical guard.** A journal
  entry was not enough to stop the bare-f-string bug twice. (`hygiene`)
- **Scrubbing at one call site is not scrubbing.** Route through a chokepoint
  and test the chokepoint. (`deepseek_client`)
- **The retry tuple must cover the whole transport family.**
  IncompleteRead/RemoteDisconnected subclass HTTPException, not URLError; a
  urllib-only tuple let one disconnect kill a full paid run. (`deepseek_client`)

---

## 2. Chronological log

Newest first. Five lines max each.

### 2026-08-25 — repository bootstrapped
Extracted from two parent projects at the second occurrence of byte-identical
duplication. Tests ported with the modules — the four PII chokepoint tests
ship in the same commit as the client, as the parent handoff required.
