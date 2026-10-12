"""Exports execute fresh runs with fixed assertions, never generated code."""

import copy
import json
from dataclasses import replace

import pytest

from agent_reliability_investigator import runner
from agent_reliability_investigator.models import canonical_scenario
from agent_reliability_investigator.regression import check_regression, export_regression


EXPECTED_ASSERTIONS = {"claim_verification": "PASS", "task_outcome": "NOT_ACHIEVED"}


@pytest.fixture
def exported(tmp_path):
    report = runner.run_scenario(canonical_scenario("B"), tmp_path / "runs")
    path = tmp_path / "case.json"
    regression = export_regression(report, path)
    return report, regression, path


def test_export_is_portable_and_runs_original_then_reviewed_correction(tmp_path, exported):
    source, regression, path = exported
    assert json.loads(path.read_text()) == regression
    assert regression["schema_version"] == 1
    assert regression["template"] == "truthful-completion-v1"
    assert regression["scenario"] == source["scenario"]
    assert regression["assertions"] == EXPECTED_ASSERTIONS

    broken = check_regression(path, tmp_path / "fresh")
    corrected = check_regression(path, tmp_path / "fresh", corrected=True)
    assert broken["status"] == "FAIL"
    assert corrected["status"] == "PASS"
    assert broken["assertions"] == corrected["assertions"] == EXPECTED_ASSERTIONS
    assert broken["checks"] == [
        {"field": "claim_verification", "expected": "PASS", "actual": "FAIL", "passed": False},
        {"field": "task_outcome", "expected": "NOT_ACHIEVED", "actual": "NOT_ACHIEVED", "passed": True},
    ]
    assert all(check["passed"] for check in corrected["checks"])
    assert broken["source_run_id"] == corrected["source_run_id"] == source["run_id"]
    for field in ("run_id", "task_id", "operation_id"):
        assert len({source[field], broken["report"][field], corrected["report"][field]}) == 3
    for result in (broken, corrected):
        report = result["report"]
        assert report["request"] == source["request"]
        assert report["scenario"]["fault"] == source["scenario"]["fault"]
        assert report["evidence"]["complete"] is True
        assert report["evidence"]["deliveries"] == []
        assert (tmp_path / "fresh" / report["run_id"] / "service.sqlite3").is_file()
    assert json.loads(path.read_text()) == regression


def test_correction_removes_response_override_without_changing_task(tmp_path):
    scenario = replace(canonical_scenario("B"), response_override="The message was sent to sd@example.test.")
    source = runner.run_scenario(scenario, tmp_path / "runs")
    path = tmp_path / "case.json"
    export_regression(source, path)
    corrected = check_regression(path, tmp_path / "fresh", corrected=True)
    assert corrected["status"] == "PASS"
    assert corrected["report"]["scenario"]["response_override"] is None
    assert corrected["report"]["request"] == source["request"]
    assert corrected["assertions"] == EXPECTED_ASSERTIONS


def test_wrong_destination_case_can_export_but_has_no_reviewed_correction(tmp_path):
    report = runner.run_scenario(canonical_scenario("F"), tmp_path / "runs")
    path = tmp_path / "wrong-destination.json"
    export_regression(report, path)
    assert check_regression(path, tmp_path / "fresh")["status"] == "FAIL"
    with pytest.raises(ValueError):
        check_regression(path, tmp_path / "fresh", corrected=True)


@pytest.mark.parametrize("case", ["A", "C", "D", "E"])
def test_non_confirmed_false_completion_cannot_export(tmp_path, case):
    report = runner.run_scenario(canonical_scenario(case), tmp_path / "runs")
    path = tmp_path / "case.json"
    with pytest.raises(ValueError):
        export_regression(report, path)
    assert not path.exists()


def test_export_cannot_overwrite_existing_case(tmp_path, exported):
    source, _, path = exported
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        export_regression(source, path)
    assert path.read_bytes() == original


