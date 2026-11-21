"""Task verification contract (PB-7A).

Describes, in a bounded and typed form, which task-level conditions a planner
declares for a task. The contract is data only: it never executes anything and
never grants authority. PB-7 evaluates it later.

Only two check kinds exist in PB-7A. Field-level and value checks are deferred
to PB-7B because the current task result is a string (see PB-7A spec §36.2).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict

MAX_VERIFICATION_CHECKS = 2
"""Upper bound on checks per task. Derived from the PB-7A allowlist size."""

_MAX_REPORTED_LEN = 64
_CHECK_KEYS = frozenset({"kind"})
_CONTRACT_KEYS = frozenset({"checks"})


class VerificationCheckKind(str, Enum):
    EXECUTION_SUCCESS = "execution_success"
    RESULT_PRESENT = "result_present"


class VerificationCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: VerificationCheckKind


class TaskVerificationContract(BaseModel):
    """AND-combined checks, all of which must pass. Immutable after parse."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    checks: tuple[VerificationCheck, ...]


def _bounded(value: object) -> str:
    text = str(value)
    return text if len(text) <= _MAX_REPORTED_LEN else text[:_MAX_REPORTED_LEN] + "..."


def parse_verification(raw: Any, task_title: str) -> TaskVerificationContract | None:
    """Parse one task's ``verification`` value.

    Returns ``None`` for an absent, ``null`` or explicitly empty contract. Any other
    malformed value raises ``ValueError``; it is never downgraded to ``None``, to
    UNCERTAIN, or to PASS. The message names the task and the bounded violation only.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"task {task_title!r}: verification must be an object")
    unknown_contract = set(raw) - _CONTRACT_KEYS
    if unknown_contract:
        raise ValueError(
            f"task {task_title!r}: Unknown verification contract key: "
            f"{_bounded(sorted(unknown_contract)[0])}"
        )
    checks_raw = raw.get("checks")
    if checks_raw is None:
        raise ValueError(f"task {task_title!r}: verification.checks is required")
    if not isinstance(checks_raw, list):
        raise ValueError(f"task {task_title!r}: verification.checks must be a list")
    if not checks_raw:
        return None
    if len(checks_raw) > MAX_VERIFICATION_CHECKS:
        raise ValueError(
            f"task {task_title!r}: Verification contract exceeds maximum of "
            f"{MAX_VERIFICATION_CHECKS} checks"
        )

    allowed = {kind.value: kind for kind in VerificationCheckKind}
    seen: set[VerificationCheckKind] = set()
    checks: list[VerificationCheck] = []
    for item in checks_raw:
        if not isinstance(item, dict):
            raise ValueError(f"task {task_title!r}: each verification check must be an object")
        unknown_keys = set(item) - _CHECK_KEYS
        if unknown_keys:
            raise ValueError(
                f"task {task_title!r}: Unknown verification check key: "
                f"{_bounded(sorted(unknown_keys)[0])}"
            )
        if "kind" not in item:
            raise ValueError(f"task {task_title!r}: verification check is missing kind")
        raw_kind = item["kind"]
        if not isinstance(raw_kind, str) or raw_kind not in allowed:
            raise ValueError(
                f"task {task_title!r}: Unknown verification check kind: {_bounded(raw_kind)}"
            )
        kind = allowed[raw_kind]
        if kind in seen:
            raise ValueError(
                f"task {task_title!r}: Duplicate verification check kind: {kind.value}"
            )
        seen.add(kind)
        checks.append(VerificationCheck(kind=kind))

    return TaskVerificationContract(checks=tuple(checks))
