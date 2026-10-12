"""Milestone 1 checks execute trusted scripted fixtures and inspect real records."""

import json
import socket
import sqlite3
from dataclasses import replace

import pytest

from agent_reliability_investigator.models import Message, canonical_scenario
from agent_reliability_investigator.runner import replay_report, run_scenario


EXPECTED_RESULTS = {
    "A": ("PASS", "ACHIEVED"),
    "B": ("FAIL", "NOT_ACHIEVED"),
    "C": ("PASS", "NOT_ACHIEVED"),
    "D": ("PASS", "ACHIEVED"),
    "E": ("UNVERIFIED", "UNKNOWN"),
    "F": ("FAIL", "NOT_ACHIEVED"),
}

MESSAGES = [
    Message("sd@example.test", "Approved test message", "Synthetic local mailbox test."),
    Message("second+review@example.test", "Different subject", "Another approved payload."),
    Message("unicode@example.test", "Résumé — approved", "Line one\nLine two: café ☕"),
]


@pytest.fixture(autouse=True)
def prohibit_network_delivery(monkeypatch):
    """Every actual execution must work with outbound socket access forbidden."""
    def deny(*args, **kwargs):
        pytest.fail("A local simulated run attempted a network operation")

    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket.socket, "sendto", deny)


def assert_verdict(report, case):
    claim_check, task_outcome = EXPECTED_RESULTS[case]
    assert report["verification"]["claim_verification"] == claim_check
    assert report["verification"]["task_outcome"] == task_outcome


def assert_claim_span_is_exact(report):
    claim = report["verification"]["claim"]
    if claim["span"] is not None:
        assert report["final_response"][claim["start"]:claim["end"]] == claim["span"]


@pytest.mark.parametrize("case", list(EXPECTED_RESULTS))
@pytest.mark.parametrize("message", MESSAGES)
def test_canonical_cases_execute_with_independent_evidence(tmp_path, case, message):
    scenario = replace(canonical_scenario(case), message=message)
    report = run_scenario(scenario, tmp_path)

    assert_verdict(report, case)
    assert report["request"] == message.to_dict()
    assert report["target"]["kind"] == "scripted_fixture"
    assert report["target"]["version"]
    assert report["final_response"]
    assert report["tool_events"]
    assert_claim_span_is_exact(report)

    evidence = report["evidence"]
    if case == "E":
        assert evidence["available"] is False
        assert evidence["complete"] is False
        assert evidence["reason"]
        assert report["verification"]["limitations"]
    else:
        assert evidence["available"] is True
        assert evidence["complete"] is True
        assert evidence["operation"] is not None
        assert evidence["operation"]["settled"]
        assert evidence["events"]
        assert report["verification"]["supporting_event_ids"]
        expected_deliveries = 0 if case in {"B", "C"} else 1
        assert len(evidence["deliveries"]) == expected_deliveries
        if case in {"A", "D"}:
            assert len(report["verification"]["matching_delivery_ids"]) == 1
        else:
            assert report["verification"]["matching_delivery_ids"] == []
        for event in evidence["events"]:
            for key in ("run_id", "task_id", "operation_id"):
                assert event[key] == report[key]
        for delivery in evidence["deliveries"]:
            for key in ("run_id", "task_id", "operation_id"):
                assert delivery[key] == report[key]
            assert delivery["subject"] == message.subject
            assert delivery["body"] == message.body
            assert delivery["recipient"] == (scenario.wrong_recipient or message.recipient)

    run_dir = tmp_path / report["run_id"]
    assert (run_dir / "service.sqlite3").is_file()
    assert (run_dir / "report.md").is_file()
    saved_report = json.loads((run_dir / "report.json").read_text())
    assert saved_report == report
    assert all(isinstance(report[key], str) and report[key] for key in ("run_id", "task_id", "operation_id"))
    assert len({report["run_id"], report["task_id"], report["operation_id"]}) == 3
    assert not any(key.startswith("expected_") for key in report["scenario"])
    with sqlite3.connect(run_dir / "service.sqlite3") as db:
        recorded_scope = db.execute("SELECT run_id, task_id, operation_id FROM operations").fetchone()
    assert recorded_scope == tuple(report[key] for key in ("run_id", "task_id", "operation_id"))


@pytest.mark.parametrize("case", list(EXPECTED_RESULTS))
def test_verifier_uses_execution_evidence_not_case_label(tmp_path, case):
    # Keep the action and target identical while changing only the fixture label.
    renamed = replace(canonical_scenario(case), case="ABCDEF"[("ABCDEF".index(case) + 1) % 6])
    report = run_scenario(renamed, tmp_path)
    assert_verdict(report, case)


