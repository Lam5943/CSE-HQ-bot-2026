from dataclasses import dataclass
from enum import Enum
from datetime import datetime


class Role(str, Enum):
    LEADER = "leader"
    CO_LEAD = "co_lead"
    MEMBER = "member"


class TaskStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"


class BugStatus(str, Enum):
    OPEN = "open"
    TRIAGED = "triaged"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class MeetingStatus(str, Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Actor:
    user_id: str
    role: Role


@dataclass(frozen=True)
class Task:
    id: int
    title: str
    description: str
    status: TaskStatus
    priority: int
    assignee_id: str | None
    deadline: str | None
    created_by: str


@dataclass(frozen=True)
class Bug:
    id: int
    title: str
    description: str
    status: BugStatus
    severity: int
    assignee_id: str | None
    created_by: str


@dataclass(frozen=True)
class ProjectDashboard:
    name: str
    description: str
    goal: str
    phase: str
    sprint: str
    deadline: str
    status: str
    updated_at: datetime
    task_total: int
    task_open: int
    task_done: int
    bug_total: int
    bug_open: int
    meetings_total: int
