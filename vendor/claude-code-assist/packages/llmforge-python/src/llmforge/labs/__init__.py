"""labs -- techniques that are promising, implemented, and not yet proven.

Everything in this package is held to a different standard than the core. Core
modules must be correct and boring. Lab modules must be *falsifiable*: each one
states a claim, ships an implementation you can actually run, and names the
measurement that would show it does not work.

The rule that keeps this honest: **nothing graduates to the core without an eval
suite showing it beats the obvious baseline on cost, quality, or both.** A
technique that sounds clever and measures neutral is a technique that costs you
latency for nothing.

    ==================  ====================================================
    module              claim
    ==================  ====================================================
    spec_lock           Freezing an executable contract before writing code
                        converts "looks right" into "is checked".
    ensemble            k independent samples plus a selector beat 1 sample
                        at the same total spend, when verification is cheaper
                        than generation.
    critic              A separate critique pass with a fixed rubric catches
                        defects a single pass misses -- for about 2 rounds,
                        after which it starts inventing problems.
    ladder              Escalating cheap -> strong on a verifier signal costs
                        far less than always-strong at similar quality.
    memory              Distilling finished runs into short reusable lessons
                        beats replaying whole transcripts.
    redteam             Adversarial framing produces runnable probes where a
                        review pass produces prose.
    ==================  ====================================================
"""

from .critic import CritiqueRound, refine
from .ensemble import EnsembleResult, best_of_n, self_consistency
from .ladder import LadderResult, Rung, escalate
from .memory import Lesson, LessonStore, distill
from .redteam import Probe, RedTeamResult, attack_code, probe_prompt
from .spec_lock import Contract, SpecLockResult, spec_lock

__all__ = [
    "Contract",
    "CritiqueRound",
    "EnsembleResult",
    "LadderResult",
    "Lesson",
    "LessonStore",
    "Probe",
    "RedTeamResult",
    "Rung",
    "SpecLockResult",
    "attack_code",
    "best_of_n",
    "distill",
    "escalate",
    "probe_prompt",
    "refine",
    "self_consistency",
    "spec_lock",
]
