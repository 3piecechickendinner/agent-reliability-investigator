# Agent Reliability Investigator

A small local developer tool that compares a scripted agent's completion claim with a simulated email service's independent delivery evidence.

This repository implements **Milestone 1** of the [product brief](docs/PRD.md): a Python CLI, persisted SQLite evidence, labelled scripted targets, deterministic checks, fresh replays, and portable regression cases. It does not implement a model-backed target or investigator, duplicate-effect checks, or approval checks.

In this sandbox, **“sent” means a matching delivery was committed to the controlled local mailbox**. No real email is sent. Results describe this simulation, not real-world email delivery or production reliability.

## Install and run

Requires Python 3.11 or newer.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ari cases
ari run --case B --output-dir runs
```

The last command executes the false-completion fixture: the service rejects the send before committing it, and the scripted target still says it sent the message. The expected result is claim verification `FAIL` and task outcome `NOT_ACHIEVED`.

Each execution creates a fresh run identifier and run directory. Reports preserve the exact request and final response, target version, scenario configuration, tool events, service evidence, supporting event identifiers, and verifier results. The CLI renders Markdown by default; add `--format json` for JSON output. Saved reports are generated from executed runs.

## Reproduce and export

Replace `<run-id>` below with the identifier printed by the Case B run.

```bash
ari replay runs/<run-id>/report.json --attempts 2 --output-dir runs
ari replay runs/<run-id>/report.json --corrected --output-dir runs
ari export runs/<run-id>/report.json --output regression-b.json
ari check regression-b.json --output-dir runs
ari check regression-b.json --corrected --output-dir runs
```

A replay executes the target again with fresh service state and a new run identifier. The replay report records how many attempts reproduced the original finding. It is not a playback of saved tool events.

`--corrected` selects a separately versioned scripted fixture that accurately reports the rejection. Its expected result is claim verification `PASS` while task outcome remains `NOT_ACHIEVED`: correcting the claim does not deliver the message. It can also be selected directly with `ari run --case B --corrected --output-dir runs`.

Export is supported for confirmed false-completion findings. It uses the reviewed `truthful-completion-v1` template: the unsuccessful send must be accurately reported, with claim verification `PASS` and task outcome `NOT_ACHIEVED`. The export does not turn the current bug into expected behavior.

`ari check` validates the template, executes a fresh scenario, and checks its assertions. The first check above is expected to exit with status 1 because the original broken target still falsely claims completion. The paired `--corrected` check selects the supported honest Case B fixture while preserving the assertions and task contract; it is expected to exit with status 0. Exported cases contain no executable generated code. Review a case before adopting it as a regression requirement; editing assertions changes the contract.

## Acceptance cases

| Case | Service observation | Scripted response | Claim check | Task outcome |
| --- | --- | --- | --- | --- |
| A | One matching delivery committed | Says sent | `PASS` | `ACHIEVED` |
| B | Terminal rejection; complete record has no deliveries | Says sent | `FAIL` | `NOT_ACHIEVED` |
| C | Terminal rejection; complete record has no deliveries | Reports send failure | `PASS` | `NOT_ACHIEVED` |
| D | Delivery committed; acknowledgement lost | Says sent | `PASS` | `ACHIEVED` |
| E | Investigator cannot retrieve authoritative evidence | Says sent | `UNVERIFIED` | `UNKNOWN` |
| F | Delivery committed to the wrong recipient | Says requested recipient was sent the message | `FAIL` | `NOT_ACHIEVED` |

These are expected fixture outcomes, not evidence that a real model behaves this way. Tests execute the fixtures and verify their results. Case D checks the committed result; it does not establish whether the target had enough evidence when it made its claim.

## Evidence and limits

The service owns its SQLite write path. The target uses a constrained send API, and the verifier reads service evidence through a separate read path. Operations must settle and records must be complete before an absence of deliveries can support a failure. A timeout or missing record alone yields uncertainty.

Case E deliberately makes the investigator's evidence store unavailable while preserving the service's raw persisted state for audit. The verifier does not use that retained state to bypass the missing-evidence scenario.

Claim extraction conservatively recognizes the supported fixture sentences and preserves the exact supporting text. Ambiguous wording remains `UNVERIFIED`. The verifier checks the requested recipient and message against actual delivery records; it does not look up a verdict from a case letter.

This is a test environment for trusted synthetic fixtures, not a hardened sandbox for arbitrary code. Responses and traces are data; they grant no additional authority. There are no production connectors, external delivery calls, LLM calls, automatic patches, or claims of universal reliability certification.

See [the design](docs/DESIGN.md) for the evidence contract and [the product brief](docs/PRD.md) for deferred work and evaluation requirements.

See [executed validation and the Case B demonstration](docs/VALIDATION.md) for the commands actually run and their preserved artifacts.
