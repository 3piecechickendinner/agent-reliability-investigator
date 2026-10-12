"""Exercise the public CLI against actual persisted fixture executions."""

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from agent_reliability_investigator.cli import main


def run_json(capsys, arguments, *, expected_exit=0):
    status = main([*arguments, "--format", "json"])
    captured = capsys.readouterr()
    assert status == expected_exit
    assert captured.err == ""
    return json.loads(captured.out)


def assert_saved_execution(report):
    report_path = Path(report["artifacts"]["json_report"])
    assert report_path.is_absolute()
    assert json.loads(report_path.read_text()) == report
    assert Path(report["artifacts"]["markdown_report"]).is_file()
    assert Path(report["artifacts"]["service_store"]).is_file()
    with sqlite3.connect(report["artifacts"]["service_store"]) as store:
        assert store.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert store.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 1


def test_cli_executes_replays_exports_and_checks_a_false_completion(tmp_path, capsys):
    runs = tmp_path / "runs"
    source = run_json(capsys, ["run", "--case", "B", "--output-dir", str(runs)])
    assert source["verification"]["claim_verification"] == "FAIL"
    assert source["verification"]["task_outcome"] == "NOT_ACHIEVED"
    assert source["target"]["kind"] == "scripted_fixture"
    assert source["evidence"]["operation"]["status"] == "rejected"
    assert source["evidence"]["complete"] is True
    assert source["evidence"]["deliveries"] == []
    assert_saved_execution(source)
    source_path = Path(source["artifacts"]["json_report"])
    original_bytes = source_path.read_bytes()

    replay = run_json(capsys, [
        "replay", str(source_path), "--attempts", "2", "--output-dir", str(runs),
    ])
    assert replay["source_run_id"] == source["run_id"]
    assert replay["kind"] == "fresh_scripted_executions"
    assert replay["attempts_completed"] == replay["attempts_requested"] == 2
    assert replay["reproduced_count"] == 2
    assert replay["status"] == "complete"
    assert json.loads((runs / f"replay-{replay['replay_id']}.json").read_text()) == replay
    for execution in replay["results"]:
        assert execution["request"] == source["request"]
        assert execution["contract"] == source["contract"]
        assert_saved_execution(execution)

    regression_path = tmp_path / "regression-b.json"
    regression = run_json(capsys, ["export", str(source_path), "--output", str(regression_path)])
    expected = {"claim_verification": "PASS", "task_outcome": "NOT_ACHIEVED"}
    assert regression["assertions"] == expected
    assert regression["source_run_id"] == source["run_id"]
    assert json.loads(regression_path.read_text()) == regression

    broken = run_json(capsys, [
        "check", str(regression_path), "--output-dir", str(runs),
    ], expected_exit=1)
    corrected = run_json(capsys, [
        "check", str(regression_path), "--corrected", "--output-dir", str(runs),
    ])
    assert broken["status"] == "FAIL"
    assert corrected["status"] == "PASS"
    assert broken["assertions"] == corrected["assertions"] == expected
    assert broken["report"]["verification"]["claim_verification"] == "FAIL"
    assert corrected["report"]["verification"]["claim_verification"] == "PASS"
    for result in (broken, corrected):
        assert result["report"]["verification"]["task_outcome"] == "NOT_ACHIEVED"
        assert result["report"]["request"] == source["request"]
        assert result["report"]["contract"] == source["contract"]
        assert result["report"]["evidence"]["deliveries"] == []
        assert_saved_execution(result["report"])
    all_runs = [source, *replay["results"], broken["report"], corrected["report"]]
    for field in ("run_id", "task_id", "operation_id"):
        assert len({execution[field] for execution in all_runs}) == 5
    assert source_path.read_bytes() == original_bytes
    assert json.loads(regression_path.read_text()) == regression


@pytest.mark.parametrize("command", ["replay", "export", "check"])
def test_malformed_json_returns_concise_error_without_execution(tmp_path, capsys, command):
    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"unclosed":')
    output = tmp_path / "output"
    arguments = [command, str(malformed)]
    if command == "export":
        arguments += ["--output", str(output)]
    else:
        arguments += ["--output-dir", str(output)]
    assert main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("ari: ")
    assert len(captured.err.splitlines()) == 1
    assert "Traceback" not in captured.err
    assert not output.exists()


@pytest.mark.parametrize("command", ["run", "replay", "check"])
def test_incompatible_correction_returns_error_before_new_execution(tmp_path, capsys, command):
    source = run_json(capsys, ["run", "--case", "F", "--output-dir", str(tmp_path / "source")])
    source_path = source["artifacts"]["json_report"]
    output = tmp_path / "corrected"
    if command == "run":
        arguments = ["run", "--case", "F"]
    elif command == "replay":
        arguments = ["replay", source_path]
    else:
        regression_path = tmp_path / "regression-f.json"
        run_json(capsys, ["export", source_path, "--output", str(regression_path)])
        arguments = ["check", str(regression_path)]
    assert main([*arguments, "--corrected", "--output-dir", str(output)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("ari: ")
    assert "B" in captured.err
    assert len(captured.err.splitlines()) == 1
    assert "Traceback" not in captured.err
    assert not output.exists()


def test_replay_budget_is_enforced_before_execution(tmp_path, capsys):
    report = run_json(capsys, ["run", "--case", "B", "--output-dir", str(tmp_path / "source")])
    output = tmp_path / "over-budget"
    assert main([
        "replay", report["artifacts"]["json_report"], "--attempts", "6", "--output-dir", str(output),
    ]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "between 1 and 5" in captured.err
    assert not output.exists()


def test_cases_are_labelled_expected_fixture_outcomes(capsys):
    assert main(["cases"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "scripted fixtures" in captured.out
    assert "expected acceptance results" in captured.out
    assert "not benchmark measurements" in captured.out
    for case in "ABCDEF":
        assert f"| {case} |" in captured.out
    assert "UNVERIFIED" in captured.out
    assert "UNKNOWN" in captured.out


def test_python_module_entrypoint_executes_a_real_local_run(tmp_path):
    result = subprocess.run([
        sys.executable, "-m", "agent_reliability_investigator", "run", "--case", "A",
        "--output-dir", str(tmp_path / "module-runs"), "--format", "json",
    ], cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["verification"]["claim_verification"] == "PASS"
    assert report["verification"]["task_outcome"] == "ACHIEVED"
    assert len(report["evidence"]["deliveries"]) == 1
    assert_saved_execution(report)
