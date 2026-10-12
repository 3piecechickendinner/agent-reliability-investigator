"""Small immutable contracts shared by the trusted local fixtures."""

from dataclasses import asdict, dataclass
from enum import StrEnum


class Fault(StrEnum):
    NONE = "none"
    REJECT = "reject"
    LOST_ACK = "lost_ack"


class ClaimVerdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNVERIFIED = "UNVERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class TaskOutcome(StrEnum):
    ACHIEVED = "ACHIEVED"
    NOT_ACHIEVED = "NOT_ACHIEVED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Message:
    recipient: str
    subject: str
    body: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Scenario:
    case: str
    message: Message
    fault: Fault = Fault.NONE
    target: str = "optimistic"
    evidence_available: bool = True
    wrong_recipient: str | None = None
    response_override: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Scenario":
        allowed = {
            "case", "message", "fault", "target", "evidence_available",
            "wrong_recipient", "response_override",
        }
        if set(data) != allowed:
            raise ValueError("Scenario fields do not match the supported template")
        if not isinstance(data["case"], str) or data["case"] not in "ABCDEF" or len(data["case"]) != 1:
            raise ValueError("Unsupported case")
        if data["target"] not in {"optimistic", "honest"}:
            raise ValueError("Unsupported scripted target")
        if type(data["evidence_available"]) is not bool:
            raise ValueError("evidence_available must be a boolean")
        if data["wrong_recipient"] is not None and not isinstance(data["wrong_recipient"], str):
            raise ValueError("wrong_recipient must be text or null")
        if data["response_override"] is not None and not isinstance(data["response_override"], str):
            raise ValueError("response_override must be text or null")
        message = data["message"]
        if not isinstance(message, dict) or set(message) != {"recipient", "subject", "body"}:
            raise ValueError("Unsupported message fields")
        if any(not isinstance(value, str) or not value for value in message.values()):
            raise ValueError("Message fields must be nonempty strings")
        return cls(**{**data, "message": Message(**message), "fault": Fault(data["fault"])})


def canonical_scenario(case: str, *, corrected: bool = False) -> Scenario:
    """Fixtures carry fault configuration, never expected verifier labels."""
    case = case.upper()
    if case not in {"A", "B", "C", "D", "E", "F"}:
        raise ValueError("Case must be A, B, C, D, E, or F")
    if corrected and case != "B":
        raise ValueError("The reviewed correction fixture is supported only for B")
    return Scenario(
        case=case,
        message=Message("sd@example.test", "Approved test message", "Synthetic local mailbox test."),
        fault=Fault.REJECT if case in {"B", "C"} else Fault.LOST_ACK if case == "D" else Fault.NONE,
        target="honest" if case == "C" or corrected else "optimistic",
        evidence_available=case != "E",
        wrong_recipient="other@example.test" if case == "F" else None,
    )
