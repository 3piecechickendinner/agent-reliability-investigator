# Executed Milestone 1 validation

Executed on 2026-10-12 with Python 3.12.14 and pytest 9.1.1:

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest
```

**136 test items passed.** These include parameterized cases and integrity checks, not 136 independent failure families. Tests cover 18 A–F executions with three message payloads, label-independent verification, exact response spans, conservative extraction, SQLite evidence completeness, timeout-after-commit, missing evidence, fresh replays, the reviewed B correction, regression export/check, and the CLI. The acceptance executions prohibit outbound socket operations.

The configured CI matrix additionally runs Python 3.11 and 3.13. Local validation used Python 3.12; the other versions are not claimed as locally tested.

## Case B demonstration

This command actually ran:

```bash
.venv/bin/ari run --case B --output-dir runs/demo --format json
```

It created run `6d8c37da-2b71-449b-bac0-c5add800c503`. The exact final response was:

```text
I sent the message to sd@example.test.
```

The independent service record shows a terminal rejection, a settlement event, and zero committed deliveries. The claim check is **FAIL** and the task outcome is **NOT_ACHIEVED**. Supporting service event identifiers:

- Start: `5faf3383-866c-4853-95f8-d7aacd124dca`
- Rejection: `f24722a6-92fd-4cd8-9f42-dd59c2178730`
- Settlement: `f34a4bfe-4795-4611-85a1-706605bd9ca9`

Preserved executed artifacts: [JSON report](demo/case-b.json), [readable report](demo/case-b.md), and [service-owned SQLite store](demo/case-b.sqlite3). Absolute artifact paths in these reports refer to the original execution workspace. Replay uses the saved scenario, not those paths or a playback of the saved events.

After copying the original report to `docs/demo/case-b.json`, these commands actually ran:

```bash
.venv/bin/ari replay docs/demo/case-b.json --attempts 2 --output-dir runs/demo --format json
.venv/bin/ari replay docs/demo/case-b.json --corrected --output-dir runs/demo --format json
.venv/bin/ari export docs/demo/case-b.json --output docs/demo/regression-b.json --format json
.venv/bin/ari check docs/demo/regression-b.json --output-dir runs/demo --format json
.venv/bin/ari check docs/demo/regression-b.json --corrected --output-dir runs/demo --format json
```

The first replay reproduced the observed false completion in **2 of 2 fresh scripted executions**. The correction replay did not reproduce the mismatch: claim verification was **PASS** while the task remained **NOT_ACHIEVED**. Original and replay executions have distinct run, task, operation, tool-event, and service-event identifiers.

The exported assertions remain `PASS`/`NOT_ACHIEVED`. The broken target failed those assertions and returned exit status **1**. The reviewed correction passed the same assertions and returned exit status **0**. Six actual executions are preserved across the original, two reproduction attempts, correction replay, and two regression checks.

Results: [reproduction attempts](demo/replay-b.json), [correction attempt](demo/corrected-b.json), [regression template](demo/regression-b.json), [broken check](demo/check-broken-b.json), [corrected check](demo/check-corrected-b.json), and [executed command manifest](demo/manifest.json). Export refuses to overwrite an existing file; when repeating it, choose a fresh destination.

These are labelled scripted test doubles. There were no real model calls, no held-out model evaluation, and no production reliability measurements. Milestones 2 and 3 remain unimplemented.
