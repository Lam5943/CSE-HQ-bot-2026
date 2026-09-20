from dataclasses import dataclass, field
from enum import Enum


class ActionProposalStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class ActionIntentKind(str, Enum):
    NONE = "NONE"
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    MULTI_ACTION = "MULTI_ACTION"


@dataclass(frozen=True)
class ActionIntent:
    kind: ActionIntentKind
    action_type: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class KnownMember:
    user_id: str
    display_name: str
    username: str | None = None


@dataclass(frozen=True)
class ActionProposalDraft:
    action_type: str
    target_type: str | None
    target_id: int | None
    arguments: dict[str, object]
    summary: str
    expected_state: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionProposal:
    id: int
    session_id: int
    actor_id: str
    action_type: str
    target_type: str | None
    target_id: int | None
    arguments: dict[str, object]
    summary: str
    expected_state: dict[str, object]
    status: str
    source_message_id: int | None
    created_at: str
    expires_at: str
    confirmed_at: str | None = None
    confirmed_by: str | None = None
    executed_at: str | None = None
    cancelled_at: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ActionExecutionResult:
    proposal: ActionProposal
    message: str
