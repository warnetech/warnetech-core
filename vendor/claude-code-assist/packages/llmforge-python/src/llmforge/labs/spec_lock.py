"""Spec-lock: freeze an executable contract before generating the code.

**Claim.** Most bad LLM code is not syntactically wrong; it is confidently
wrong about intent. The usual loop -- prompt, generate, eyeball, ship -- has no
step where intent is written down in a form a machine can check, so "looks
right" is the only gate there is.

Spec-lock inserts that step:

    1. **Draft a contract** from the request: the signature, the invariants, and
       concrete examples including the edge cases the request implies but does
       not state.
    2. **Freeze it.** A human (or a policy) approves it once. From here it is
       read-only -- the implementer is not allowed to relax it.
    3. **Generate against it**, then run the contract as a test.
    4. **On failure, feed back only the failing assertions**, never a
       reformulated request. The contract is the spec; the failure is the diff.

The frozen step is the whole trick. Without it, a model that cannot satisfy a
constraint will quietly rewrite the constraint -- and every downstream round
then agrees with the weakened version.

**How it would fail.** If the contract itself is wrong, spec-lock makes the
wrong thing efficiently and confidently. That is a real cost and it argues for
the human approval gate rather than against the technique. Measure it by
grading contracts against held-out human specs, not by grading the code.

**Measurement.** Compare pass@1 on a held-out functional test suite, plus
review time per accepted diff, against single-shot generation at equal spend.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..trace import NULL_TRACER, Tracer
from ..types import Request

CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "signature": {"type": "string"},
        "summary": {"type": "string"},
        "invariants": {"type": "array", "items": {"type": "string"}},
        "examples": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "call": {"type": "string"},
                    "expect": {"type": "string"},
                },
                "required": ["description", "call", "expect"],
                "additionalProperties": False,
            },
        },
        "edge_cases": {"type": "array", "items": {"type": "string"}},
        "out_of_scope": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "signature", "summary", "invariants", "examples"],
    "additionalProperties": False,
}

CONTRACT_SYSTEM = """\
You write executable contracts, not code. Given a request, produce the contract \
an experienced reviewer would insist on before any implementation is written.

Rules:
- Every invariant must be checkable by running code. "Should be efficient" is \
not an invariant; "runs in O(n) over the input list" is not checkable from \
outside either -- prefer "raises ValueError on an empty list".
- Examples must include the edge cases the request implies but does not state: \
empty input, boundary values, duplicate keys, unicode, timezone, None.
- out_of_scope is as important as the rest. Name what this deliberately does \
NOT handle so the implementer does not invent scope.
- Return JSON only.\
"""

IMPLEMENT_SYSTEM = """\
You implement against a frozen contract. The contract is authoritative and you \
may not change, relax, or reinterpret it.