def mutate(data, change):
    """Mutations are test data, never instructions for the fixture runner."""
    if change == "template":
        data["template"] = "execute-python-v1"
    elif change == "schema_type":
        data["schema_version"] = True
    elif change == "source_run_id":
        data["source_run_id"] = "fabricated"
    elif change == "code":
        data["code"] = "raise RuntimeError('must not execute')"
    elif change == "extra_assertion":
        data["assertions"]["delivery_count"] = 0
    elif change == "claim_assertion":
        data["assertions"]["claim_verification"] = "FAIL"
    elif change == "outcome_assertion":
        data["assertions"]["task_outcome"] = "ACHIEVED"
    elif change == "scenario_code":
        data["scenario"]["shell"] = "touch forbidden"
    elif change == "target":
        data["scenario"]["target"] = "custom-code"
    elif change == "target_type":
        data["scenario"]["target"] = ["optimistic"]
    elif change == "evidence_type":
        data["scenario"]["evidence_available"] = 1
    elif change == "missing_evidence":
        data["scenario"]["evidence_available"] = False
    elif change == "missing_scenario_field":
        del data["scenario"]["fault"]
    elif change == "message_extra_field":
        data["scenario"]["message"]["command"] = "anything"
    elif change == "message_type":
        data["scenario"]["message"]["recipient"] = 123
    elif change == "achievable_task":
        data["scenario"]["fault"] = "none"
    elif change == "matching_destination":
        data["scenario"]["fault"] = "none"
        data["scenario"]["wrong_recipient"] = data["scenario"]["message"]["recipient"]
    elif change == "empty_destination":
        data["scenario"]["fault"] = "none"
        data["scenario"]["wrong_recipient"] = ""
    else:
        raise AssertionError(change)


@pytest.mark.parametrize("change", [
    "template", "schema_type", "source_run_id", "code", "extra_assertion",
    "claim_assertion", "outcome_assertion", "scenario_code", "target", "target_type",
    "evidence_type", "missing_evidence", "missing_scenario_field", "message_extra_field",
    "message_type", "achievable_task", "matching_destination", "empty_destination",
])
def test_invalid_regression_is_rejected_before_execution(tmp_path, exported, monkeypatch, change):
    _, regression, path = exported
    data = copy.deepcopy(regression)
    mutate(data, change)
    path.write_text(json.dumps(data))

    def forbidden(*args, **kwargs):
        pytest.fail("An invalid regression reached target execution")

    monkeypatch.setattr(runner, "run_scenario", forbidden)
    with pytest.raises(ValueError):
        check_regression(path, tmp_path / "fresh")
    assert not (tmp_path / "fresh").exists()


def test_duplicate_json_fields_are_rejected_before_execution(tmp_path, exported, monkeypatch):
    _, regression, path = exported
    content = json.dumps(regression)
    path.write_text('{"template": "injected", ' + content[1:])
    monkeypatch.setattr(runner, "run_scenario", lambda *args, **kwargs: pytest.fail("Unexpected execution"))
    with pytest.raises(ValueError, match="Duplicate"):
        check_regression(path, tmp_path / "fresh")


@pytest.mark.parametrize("change", ["request", "response", "verification", "evidence", "scenario"])
def test_inconsistent_source_report_cannot_export(tmp_path, exported, change):
    source, _, _ = exported
    report = copy.deepcopy(source)
    if change == "request":
        report["request"]["recipient"] = "changed@example.test"
    elif change == "response":
        report["final_response"] = "I could not send the message to sd@example.test."
    elif change == "verification":
        report["verification"]["task_outcome"] = "ACHIEVED"
    elif change == "evidence":
        report["evidence"]["complete"] = False
    elif change == "scenario":
        report["scenario"]["fault"] = "none"
    path = tmp_path / "altered-case.json"
    with pytest.raises(ValueError):
        export_regression(report, path)
    assert not path.exists()


def test_a_forged_fail_label_does_not_override_success_evidence(tmp_path):
    source = runner.run_scenario(canonical_scenario("A"), tmp_path / "runs")
    source["verification"]["claim_verification"] = "FAIL"
    source["verification"]["task_outcome"] = "NOT_ACHIEVED"
    source["verification"]["finding"] = {
        "code": "false_completion_claim", "status": "confirmed_observed_mismatch",
        "observation": "Invented", "hypotheses": [],
    }
    path = tmp_path / "forged.json"
    with pytest.raises(ValueError):
        export_regression(source, path)
    assert not path.exists()
