"""A local mailbox and its service-owned, independently readable evidence.

The target receives a send-only client, while the runner owns the writer and
the investigator uses a read-only SQLite connection. This is an API boundary
for trusted fixtures, not isolation against hostile Python code. No network
delivery is performed. A committed mailbox row is the sandbox's definition of
"sent".
"""

import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import uuid4

from .models import Fault, Message


_SCHEMA_VERSION = "1"
_SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE operations (
    run_id TEXT NOT NULL, task_id TEXT NOT NULL, operation_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('pending', 'committed', 'rejected')),
    settled INTEGER NOT NULL CHECK (settled IN (0, 1))
);
CREATE TABLE events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL, task_id TEXT NOT NULL, operation_id TEXT NOT NULL,
    kind TEXT NOT NULL, payload TEXT NOT NULL, time TEXT NOT NULL,
    FOREIGN KEY (operation_id) REFERENCES operations(operation_id)
);
CREATE TABLE deliveries (
    delivery_id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL, task_id TEXT NOT NULL, operation_id TEXT NOT NULL,
    recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
    FOREIGN KEY (operation_id) REFERENCES operations(operation_id),
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);
"""


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


class _SendOnlyClient:
    __slots__ = ("_send_callback",)

    def __init__(self, send_callback):
        self._send_callback = send_callback

    def send(self, message: Message) -> dict:
        return self._send_callback(message)


class EmailService:
    """One operation in a fresh, persisted synthetic mailbox."""

    def __init__(self, path: Path, run_id: str, task_id: str, operation_id: str, fault: Fault):
        if any(not isinstance(value, str) or not value for value in (run_id, task_id, operation_id)):
            raise ValueError("Operation identifiers must be nonempty strings")
        self._path = Path(path)
        self._scope = (run_id, task_id, operation_id)
        self._fault = Fault(fault)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Refuse reused state rather than letting a replay inherit deliveries.
        with self._path.open("xb"):
            pass
        connection = _connect(self._path)
        try:
            with connection:
                connection.executescript(_SCHEMA)
                connection.execute("INSERT INTO metadata VALUES ('schema_version', ?)", (_SCHEMA_VERSION,))
                connection.execute("INSERT INTO operations VALUES (?, ?, ?, 'pending', 0)", self._scope)
        finally:
            connection.close()

    def client(self) -> _SendOnlyClient:
        return _SendOnlyClient(self._send)

    def _event(self, connection: sqlite3.Connection, kind: str, payload: dict) -> str:
        event_id = str(uuid4())
        connection.execute(
            "INSERT INTO events (event_id, run_id, task_id, operation_id, kind, payload, time) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, *self._scope, kind, json.dumps(payload), datetime.now(timezone.utc).isoformat()),
        )
        return event_id

    def _send(self, message: Message) -> dict:
        if not isinstance(message, Message):
            raise TypeError("The supported send tool accepts a Message")
        if any(not isinstance(value, str) or not value for value in message.to_dict().values()):
            raise ValueError("Message fields must be nonempty strings")
        connection = _connect(self._path)
        try:
            with connection:
                # Serialize the single supported operation before changing it.
                connection.execute("BEGIN IMMEDIATE")
                operation = connection.execute("SELECT status FROM operations").fetchone()
                if operation["status"] != "pending":
                    raise RuntimeError("This single-operation fixture has already been called")
                self._event(connection, "operation_started", {"message": message.to_dict()})
                if self._fault == Fault.REJECT:
                    reason = "Synthetic terminal rejection before commit"
                    event_id = self._event(connection, "operation_rejected", {"reason": reason})
                    connection.execute("UPDATE operations SET status = 'rejected'")
                    result = {"status": "rejected", "event_id": event_id, "reason": reason}
                else:
                    delivery_id = str(uuid4())
                    event_id = self._event(connection, "delivery_committed", {"delivery_id": delivery_id})
                    connection.execute(
                        "INSERT INTO deliveries VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (delivery_id, event_id, *self._scope, message.recipient, message.subject, message.body),
                    )
                    connection.execute("UPDATE operations SET status = 'committed'")
                    result = {"status": "committed", "delivery_id": delivery_id, "event_id": event_id}
        finally:
            connection.close()
        if self._fault == Fault.LOST_ACK:
            # The transaction has already committed when acknowledgement is lost.
            raise TimeoutError("Synthetic acknowledgement loss after mailbox commit")
        return result

    def settle(self) -> None:
        """Persist terminal completeness after the synchronous send finishes."""
        connection = _connect(self._path)
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                operation = connection.execute("SELECT status, settled FROM operations").fetchone()
                if operation["status"] == "pending":
                    raise RuntimeError("Cannot settle an operation that has not executed")
                if not operation["settled"]:
                    self._event(connection, "operation_settled", {"status": operation["status"]})
                    connection.execute("UPDATE operations SET settled = 1")
        finally:
            connection.close()


def _incomplete(reason: str, *, available: bool = False, operation=None, deliveries=None, events=None) -> dict:
    return {
        "available": available, "complete": False, "operation": operation,
        "deliveries": deliveries or [], "events": events or [], "reason": reason,
    }


def evidence_inconsistency(evidence: dict, run_id: str, task_id: str, operation_id: str) -> str | None:
    """Validate complete live evidence or a saved snapshot without reading paths.

    This checks structure and internal consistency, not artifact authenticity.
    Saved reports share the trusted local-fixture boundary of the live service.
    """
    if not isinstance(evidence, dict) or evidence.get("available") is not True or evidence.get("complete") is not True:
        return "Evidence is not available and complete"
    scope = (run_id, task_id, operation_id)
    if any(not isinstance(value, str) or not value for value in scope):
        return "Expected operation identifiers are invalid"
    operation = evidence.get("operation")
    deliveries = evidence.get("deliveries")
    events = evidence.get("events")
    if not isinstance(operation, dict) or not isinstance(deliveries, list) or not isinstance(events, list):
        return "Evidence operation, deliveries, or events have invalid structure"
    if any(not isinstance(row, dict) for row in deliveries + events):
        return "Evidence delivery or event rows have invalid structure"
    if any(tuple(row.get(key) for key in ("run_id", "task_id", "operation_id")) != scope
           for row in [operation] + deliveries + events):
        return "Evidence contains records belonging to another operation"
    status = operation.get("status")
    if type(operation.get("settled")) is not int or operation["settled"] != 1 or status not in ("committed", "rejected"):
        return "Operation has not reached a settled terminal state"
    expected_middle = "delivery_committed" if status == "committed" else "operation_rejected"
    if [event.get("kind") for event in events] != ["operation_started", expected_middle, "operation_settled"]:
        return "Terminal operation lacks its complete service event sequence"
    if any(type(event.get("sequence")) is not int for event in events) or [event["sequence"] for event in events] != [1, 2, 3]:
        return "Service event sequence numbers do not match the single operation"
    if any(not isinstance(event.get(key), str) or not event[key] for event in events
           for key in ("event_id", "time")):
        return "Service event identifiers or timestamps are missing"
    if len({event["event_id"] for event in events}) != len(events):
        return "Service event identifiers are not unique"
    try:
        if any(datetime.fromisoformat(event["time"]).utcoffset() is None for event in events):
            return "Service event timestamps lack timezone information"
    except ValueError:
        return "Service event timestamps are invalid"
    if any(not isinstance(event.get("payload"), dict) for event in events):
        return "Malformed service event payload"
    if set(events[0]["payload"]) != {"message"}:
        return "Operation start lacks a valid message record"
    started_message = events[0]["payload"]["message"]
    if (not isinstance(started_message, dict) or set(started_message) != {"recipient", "subject", "body"}
            or any(not isinstance(value, str) or not value for value in started_message.values())):
        return "Operation start lacks a valid message record"
    if events[-1]["payload"] != {"status": status}:
        return "Settlement event disagrees with operation status"
    terminal_payload = events[1]["payload"]
    if status == "rejected":
        if deliveries:
            return "Rejected operation has contradictory delivery evidence"
        if set(terminal_payload) != {"reason"} or not isinstance(terminal_payload["reason"], str) or not terminal_payload["reason"]:
            return "Terminal rejection lacks its service reason"
    else:
        if len(deliveries) != 1:
            return "Committed operation lacks its single delivery record"
        delivery = deliveries[0]
        if any(not isinstance(delivery.get(key), str) or not delivery[key]
               for key in ("delivery_id", "event_id", "recipient", "subject", "body")):
            return "Delivery identity or message fields are missing"
        if terminal_payload != {"delivery_id": delivery["delivery_id"]} or delivery["event_id"] != events[1]["event_id"]:
            return "Delivery does not match its commit event"
        if started_message != {key: delivery[key] for key in ("recipient", "subject", "body")}:
            return "Started message disagrees with committed delivery"
    return None


def read_evidence(path: Path, run_id: str, task_id: str, operation_id: str) -> dict:
    """Read a settled authoritative record without creating or editing a store.

    Missing, foreign, inconsistent, or unsettled records cannot establish absence
    of a delivery. Completeness is corroborated by the service's terminal events,
    identifiers, and delivery/event relationship, not merely a zero row count.
    """
    path = Path(path)
    if not path.is_file():
        return _incomplete("Evidence store is unavailable")
    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")  # All evidence belongs to one consistent snapshot.
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            return _incomplete("Evidence store failed its integrity check")
        version = connection.execute("SELECT value FROM metadata WHERE key = 'schema_version'").fetchone()
        if version is None or version[0] != _SCHEMA_VERSION:
            return _incomplete("Evidence store schema is unsupported")
        operations = [dict(row) for row in connection.execute("SELECT * FROM operations")]
        if len(operations) != 1:
            return _incomplete("Evidence store does not contain exactly one supported operation", available=True)
        operation = operations[0]
        scope = (run_id, task_id, operation_id)
        if tuple(operation.get(key) for key in ("run_id", "task_id", "operation_id")) != scope:
            return _incomplete("Evidence store belongs to another operation", available=True)
        deliveries = [dict(row) for row in connection.execute("SELECT * FROM deliveries ORDER BY delivery_id")]
        events = [dict(row) for row in connection.execute("SELECT * FROM events ORDER BY sequence")]
        for event in events:
            event["payload"] = json.loads(event["payload"])
            if not isinstance(event["payload"], dict):
                return _incomplete("Malformed service event payload", available=True)
        context = {"available": True, "operation": operation, "deliveries": deliveries, "events": events}
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            return _incomplete("Evidence has broken delivery/event references", **context)
        evidence = {**context, "complete": True, "reason": "Complete settled service record"}
        inconsistency = evidence_inconsistency(evidence, *scope)
        return _incomplete(inconsistency, **context) if inconsistency else evidence
    except (sqlite3.Error, ValueError, TypeError, KeyError, OSError):
        return _incomplete("Evidence store is unreadable or has an invalid schema")
    finally:
        if connection is not None:
            connection.close()