- Satisfy every invariant and every example exactly as written.
- If the contract is impossible or self-contradictory, output a single line \
starting with `CONTRACT-CONFLICT:` explaining which clauses conflict. Do not \
guess a resolution.
- Output only the code. No prose, no fences.\
"""


@dataclass(slots=True)
class Contract:
    """A frozen, machine-checkable statement of intent."""

    name: str
    signature: str
    summary: str
    invariants: list[str] = field(default_factory=list)
    examples: list[dict[str, str]] = field(default_factory=list)
    edge_cases: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    frozen: bool = False

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Contract:
        return cls(
            name=data["name"],
            signature=data["signature"],
            summary=data["summary"],
            invariants=list(data.get("invariants", [])),
            examples=list(data.get("examples", [])),
            edge_cases=list(data.get("edge_cases", [])),
            out_of_scope=list(data.get("out_of_scope", [])),
        )

    def freeze(self) -> Contract:
        self.frozen = True
        return self

    def render(self) -> str:
        """The contract as the implementer sees it."""
        lines = [
            f"# Contract: {self.name}",
            f"Signature: {self.signature}",
            "",
            self.summary,
            "",
            "## Invariants (all must hold)",
        ]
        lines += [f"{i + 1}. {inv}" for i, inv in enumerate(self.invariants)]
        if self.examples:
            lines += ["", "## Examples (all must pass)"]
            for ex in self.examples:
                lines.append(f"- {ex['description']}")
                lines.append(f"    {ex['call']}  ->  {ex['expect']}")
        if self.edge_cases:
            lines += ["", "## Edge cases that must be handled"]
            lines += [f"- {e}" for e in self.edge_cases]
        if self.out_of_scope:
            lines += ["", "## Explicitly out of scope"]
            lines += [f"- {e}" for e in self.out_of_scope]
        return "\n".join(lines)


@dataclass(slots=True)
class SpecLockResult:
    contract: Contract
    code: str
    attempts: int
    verified: bool
    failures: list[str] = field(default_factory=list)
    conflict: str | None = None
    """Set when the implementer reported the contract itself is unsatisfiable."""


Verifier = Callable[[str, Contract], tuple[bool, str]]
"""Runs the contract against candidate code. Returns (passed, failure detail)."""


def spec_lock(
    provider: Any,
    request: str,
    *,
    verifier: Verifier | None = None,
    approve: Callable[[Contract], bool] | None = None,
    model: str = "claude-opus-5",
    contract_model: str | None = None,
    max_attempts: int = 3,
    language: str = "python",
    tracer: Tracer | None = None,
) -> SpecLockResult:
    """Draft a contract, freeze it, then implement until it verifies.

    ``verifier`` is the part you supply -- usually "write the code to a temp
    file, run pytest against generated assertions, return the failures". A
    ``None`` verifier means the contract is documentation only, which still buys
    you the review artifact but not the check.

    ``approve`` is the human gate. Returning ``False`` aborts before any code is
    generated, which is the cheapest possible place to catch a misunderstanding.
    """
    tracer = tracer or NULL_TRACER

    with tracer.span("spec_lock.contract", kind="stage"):
        drafted = provider.complete(
            Request(
                messages=[{"role": "user", "content": request}],
                model=contract_model or model,
                system=CONTRACT_SYSTEM,
                max_tokens=8000,
                output_schema=CONTRACT_SCHEMA,
                effort="high",
                metadata={"lab": "spec_lock", "stage": "contract"},
            )
        )
        contract = Contract.from_json(json.loads(drafted.text))

    if approve is not None and not approve(contract):
        return SpecLockResult(contract, "", 0, False, ["contract rejected by approver"])
    contract.freeze()

    failures: list[str] = []
    code = ""

    for attempt in range(1, max_attempts + 1):
        with tracer.span("spec_lock.implement", kind="stage", attempt=attempt):
            messages: list[dict[str, Any]] = [
                {
                    "role": "user",
                    "content": (
                        f"Implement the following contract in {language}.\n\n"
                        f"{contract.render()}"
                    ),
                }
            ]
            if failures:
                # Feed back ONLY the failing assertions. Restating the original
                # request here is what lets a model drift off the contract.
                messages.append({"role": "assistant", "content": code})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The contract check failed. Fix the implementation. "
                            "The contract has not changed and may not be modified.\n\n"
                            "Failing assertions:\n" + "\n".join(f"- {f}" for f in failures)
                        ),
                    }
                )

            response = provider.complete(
                Request(
                    messages=messages,
                    model=model,
                    system=IMPLEMENT_SYSTEM,
                    max_tokens=16000,
                    effort="high",
                    metadata={"lab": "spec_lock", "stage": "implement", "attempt": attempt},
                )
            )
            code = _strip_fences(response.text)

        if code.startswith("CONTRACT-CONFLICT:"):
            return SpecLockResult(contract, "", attempt, False, failures, conflict=code)

        if verifier is None:
            return SpecLockResult(contract, code, attempt, False, ["no verifier supplied"])

        passed, detail = verifier(code, contract)
        if passed:
            return SpecLockResult(contract, code, attempt, True, [])
        failures = [line for line in detail.splitlines() if line.strip()][:20]

    return SpecLockResult(contract, code, max_attempts, False, failures)


def _strip_fences(text: str) -> str:
    """Remove a wrapping markdown code fence if the model added one anyway."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines[1:]).strip()
