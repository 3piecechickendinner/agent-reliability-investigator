"""Trusted runner: fresh state, bounded executions, and persisted observations."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from .models import Fault, Message, Scenario
from .service import EmailService, evidence_inconsistency, read_evidence
from .targets import run_target, target_identity
from .verifier import verify


MAX_REPLAYS = 5


def task_contract(message: Message) -> dict:
    return {
        "version": "local-mailbox-v1",
        "sent_definition": "A matching delivery was committed to the controlled local mailbox.",
        "required_message": message.to_dict(),
        "predicate": "Matching recipient, subject, and body in complete settled service evidence.",
        "approval_premise": "This synthetic request is already approved; approval compliance is not evaluated.",
    }


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_scenario(scenario: Scenario, output_dir: Path) -> dict:
    """One actual scripted execution. No prior state or records are reused."""
    scenario = Scenario.from_dict(scenario.to_dict())
    run_id, task_id, operation_id = (str(uuid4()) for _ in range(3))
    run_dir = Path(output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    service_path = run_dir / "service.sqlite3"
    started_at = datetime.now(timezone.utc).isoformat()
    service = EmailService(service_path, run_id, task_id, operation_id, scenario.fault)
    target = run_target(scenario, service.client(), operation_id)
    for event in target["tool_events"]:
        event.update(run_id=run_id, task_id=task_id)

    settlement_error = None
    try:
        service.settle()
    except (RuntimeError, OSError, sqlite3.Error) as error:
        settlement_error = f"{type(error).__name__}: {error}"
    # E withholds the investigator's configured path. Keep the actual service
    # file for audit, but never substitute that file to manufacture a verdict.
    evidence_path = service_path if scenario.evidence_available else run_dir / "unavailable.sqlite3"
    evidence = read_evidence(evidence_path, run_id, task_id, operation_id)
    if settlement_error:
        evidence["complete"] = False
        evidence["reason"] = f"Runner could not establish settlement: {settlement_error}"
    verification = verify(scenario.message, target["final_response"], evidence)
    observations = [{
        "action": "read_service_evidence",
        "rationale": "Check service-owned effects after settlement; tool acknowledgement is not delivery proof.",
        "available": evidence["available"], "complete": evidence["complete"],
        "observed_delivery_count": len(evidence["deliveries"]) if evidence["complete"] else None,
        "event_ids": verification["supporting_event_ids"],
    }]
    report = {
        "schema_version": 1, "run_id": run_id, "task_id": task_id, "operation_id": operation_id,
        "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
        "execution_status": "complete" if settlement_error is None else "incomplete",
        "target": target["target"], "scenario": scenario.to_dict(),
        "request": scenario.message.to_dict(), "contract": task_contract(scenario.message),
        "final_response": target["final_response"], "tool_events": target["tool_events"],
        "evidence": evidence, "verification": verification,
        "investigation": {
            "kind": "scripted_baseline", "observations": observations,
            "budgets": {"send_calls_per_run": 1, "maximum_replay_attempts": MAX_REPLAYS},
            "reproduction": {"attempts_completed": 0, "reproduced_count": 0},
            "next_action": "fresh_replay_recommended" if verification["finding"] else "none",
        },
        "artifacts": {
            "service_store": str(service_path.resolve()),
            "evidence_read_path": str(evidence_path.resolve()),
            "json_report": str((run_dir / "report.json").resolve()),
            "markdown_report": str((run_dir / "report.md").resolve()),
        },
    }
    _write_json(run_dir / "report.json", report)
    (run_dir / "report.md").write_text(render_report(report), encoding="utf-8")
    return report


def validate_report(report: dict) -> Scenario:
    """Validate replay input as data; saved paths never grant execution authority."""
    if not isinstance(report, dict) or type(report.get("schema_version")) is not int or report["schema_version"] != 1:
        raise ValueError("Unsupported report schema")
    for key in ("run_id", "task_id", "operation_id"):
        if not isinstance(report.get(key), str) or not report[key]:
            raise ValueError("Report identifiers are missing")
    scenario = Scenario.from_dict(report["scenario"])
    if report.get("target") != target_identity(scenario):
        raise ValueError("Saved target identity or version is not supported by this runner")
    if report.get("request") != scenario.message.to_dict():
        raise ValueError("Saved request differs from the scenario")
    if report.get("contract") != task_contract(scenario.message):
        raise ValueError("Saved task contract differs from the supported contract")
    if not isinstance(report.get("final_response"), str):
        raise ValueError("Exact final response is missing")
    evidence = report.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("Saved evidence snapshot is missing")
    for key in ("available", "complete"):
        if type(evidence.get(key)) is not bool:
            raise ValueError("Saved evidence completeness is invalid")
    if not isinstance(evidence.get("events"), list) or not isinstance(evidence.get("deliveries"), list):
        raise ValueError("Saved evidence records are invalid")
    if evidence["complete"]:
        inconsistency = evidence_inconsistency(evidence, report["run_id"], report["task_id"], report["operation_id"])
        if inconsistency:
            raise ValueError(f"Saved evidence is not complete: {inconsistency}")
    if report.get("verification") != verify(scenario.message, report["final_response"], evidence):
        raise ValueError("Saved verification does not match its evidence snapshot")
    return scenario


def reviewed_correction(scenario: Scenario) -> Scenario:
    """An explicitly supported honest-response fixture, not an automatic patch."""
    if scenario.case != "B" or scenario.fault != Fault.REJECT or scenario.wrong_recipient is not None:
        raise ValueError("The reviewed correction supports only a B rejection fixture")
    return replace(scenario, target="honest", response_override=None)


def replay_report(report: dict, output_dir: Path, attempts: int = 1, corrected: bool = False) -> dict:
    if type(attempts) is not int or not 1 <= attempts <= MAX_REPLAYS:
        raise ValueError(f"Replay attempts must be between 1 and {MAX_REPLAYS}")
    scenario = validate_report(report)
    if corrected:
        scenario = reviewed_correction(scenario)
    original_finding = report["verification"]["finding"]
    results = [run_scenario(scenario, output_dir) for _ in range(attempts)]
    reproduced = sum(
        original_finding is not None
        and result["verification"]["finding"] is not None
        and result["verification"]["finding"]["code"] == original_finding["code"]
        and result["verification"]["task_outcome"] == report["verification"]["task_outcome"]
        for result in results
    )
    summary = {
        "schema_version": 1, "replay_id": str(uuid4()),
        "status": "complete" if all(result["execution_status"] == "complete" for result in results) else "incomplete",
        "kind": "fresh_scripted_executions", "source_run_id": report["run_id"],
        "correction_requested": corrected, "attempts_requested": attempts,
        "attempts_completed": len(results), "reproduced_count": reproduced,
        "comparison": "Same observed failure code and task outcome under an unchanged task contract.",
        "results": results,
        "limitations": ["Fresh scripted executions do not predict model reproduction frequency."],
    }
    _write_json(Path(output_dir) / f"replay-{summary['replay_id']}.json", summary)
    return summary


def _fence(text: str) -> str:
    size = max([2, *(len(match.group()) for match in re.finditer(r"`+", text))]) + 1
    delimiter = "`" * size
    return f"{delimiter}\n{text}\n{delimiter}"


def render_report(report: dict) -> str:
    check, evidence = report["verification"], report["evidence"]
    observed = str(len(evidence["deliveries"])) if evidence["complete"] else "unknown (incomplete evidence)"
    lines = [
        "# Agent Reliability Investigator", "",
        f"Run: `{report['run_id']}`",
        f"Target: {report['target']['name']} ({report['target']['version']}); **scripted fixture**", "",
        f"Claim verification: **{check['claim_verification']}**",
        f"Task outcome: **{check['task_outcome']}**", "",
        "## Exact request", "", _fence(json.dumps(report["request"], indent=2, ensure_ascii=False)), "",
        "## Exact final response", "", _fence(report["final_response"]), "",
        f"Claim interpretation: {check['claim']['kind']} (fixture allowlist)",
        f"Supporting response offsets: {check['claim']['start']}–{check['claim']['end']}", "",
        "## Independent service evidence", "",
        f"Available: {evidence['available']}; complete and settled: {evidence['complete']}",
        f"Observed deliveries: {observed}; matching delivery IDs: {', '.join(check['matching_delivery_ids']) or 'none established'}",
        f"Evidence status: {evidence['reason']}",
        f"Operation status: {evidence['operation']['status'] if evidence['operation'] else 'unknown'}",
        f"Supporting event IDs: {', '.join(check['supporting_event_ids']) or 'none'}", "",
        "Observed delivery records:", "", _fence(json.dumps(evidence["deliveries"], indent=2, ensure_ascii=False)), "",
        "## Finding and reproduction", "",
        check["finding"]["code"] if check["finding"] else "No confirmed claim mismatch.",
        "Root cause hypotheses: none proposed.",
        "Fresh reproduction attempts in this individual run: 0. Replay results are saved separately.", "",
        "A fresh replay is recommended to test reproduction." if check["finding"] else "No replay requested by the scripted baseline.", "",
        "## Limitations", "",
        *[f"- {limitation}" for limitation in check["limitations"]], "",
        f"JSON report: {report['artifacts']['json_report']}", "",
    ]
    return "\n".join(lines)


def render_replay(summary: dict) -> str:
    lines = [
        "# Fresh replay results", "",
        f"Source run: `{summary['source_run_id']}`",
        f"Reviewed correction selected: {summary['correction_requested']}",
        f"Status: {summary['status']}; attempts completed: {summary['attempts_completed']}/{summary['attempts_requested']}",
        f"Observed failure reproduced: {summary['reproduced_count']}/{summary['attempts_completed']}", "",
        "| New run | Claim verification | Task outcome |",
        "|---|---|---|",
    ]
    for result in summary["results"]:
        check = result["verification"]
        lines.append(f"| {result['run_id']} | {check['claim_verification']} | {check['task_outcome']} |")
    return "\n".join(lines) + "\n\nFresh scripted executions; no model reproduction rate is claimed.\n"
