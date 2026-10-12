"""Evidence integrity tests, independent of target response interpretation."""

from copy import deepcopy
import sqlite3

import pytest

from agent_reliability_investigator.models import Fault, Message
from agent_reliability_investigator.service import EmailService, evidence_inconsistency, read_evidence


MESSAGE = Message("sd@example.test", "Approved message", "Synthetic body")
SCOPE = ("test-run", "test-task", "test-operation")


def evidence(path):
    return read_evidence(path, *SCOPE)


@pytest.mark.parametrize("fault", [Fault.NONE, Fault.REJECT, Fault.LOST_ACK])
def test_complete_records_require_settlement(tmp_path, fault):
    path = tmp_path / "mailbox.sqlite3"
    service = EmailService(path, *SCOPE, fault)
    client = service.client()
    if fault == Fault.LOST_ACK:
        with pytest.raises(TimeoutError):
            client.send(MESSAGE)
    else:
        result = client.send(MESSAGE)
        assert result["status"] == ("rejected" if fault == Fault.REJECT else "committed")
    assert evidence(path)["complete"] is False
    service.settle()
    record = evidence(path)
    assert record["available"] is True
    assert record["complete"] is True
    assert len(record["deliveries"]) == (0 if fault == Fault.REJECT else 1)
    assert record["events"][-1]["kind"] == "operation_settled"
    if fault != Fault.REJECT:
        delivery = record["deliveries"][0]
        assert {key: delivery[key] for key in ("recipient", "subject", "body")} == MESSAGE.to_dict()
        assert delivery["event_id"] == record["events"][1]["event_id"]
    service.settle()
    assert evidence(path) == record


def test_missing_reader_does_not_create_evidence_store(tmp_path):
    path = tmp_path / "missing.sqlite3"
    record = evidence(path)
    assert record["available"] is False
    assert record["complete"] is False
    assert not path.exists()


def test_corrupt_store_is_unavailable(tmp_path):
    path = tmp_path / "corrupt.sqlite3"
    path.write_bytes(b"not sqlite evidence")
    assert evidence(path)["available"] is False
    assert evidence(path)["complete"] is False
    assert path.read_bytes() == b"not sqlite evidence"


def test_foreign_operation_does_not_establish_zero_deliveries(tmp_path):
    path = tmp_path / "mailbox.sqlite3"
    service = EmailService(path, *SCOPE, Fault.REJECT)
    service.client().send(MESSAGE)
    service.settle()
    record = read_evidence(path, "another-run", SCOPE[1], SCOPE[2])
    assert record["complete"] is False
    assert record["operation"] is None


@pytest.mark.parametrize("mutation", [
    "DELETE FROM events WHERE kind = 'operation_settled'",
    "DELETE FROM events WHERE kind = 'operation_rejected'",
    "UPDATE events SET run_id = 'foreign' WHERE kind = 'operation_rejected'",
    "UPDATE events SET payload = '{\"status\":\"committed\"}' WHERE kind = 'operation_settled'",
])
def test_rejection_requires_consistent_terminal_service_evidence(tmp_path, mutation):
    path = tmp_path / "mailbox.sqlite3"
    service = EmailService(path, *SCOPE, Fault.REJECT)
    service.client().send(MESSAGE)
    service.settle()
    with sqlite3.connect(path) as connection:
        connection.execute(mutation)
    assert evidence(path)["complete"] is False


def test_commit_requires_delivery_linked_to_its_event(tmp_path):
    path = tmp_path / "mailbox.sqlite3"
    service = EmailService(path, *SCOPE, Fault.NONE)
    service.client().send(MESSAGE)
    service.settle()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE deliveries SET event_id = 'missing-event'")
    assert evidence(path)["complete"] is False


def test_service_refuses_reused_state_and_unexecuted_settlement(tmp_path):
    path = tmp_path / "mailbox.sqlite3"
    service = EmailService(path, *SCOPE, Fault.NONE)
    with pytest.raises(RuntimeError):
        service.settle()
    with pytest.raises(FileExistsError):
        EmailService(path, *SCOPE, Fault.NONE)
    service.client().send(MESSAGE)
    with pytest.raises(RuntimeError):
        service.client().send(MESSAGE)
    service.settle()
    assert len(evidence(path)["deliveries"]) == 1


@pytest.mark.parametrize("fault", [Fault.NONE, Fault.REJECT, Fault.LOST_ACK])
def test_complete_snapshot_uses_same_structural_validator_as_live_reader(tmp_path, fault):
    path = tmp_path / "mailbox.sqlite3"
    service = EmailService(path, *SCOPE, fault)
    try:
        service.client().send(MESSAGE)
    except TimeoutError:
        assert fault == Fault.LOST_ACK
    service.settle()
    snapshot = deepcopy(evidence(path))
    assert evidence_inconsistency(snapshot, *SCOPE) is None
    # The helper examines the supplied snapshot without needing the original DB.
    path.unlink()
    assert evidence_inconsistency(snapshot, *SCOPE) is None


@pytest.mark.parametrize("mutation", [
    lambda record: record.update(events=[]),
    lambda record: record["events"].pop(),
    lambda record: record["events"].append(deepcopy(record["events"][-1])),
    lambda record: record["events"][1].update(event_id=record["events"][0]["event_id"]),
    lambda record: record["events"][1].update(sequence=1),
    lambda record: record["events"][0].update(time="invalid timestamp"),
    lambda record: record["events"][0].update(time="2026-10-12T10:00:00"),
    lambda record: record["events"][1].update(payload={}),
    lambda record: record["events"][-1].update(payload={"status": "committed"}),
    lambda record: record["events"][1].update(run_id="foreign-run"),
    lambda record: record["operation"].update(status=[]),
    lambda record: record["operation"].update(settled=True),
    lambda record: record.update(deliveries=[{"recipient": "sd@example.test"}]),
])
def test_complete_rejection_snapshot_rejects_structural_corruption(tmp_path, mutation):
    path = tmp_path / "mailbox.sqlite3"
    service = EmailService(path, *SCOPE, Fault.REJECT)
    service.client().send(MESSAGE)
    service.settle()
    snapshot = deepcopy(evidence(path))
    mutation(snapshot)
    assert isinstance(evidence_inconsistency(snapshot, *SCOPE), str)
    assert evidence(path)["complete"] is True


@pytest.mark.parametrize("mutation", [
    lambda record: record["deliveries"][0].update(event_id="unrelated-event"),
    lambda record: record["deliveries"][0].update(delivery_id="unrelated-delivery"),
    lambda record: record["deliveries"][0].update(recipient="other@example.test"),
    lambda record: record["deliveries"].append(deepcopy(record["deliveries"][0])),
])
def test_complete_committed_snapshot_requires_consistent_effect_identity(tmp_path, mutation):
    path = tmp_path / "mailbox.sqlite3"
    service = EmailService(path, *SCOPE, Fault.NONE)
    service.client().send(MESSAGE)
    service.settle()
    snapshot = deepcopy(evidence(path))
    mutation(snapshot)
    assert isinstance(evidence_inconsistency(snapshot, *SCOPE), str)
