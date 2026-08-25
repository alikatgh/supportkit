# supportkit

The proven core of two support-log analysis projects — primitives that enforce
their own discipline, extracted at the second occurrence, per the rule:
*promote on the second occurrence, not the fifth.*

Both parent projects analyse the same shape of data: customer-support /
consultation logs, often bilingual, usually exported as messy Excel or CSV by
an internal tool. One module (`statlib`) had already been carried between them
**byte-identical** — which means a bug fixed in one repo silently stays in the
other. That is the framework argument in one sentence.

## What's inside

| Module | What it is | The lesson it encodes |
|---|---|---|
| `statlib` | MAE/RMSE/sMAPE, skill scores vs naive baselines, Wilson intervals, modified z (median/MAD), two-proportion z | Pinned against scipy by `tests/test_statlib_reference.py` — a Wilson boundary bug was only ever found by that cross-check. |
| `pii` | Pseudonymiser with stable typed placeholders (`<UID_1>`); preserves dates and money | Redaction destroys analysis signal; pseudonyms keep "both mentions are the *same* account". Known, measured limit: identifiers, not names. |
| `deepseek_client` | One chokepoint for every outbound LLM call, scrubbing on by default | Scrubbing at one call site once left nine scripts leaking. Four tests pin the contract: default-on, system prompt never rewritten, per-call opt-out, env can disable but never force on. |
| `xlsx_safe` | Opens workbooks openpyxl refuses (empty `<fill/>` in the stylesheet); repairs a *copy*, never the source | A file that "can't be read" may be malformed, not your environment. Two venv rebuilds were wasted learning that. |
| `common` | `short_path`, `parse_json_object` | A cosmetic `relative_to()` crashed twice *after* the real work finished; a silent `{}` fallback defeats LLM retry loops. |
| `hygiene` | AST guard for f-strings evaluated as statements | The same output-dropping bug shipped twice in one week; ruff cannot see it (B018 exempts strings). When a documented bug recurs, the fix is a mechanical check, not a better-remembered note. |

## The working rules

These are not style preferences; each cost real debugging in the parent
projects.

1. **Measure, don't read.** Run it and look. Reading code to decide what it
   does produced seven confident, specific, wrong answers in one session.
2. **Cross-check against an independent implementation.** `statlib` vs scipy;
   a second implementation finds bugs re-reading never will.
3. **A guard that cannot fail is not a guard.** After writing a test, break
   the thing it guards and confirm it goes red.
4. **A test that skips is not running.** Guard dependencies go in
   `requirements-dev.txt`, never behind `importorskip`.
5. **Chokepoints for cross-cutting concerns.** PII scrubbing lives where it
   cannot be bypassed, not at each call site.
6. **Say what a number cannot support.** A regression omitting the obvious
   confounder still returns a confident coefficient with a tiny p-value.

## What deliberately stays out

- **Loaders.** Every corpus needs its own: what counts as a fragment, how a
  bilingual date parses, what a blank quantity cell means — that is judgment,
  not plumbing. The framework defines the frame a loader must fill (`month`,
  `category`, `is_stub`, carry-your-defects semantics); it ships none.
- **Conclusions.** Findings, refusals, and "what this data cannot answer" are
  per-dataset judgment. A framework that auto-generates confident-looking
  findings is exactly the failure rule 6 exists to prevent.
- **Priority scores.** Combining volume, unresolved share and trend needs
  weights nobody has chosen.

## Where this is going

The parent projects already bake their analyses into a **static readout** —
hand-written HTML/CSS/JS, no framework, no server, served by `python -m
http.server`, shareable as a folder. The roadmap is to grow that into an
appliance for this class of data:

1. **Readout engine extraction** — template shell + baked `data/bundle.js` +
   content-digest cache stamping + the browser-side stats (`stats.js`, kept
   honest by a Python↔JS parity test).
2. **The profile-first wizard.** Drag in an export → see the profile, the
   defects, and the open decisions your data forces → answer them → get the
   readout. The rulings step is the product, not a limitation: it is where
   every silent wrong assumption dies.
3. **Bring-your-own-key, honestly.** Client-side LLM calls need two things
   before any text leaves the browser: a `pii.js` with the same parity
   guarantee as `stats.js`, and a story for CORS (most LLM APIs refuse
   browser-origin calls — so either a minimal relay or a local companion).
   Until both exist, keys stay CLI-side and every spend is dry-run by default.

Not a Streamlit competitor. Streamlit is a canvas; this is an appliance that
knows the data.

## Install

```
pip install "supportkit @ git+https://github.com/alikatgh/supportkit.git@v0.1.0"
```

## Development

```
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m ruff check . && .venv/bin/python -m pytest
```

CI runs exactly that, in a clean environment, on 3.11 and 3.13 — because the
predecessor's suite was once green locally and broken in CI.
