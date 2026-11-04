/**
 * labs -- techniques that are promising, implemented, and not yet proven.
 *
 * Held to a different standard than the core. Core modules must be correct and
 * boring. Lab modules must be *falsifiable*: each states a claim, ships a
 * runnable implementation, and names the measurement that would show it does
 * not work.
 *
 * The rule that keeps this honest: nothing graduates to the core without an
 * eval suite showing it beats the obvious baseline on cost, quality, or both.
 * A technique that sounds clever and measures neutral costs you latency for
 * nothing.
 *
 * The Python package carries the full set (spec-lock, critic, memory, red
 * team); this mirrors the two that most often belong in a TypeScript service.
 */

export {
  bestOfN,
  normalize,
  selfConsistency,
  type Candidate,
  type EnsembleResult,
  type SampleOptions,
} from "./ensemble.js";
export {
  DEFAULT_LADDER,
  escalate,
  labelOf,
  savingsVs,
  type Attempt,
  type LadderOptions,
  type LadderResult,
  type Rung,
  type Verifier,
} from "./ladder.js";