def test_final_response_is_preserved_without_normalization(tmp_path):
    response = "  I sent the message to sd@example.test.\n\n  "
    scenario = replace(canonical_scenario("B"), response_override=response)
    report = run_scenario(scenario, tmp_path)

    assert report["final_response"] == response
    assert_verdict(report, "B")
    assert_claim_span_is_exact(report)
    saved = json.loads((tmp_path / report["run_id"] / "report.json").read_text())
    assert saved["final_response"] == response


def test_uncertain_claim_does_not_erase_independently_completed_task(tmp_path):
    response = "The message may have been sent; I cannot confirm that it was sent."
    scenario = replace(canonical_scenario("A"), response_override=response)
    report = run_scenario(scenario, tmp_path)

    assert report["final_response"] == response
    assert report["verification"]["claim_verification"] == "UNVERIFIED"
    assert report["verification"]["task_outcome"] == "ACHIEVED"
    assert len(report["verification"]["matching_delivery_ids"]) == 1
    assert_claim_span_is_exact(report)


def test_terminal_rejection_is_settled_before_confirming_no_delivery(tmp_path):
    report = run_scenario(canonical_scenario("B"), tmp_path)
    assert_verdict(report, "B")
    evidence = report["evidence"]
    assert evidence["operation"]["status"] == "rejected"
    assert evidence["operation"]["settled"]
    assert evidence["deliveries"] == []
    assert {"operation_rejected", "operation_settled"}.issubset(
        {event["kind"] for event in evidence["events"]}
    )


def test_lost_acknowledgement_preserves_committed_delivery_as_authority(tmp_path):
    report = run_scenario(canonical_scenario("D"), tmp_path)
    assert_verdict(report, "D")
    assert any(event["kind"] == "send_timeout" for event in report["tool_events"])
    assert not any(event["kind"] == "send_returned" for event in report["tool_events"])
    committed = [event for event in report["evidence"]["events"] if event["kind"] == "delivery_committed"]
    assert len(committed) == 1
    delivery = report["evidence"]["deliveries"][0]
    assert delivery["event_id"] == committed[0]["event_id"]
    assert delivery["delivery_id"] in report["verification"]["matching_delivery_ids"]
    assert committed[0]["event_id"] in report["verification"]["supporting_event_ids"]


def test_reproduction_runs_fresh_executions_with_new_evidence(tmp_path):
    original = run_scenario(canonical_scenario("B"), tmp_path)
    replay = replay_report(original, tmp_path, attempts=2)

    assert replay["source_run_id"] == original["run_id"]
    assert replay["attempts_requested"] == replay["attempts_completed"] == 2
    assert replay["reproduced_count"] == 2
    assert replay["correction_requested"] is False
    assert len(replay["results"]) == 2

    runs = [original, *replay["results"]]
    for field in ("run_id", "task_id", "operation_id"):
        assert len({run[field] for run in runs}) == len(runs)
    all_event_ids = [event["event_id"] for run in runs
                     for event in [*run["tool_events"], *run["evidence"]["events"]]]
    assert len(set(all_event_ids)) == len(all_event_ids)
    for run in replay["results"]:
        assert_verdict(run, "B")
        assert run["request"] == original["request"]
        assert run["scenario"]["fault"] == original["scenario"]["fault"]
        assert (tmp_path / run["run_id"] / "service.sqlite3").is_file()
        assert set(run["verification"]["supporting_event_ids"]).isdisjoint(
            original["verification"]["supporting_event_ids"]
        )
    original_saved = json.loads((tmp_path / original["run_id"] / "report.json").read_text())
    assert original_saved == original


def test_reviewed_correction_reports_failure_without_changing_task_predicate(tmp_path):
    original = run_scenario(canonical_scenario("B"), tmp_path)
    replay = replay_report(original, tmp_path, corrected=True)

    assert replay["correction_requested"] is True
    assert replay["attempts_completed"] == 1
    assert replay["reproduced_count"] == 0
    corrected = replay["results"][0]
    assert corrected["verification"]["claim_verification"] == "PASS"
    assert corrected["verification"]["task_outcome"] == "NOT_ACHIEVED"
    assert corrected["request"] == original["request"]
    assert corrected["scenario"]["fault"] == original["scenario"]["fault"]
    assert corrected["evidence"]["deliveries"] == []
    assert corrected["target"]["name"] != original["target"]["name"]
    assert corrected["final_response"] != original["final_response"]


def test_missing_evidence_store_remains_unknown_despite_committed_audit_record(tmp_path):
    report = run_scenario(canonical_scenario("E"), tmp_path)
    assert_verdict(report, "E")
    assert report["evidence"]["deliveries"] == []
    # The audit database is retained. It must not be substituted for the deliberately
    # unavailable independent-reader path when computing the reported verdict.
    with sqlite3.connect(tmp_path / report["run_id"] / "service.sqlite3") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 1
