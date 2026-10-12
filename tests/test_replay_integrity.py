"""Saved observations cannot bypass live evidence or supported-target contracts."""

from copy import deepcopy
import json
import sqlite3
from uuid import uuid4

import pytest

from agent_reliability_investigator.models import canonical_scenario
from agent_reliability_investigator.runner import replay_report, run_scenario
from agent_reliability_investigator.verifier import verify


def _recompute_verification(report):
    """Do not let a stale verdict be the reason malformed evidence is rejected."""
    report["verification"] = verify(
        canonical_scenario("B").message, report["final_response"], report["evidence"]
    )


def _mutate_complete_evidence(report, mutation):
    evidence = report["evidence"]
    if mutation == "missing_events":
        evidence["events"] = []
    elif mutation == "missing_settlement":
        evidence["events"] = evidence["events"][:-1]
    elif mutation == "contradictory_delivery":
        evidence["deliveries"] = [{
            "delivery_id": str(uuid4()),
            "event_id": evidence["events"][1]["event_id"],
            **{key: report[key] for key in ("run_id", "task_id", "operation_id")},
            **report["request"],
        }]
    elif mutation == "foreign_event_scope":
        evidence["events"][1]["task_id"] = str(uuid4())
    elif mutation == "foreign_operation_scope":
        evidence["operation"]["run_id"] = str(uuid4())
    elif mutation == "extra_terminal_event":
        event = deepcopy(evidence["events"][1])
        event["event_id"] = str(uuid4())
        event["sequence"] = 4
        evidence["events"].append(event)
    elif mutation == "settlement_status_disagrees":
        evidence["events"][-1]["payload"]["status"] = "committed"
    else:
        raise AssertionError(f"Unknown test mutation: {mutation}")
    _recompute_verification(report)


@pytest.mark.parametrize("mutation", [
    "missing_events", "missing_settlement", "contradictory_delivery",
    "foreign_event_scope", "foreign_operation_scope", "extra_terminal_event",
    "settlement_status_disagrees",
])
def test_malformed_complete_snapshot_is_rejected_before_replay_state(tmp_path, mutation):
    original = run_scenario(canonical_scenario("B"), tmp_path / "source")
    saved = deepcopy(original)
    _mutate_complete_evidence(saved, mutation)
    assert saved["evidence"]["available"] is True
    assert saved["evidence"]["complete"] is True
    assert saved["verification"] == verify(
        canonical_scenario("B").message, saved["final_response"], saved["evidence"]
    )

    replay_dir = tmp_path / "replay"
    with pytest.raises(ValueError):
        replay_report(saved, replay_dir)
    assert not replay_dir.exists()
    assert json.loads((tmp_path / "source" / original["run_id"] / "report.json").read_text()) == original


@pytest.mark.parametrize(("field", "value"), [
    ("kind", "model_backed"),
    ("name", "unsupported-fixture"),
    ("version", "optimistic-v999"),
])
def test_unsupported_target_identity_is_rejected_before_replay_state(tmp_path, field, value):
    original = run_scenario(canonical_scenario("B"), tmp_path / "source")
    saved = deepcopy(original)
    saved["target"][field] = value

    replay_dir = tmp_path / "replay"
    with pytest.raises(ValueError):
        replay_report(saved, replay_dir)
    assert not replay_dir.exists()


def test_target_metadata_cannot_select_a_different_supported_fixture(tmp_path):
    original = run_scenario(canonical_scenario("B"), tmp_path / "source")
    saved = deepcopy(original)
    saved["target"].update(name="honest", version="honest-v1")

    replay_dir = tmp_path / "replay"
    with pytest.raises(ValueError):
        replay_report(saved, replay_dir)
    assert not replay_dir.exists()


@pytest.mark.parametrize("field", ["predicate", "sent_definition", "version"])
def test_changed_task_contract_is_rejected_before_replay_state(tmp_path, field):
    original = run_scenario(canonical_scenario("B"), tmp_path / "source")
    saved = deepcopy(original)
    saved["contract"][field] = "A different success criterion"

    replay_dir = tmp_path / "replay"
    with pytest.raises(ValueError):
        replay_report(saved, replay_dir)
    assert not replay_dir.exists()


def test_incomplete_replay_keeps_every_attempt_and_does_not_claim_reproduction(tmp_path, monkeypatch):
    original = run_scenario(canonical_scenario("B"), tmp_path / "source")

    def fail_settlement(self):
        raise sqlite3.OperationalError("Synthetic settlement database error")

    monkeypatch.setattr("agent_reliability_investigator.runner.EmailService.settle", fail_settlement)
    replay_dir = tmp_path / "replay"
    summary = replay_report(original, replay_dir, attempts=2)

    assert summary["status"] == "incomplete"
    assert summary["source_run_id"] == original["run_id"]
    assert summary["attempts_requested"] == summary["attempts_completed"] == 2
    assert summary["reproduced_count"] == 0
    assert len(summary["results"]) == 2
    assert len({original["run_id"], *(result["run_id"] for result in summary["results"])}) == 3
    for result in summary["results"]:
        assert result["execution_status"] == "incomplete"
        assert result["verification"]["claim_verification"] == "UNVERIFIED"
        assert result["verification"]["task_outcome"] == "UNKNOWN"
        assert result["evidence"]["complete"] is False
        assert "settlement" in result["evidence"]["reason"].lower()
        assert result["verification"]["supporting_event_ids"] == []
        assert (replay_dir / result["run_id"] / "report.json").is_file()
        assert (replay_dir / result["run_id"] / "service.sqlite3").is_file()
    saved_summary = json.loads((replay_dir / f"replay-{summary['replay_id']}.json").read_text())
    assert saved_summary == summary
