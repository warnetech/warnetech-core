"""llmforge -- wiring LLMs into real codebases.

Six primitives that every serious integration ends up needing, plus a lab of
techniques that are promising but not yet proven:

    provider   the vendor seam (real, fake, record/replay, retrying)
    trace      spans and a cost ledger
    tools      typed tools, generated schemas, gated dispatch
    loop       a bounded agent loop that handles the awkward stop reasons
    context    walk -> rank -> pack a repository into a token budget
    guard      untrusted-content envelopes, secret redaction, output validation
    evals      a variance-aware suite you can gate CI on
    assurance  four coding principles compiled into gates that can fail
    audit      tamper-evident trail, deny-by-default authority, dual control
    limits     a token bucket in front of the provider
    retention  tiered pruning for traces and audit chains
    doctor     a self-test that reports rather than raises
    labs       frontier techniques, each with a falsifiable claim

Nothing here requires the Anthropic SDK to import. Everything can be exercised
against ``FakeProvider`` with no network.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .assurance import (
    AssumptionLedger,
    AssuranceError,
    Postflight,
    Preflight,
    SuccessCriteria,
    Verdict,
    Violation,
    complexity_budget,
    diff_discipline,
    gate_agent,
)
from .audit import AuditLog, DualControl, SafePolicy, audited
from .context import (
    Chunk,
    Packed,
    build_context,
    cosine_similarity,
    hash_embed,
    pack,
    rank,
    repo_map,
    walk_repo,
)
from .doctor import doctor, render_doctor
from .evals import (
    Case,
    Grade,
    Suite,
    SuiteResult,
    all_of,
    any_of,
    contains,
    excludes,
    json_valid,
    llm_judge,
    matches,
    sweep,
    under_tokens,
)
from .guard import (
    ValidationError,
    redact,
    repair_prompt,
    scan_injection,
    validate_json,
    wrap_untrusted,
)
from .limits import RateLimitExceeded, TokenBucket, with_rate_limit
from .loop import Agent, Budget, BudgetExceeded, RunResult, Step
from .provider import (
    AnthropicProvider,
    FakeProvider,
    Provider,
    RecordingProvider,
    default_provider,
    text_response,
    tool_response,
    with_retry,
)
from .retention import RetentionReport, TierPolicy, sweep_audit, sweep_jsonl, tier_for_age
from .tools import Decision, GatedPolicy, Tool, ToolPolicy, ToolRegistry, tool
from .trace import TracedProvider, Tracer, cost_usd
from .types import ProviderError, Request, Response, ToolCall, ToolResult, Usage

__all__ = [
    "Agent",
    "all_of",
    "AnthropicProvider",
    "any_of",
    "AssumptionLedger",
    "AssuranceError",
    "audited",
    "AuditLog",
    "Budget",
    "BudgetExceeded",
    "build_context",
    "Case",
    "Chunk",
    "complexity_budget",
    "contains",
    "cosine_similarity",
    "cost_usd",
    "Decision",
    "default_provider",
    "diff_discipline",
    "doctor",
    "DualControl",
    "excludes",
    "FakeProvider",
    "gate_agent",
    "GatedPolicy",
    "Grade",
    "hash_embed",
    "json_valid",
    "llm_judge",
    "matches",
    "pack",
    "Packed",
    "Postflight",
    "Preflight",
    "Provider",
    "ProviderError",
    "rank",
    "RateLimitExceeded",
    "RecordingProvider",
    "redact",
    "render_doctor",
    "repair_prompt",
    "repo_map",
    "Request",
    "Response",
    "RetentionReport",
    "RunResult",
    "SafePolicy",
    "scan_injection",
    "Step",
    "SuccessCriteria",
    "Suite",
    "SuiteResult",
    "sweep",
    "sweep_audit",
    "sweep_jsonl",
    "text_response",
    "tier_for_age",
    "TierPolicy",
    "TokenBucket",
    "Tool",
    "tool",
    "tool_response",
    "ToolCall",
    "ToolPolicy",
    "ToolRegistry",
    "ToolResult",
    "TracedProvider",
    "Tracer",
    "under_tokens",
    "Usage",
    "validate_json",
    "ValidationError",
    "Verdict",
    "Violation",
    "walk_repo",
    "with_rate_limit",
    "with_retry",
    "wrap_untrusted",
    "__version__",
]
