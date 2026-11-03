"""Tamper-evident audit trail and fail-safe authority controls.

An ordinary log answers "what did the agent do". It does not answer "is this
log the same log that was written", and on any path where the answer matters --
a change to something people depend on, an action someone will later be asked
to account for -- that second question is the one that gets asked.

This module provides:

* :class:`AuditLog` -- an append-only, hash-chained record. Each entry commits
  to its predecessor, so removing or editing any entry breaks the chain from
  that point forward and :meth:`AuditLog.verify` says exactly where.
* :class:`DualControl` -- the two-person rule. A high-consequence action needs
  two distinct approvers, and the approval is recorded in the chain.
* :class:`SafePolicy` -- deny-by-default tool authority: nothing runs unless it
  was explicitly allowed, which is the opposite of the usual default and the
  right one when the cost of a wrong action is asymmetric.

**What this is not.** A hash chain proves *internal* consistency. Someone who
can rewrite the whole file can rewrite the whole chain. Real tamper-evidence
needs an external anchor -- ship the head digest somewhere the agent cannot
reach (a write-only sink, a second host, a signed commit) and compare. This
module makes that cheap by exposing :attr:`AuditLog.head`; it cannot do it for
you, and pretending otherwise would be the exact kind of unearned assurance the
rest of this package exists to avoid.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .guard import redact
from .tools import Decision, Tool, ToolPolicy
from .types import ToolCall

GENESIS = "0" * 64


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Entry:
    """One immutable record in the chain."""

    seq: int
    at: float
    actor: str
    action: str
    detail: dict[str, Any]
    prev: str
    digest: str

    def recompute(self) -> str:
        """Recompute this entry's digest from its own contents."""
        body = json.dumps(
            {
                "seq": self.seq,
                "at": self.at,
                "actor": self.actor,
                "action": self.action,
                "detail": self.detail,
                "prev": self.prev,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return _digest(body)

    def to_json(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "at": self.at,
                "actor": self.actor,
                "action": self.action,
                "detail": self.detail,
                "prev": self.prev,
                "digest": self.digest,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )


@dataclass(slots=True)
class ChainCheck:
    """The result of verifying a chain."""

    ok: bool
    entries: int
    broken_at: int | None = None
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


class AuditLog:
    """Append-only, hash-chained record of everything an agent did.

    Every entry commits to the digest of the one before it, so the chain is
    verifiable end to end and any edit is localized to a sequence number.

    Secrets are redacted on write, not on read. A credential that reaches the
    log has already leaked -- it is on disk, in backups, and in whatever ships
    logs off the host. Redaction has to happen before the write or it is theatre.

    >>> log = AuditLog()
    >>> _ = log.record("agent", "tool.call", {"name": "read_file"})
    >>> _ = log.record("agent", "tool.call", {"name": "write_file"})
    >>> log.verify().ok
    True
    >>> log.entries[0].detail["name"]
    'read_file'
    """

    def __init__(self, path: str | Path | None = None, *, actor: str = "system") -> None:
        self.path = Path(path) if path else None
        self.actor = actor
        self.entries: list[Entry] = []
        if self.path and self.path.exists():
            self._load()
        elif self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- writing ------------------------------------------------------------ #

    @property
    def head(self) -> str:
        """The digest committing to the entire chain so far.

        Publish this somewhere the agent cannot write. Comparing a stored head
        against a recomputed one is what turns internal consistency into actual
        tamper-evidence.
        """
        return self.entries[-1].digest if self.entries else GENESIS

    def record(self, actor: str, action: str, detail: dict[str, Any] | None = None) -> Entry:
        """Append an entry. Returns it, so callers can cite the digest."""
        clean: dict[str, Any] = {}
        for key, value in (detail or {}).items():
            if isinstance(value, str):
                redacted, found = redact(value)
                clean[key] = redacted
                if found:
                    clean.setdefault("_redacted", []).append(f"{key}:{','.join(found)}")
            else:
                clean[key] = value

        fields = {
            "seq": len(self.entries),
            "at": time.time(),
            "actor": actor,
            "action": action,
            "detail": clean,
            "prev": self.head,
        }
        # Build once with an empty digest to compute it, then seal the entry.
        entry = Entry(**fields, digest="")
        entry = Entry(**fields, digest=entry.recompute())

        self.entries.append(entry)
        if self.path:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(entry.to_json() + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return entry

    # -- reading ------------------------------------------------------------ #

    def _load(self) -> None:
        assert self.path is not None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            self.entries.append(Entry(**data))

    def verify(self) -> ChainCheck:
        """Walk the chain and report the first entry that does not hold.

        Two independent failures are detected: an entry whose digest does not
        match its own contents (it was edited), and an entry whose ``prev`` does
        not match the previous digest (one was removed or inserted).
        """
        previous = GENESIS
        for entry in self.entries:
            if entry.prev != previous:
                return ChainCheck(
                    False, len(self.entries), entry.seq,
                    f"entry {entry.seq} links to {entry.prev[:12]}, expected {previous[:12]} "
                    "-- an entry was removed, reordered, or inserted",
                )
            if entry.recompute() != entry.digest:
                return ChainCheck(
                    False, len(self.entries), entry.seq,
                    f"entry {entry.seq} digest does not match its contents -- it was edited",
                )
            previous = entry.digest
        return ChainCheck(True, len(self.entries))

    def since(self, seq: int) -> list[Entry]:
        return [e for e in self.entries if e.seq >= seq]

    def by_action(self, action: str) -> list[Entry]:
        return [e for e in self.entries if e.action == action]

    def report(self) -> str:
        check = self.verify()
        status = "intact" if check.ok else f"BROKEN at {check.broken_at}: {check.reason}"
        lines = [f"audit: {len(self.entries)} entries, chain {status}", f"head: {self.head}"]
        for entry in self.entries[-20:]:
            lines.append(
                f"  #{entry.seq:<4} {entry.actor:<12} {entry.action:<24} "
                f"{json.dumps(entry.detail, default=str)[:70]}"
            )
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Authority
# --------------------------------------------------------------------------- #


class DualControl:
    """The two-person rule for high-consequence actions.

    One approver can be socially engineered, can be the agent wearing a
    convincing hat, or can simply be tired at 2am. Requiring two *distinct*
    approvers is the oldest control there is for actions that cannot be undone,
    and it is cheap to implement correctly.

    The approvals are recorded in the audit chain, so "who authorized this" has
    an answer that survives the incident review.

    >>> log = AuditLog()
    >>> control = DualControl(log, approvers={"alice", "bob"})
    >>> control.approve("delete-prod-index", "alice")
    False
    >>> control.approve("delete-prod-index", "bob")
    True
    """

    def __init__(self, log: AuditLog, approvers: Iterable[str], *, required: int = 2) -> None:
        self.log = log
        self.approvers = set(approvers)
        self.required = required
        self._pending: dict[str, set[str]] = {}
        if len(self.approvers) < required:
            raise ValueError(
                f"dual control needs at least {required} distinct approvers, "
                f"got {len(self.approvers)}"
            )

    def approve(self, action: str, approver: str) -> bool:
        """Record one approval. Returns True once the threshold is met."""
        if approver not in self.approvers:
            self.log.record(approver, "approval.rejected", {"action": action,
                                                            "reason": "not an authorized approver"})
            return False
        held = self._pending.setdefault(action, set())
        held.add(approver)
        self.log.record(approver, "approval.granted",
                        {"action": action, "held": len(held), "required": self.required})
        return len(held) >= self.required

    def authorized(self, action: str) -> bool:
        return len(self._pending.get(action, ())) >= self.required

    def revoke(self, action: str) -> None:
        self._pending.pop(action, None)
        self.log.record("system", "approval.revoked", {"action": action})


class SafePolicy(ToolPolicy):
    """Deny-by-default tool authority, with everything recorded.

    The usual policy allows a tool unless something objects. That default is
    right when the worst case is an error message and wrong when the worst case
    is irreversible: an agent that acquires a new tool through a config change,
    a merge, or an MCP server it did not have yesterday inherits authority
    nobody granted it.

    Here nothing runs unless its name is on the list. New tools arrive denied,
    which is noisy the first time and correct every time.

    ``dual_control`` escalates: a destructive tool additionally needs a live
    two-person approval, so "allowed to exist" and "allowed right now" stay
    separate questions.

    >>> from llmforge.tools import tool, ToolCall
    >>> @tool
    ... def read_file(path: str) -> str:
    ...     "Read a file."
    ...     return ""
    >>> log = AuditLog()
    >>> policy = SafePolicy(log, allow={"read_file"})
    >>> policy.check(read_file, ToolCall("1", "read_file", {"path": "x"})).allow
    True
    >>> policy.check(read_file, ToolCall("2", "rm_rf", {})).allow
    False
    """

    def __init__(
        self,
        log: AuditLog,
        *,
        allow: Iterable[str] = (),
        dual_control: DualControl | None = None,
        on_deny: Callable[[Tool, ToolCall, str], None] | None = None,
    ) -> None:
        self.log = log
        self.allow = set(allow)
        self.dual_control = dual_control
        self.on_deny = on_deny

    def _deny(self, tool: Tool, call: ToolCall, reason: str) -> Decision:  # noqa: A002
        self.log.record("policy", "tool.denied", {"tool": call.name, "reason": reason})
        if self.on_deny:
            self.on_deny(tool, call, reason)
        return Decision.deny(reason)

    def check(self, tool: Tool, call: ToolCall) -> Decision:  # noqa: A002
        if call.name not in self.allow:
            return self._deny(
                tool, call,
                f"'{call.name}' is not in the allowed set "
                f"({', '.join(sorted(self.allow)) or 'empty'}); "
                "authority is granted explicitly, never inherited",
            )

        needs_approval = tool.destructive and self.dual_control is not None
        if needs_approval and not self.dual_control.authorized(call.name):
            return self._deny(
                tool, call,
                f"'{call.name}' is destructive and lacks "
                f"{self.dual_control.required} approvals",
            )

        self.log.record("policy", "tool.allowed",
                        {"tool": call.name, "input": json.dumps(call.input, default=str)[:400]})
        return Decision.ok()


def audited(agent: Any, log: AuditLog) -> Any:
    """Attach an audit log to an agent: every step is recorded as it happens.

    Recording per step rather than at the end matters -- a run that crashes,
    hangs, or is killed is exactly the run whose trail you want, and an
    end-of-run write is the one that never happens.
    """
    previous_hook = agent.on_step

    def on_step(inner: Any, step: Any) -> None:
        log.record(
            "agent",
            "step",
            {
                "index": step.index,
                "stop_reason": step.response.stop_reason,
                "tools": [c.name for c in step.response.tool_calls],
                "tool_errors": sum(1 for r in step.results if r.is_error),
                "output_tokens": step.response.usage.output_tokens,
                "seconds": round(step.seconds, 3),
            },
        )
        if previous_hook is not None:
            previous_hook(inner, step)

    agent.on_step = on_step
    agent.audit = log
    return agent
