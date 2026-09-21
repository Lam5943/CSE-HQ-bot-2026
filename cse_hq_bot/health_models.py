from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class HealthState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class HealthComponent:
    key: str
    label: str
    state: HealthState
    detail: str


@dataclass(frozen=True)
class HealthReport:
    version: str
    overall: HealthState
    checked_at: datetime
    components: tuple[HealthComponent, ...]
