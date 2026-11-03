# Guardrails

## The actual threat model

Not "the model says something rude". It is:

1. **Authority confusion.** A README, issue comment, dependency changelog or
   fetched page contains text addressed to *your agent* rather than to a human,
   and the agent cannot tell the difference by default.
2. **Egress.** A secret in the context window gets echoed into a log, a PR body,
   a trace, or an outbound call.
3. **Confident wrongness.** Output that is syntactically valid and semantically
   wrong, applied without a check because it looked right.

## What each control actually buys

| Control | Buys you | Does not buy you |
|---|---|---|
| `wrap_untrusted` | consistent provenance + authority marking; the model gets the same signal the harness has | immunity — a competent injection still gets read |
| `scan_injection` | a tripwire and a place to hang a decision | detection of a competent attacker |
| `redact` | secrets do not reach logs, traces, PR bodies | secrets not reaching the model's context |
| `validate_json` | shape correctness | sense correctness |
| `SafePolicy` | authority is granted, never inherited | protection against a tool you explicitly allowed |
| `DualControl` | one compromised or tired approver is not enough | two colluding approvers |
| `AuditLog` | edits and deletions are localized and visible | protection from someone who rewrites the whole file |

Being explicit about the right column is the point. A guardrail that implies
more assurance than it earned is worse than none, because it provides cover.

## Use redaction in the right direction

Redact **outward** — logs, traces, PR bodies, error reports. Do not redact the
context you feed the model: it destroys information the model may legitimately
need, such as reviewing the very commit that leaks a key.

`AuditLog` redacts on write, not on read. A credential that reached the log has
already leaked — it is on disk, in backups, and in whatever ships logs off the
host.

## Authority is granted, never inherited

```python
from llmforge.audit import AuditLog, DualControl, SafePolicy

log = AuditLog(".llmforge/audit.jsonl")
control = DualControl(log, approvers={"alice", "bob"})
policy = SafePolicy(log, allow={"grep", "read_file", "apply_patch"}, dual_control=control)
```

The usual policy allows a tool unless something objects. That default is right
when the worst case is an error message and wrong when it is irreversible: an
agent that acquires a tool through a config change, a merge, or an MCP server
it did not have yesterday inherits authority nobody granted it.

Deny-by-default is noisy the first time and correct every time.

## The audit chain's honest limit

It proves **internal consistency**. Whoever can rewrite the file can rewrite the
chain. Real tamper-evidence needs an external anchor: publish `log.head`
somewhere the agent cannot reach — a write-only sink, a second host, a signed
commit — and compare. The library makes that cheap; it cannot do it for you.

## Red-teaming your own agent

```python
from llmforge.labs import probe_prompt

result = probe_prompt(provider, SYSTEM_PROMPT, tools.definitions())
print(result.report())
```

Keep the probes as a regression suite. The value is not the first run — it is
that six months later, when someone widens a tool's schema, the suite tells you
the boundary moved.
