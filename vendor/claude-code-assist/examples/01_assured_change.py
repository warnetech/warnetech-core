"""End-to-end: an assured change, from assumptions to a verified diff.

This is the flow the repository is actually arguing for. Run it with no
credentials -- it uses FakeProvider and does the whole loop offline:

    python examples/01_assured_change.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/llmforge-python/src"))

from llmforge import (  # noqa: E402
    Agent,
    Budget,
    FakeProvider,
    ToolRegistry,
    TracedProvider,
    Tracer,
    tool,
)
from llmforge.assurance import (  # noqa: E402
    AssumptionLedger,
    Postflight,
    Preflight,
    SuccessCriteria,
)
from llmforge.audit import AuditLog, SafePolicy, audited  # noqa: E402

REQUEST = "Add retry-on-503 to the uploader."

PATCH = """\
+++ b/src/upload/client.py
@@ -18,6 +18,11 @@
 def upload(path: str) -> str:
+    for attempt in range(3):
+        response = _put(path)
+        if response.status != 503:
+            return response.body
+        time.sleep(2**attempt)
     return _put(path).body
"""


@tool(parallel_safe=True)
def read_file(path: str) -> str:
    """Read a file from the repository.

    Args:
        path: Repository-relative path.
    """
    return "def upload(path): return _put(path).body"


def main() -> int:
    # ---- 1. Think Before Coding -------------------------------------------
    ledger = AssumptionLedger(REQUEST)
    ledger.assume(
        "retrying is safe",
        because="upload() PUTs to a content-addressed key, so it is idempotent",
    )
    ledger.rejected("backoff in the caller", "three call sites would each need it")

    # ---- 2. Goal-Driven Execution ------------------------------------------
    goals = (
        SuccessCriteria(REQUEST)
        .require("test_upload_retries_on_503 passes", lambda: (True, "1 passed"))
        .step("write the failing test", "it fails for the right reason")
        .step("add the retry", "the test passes")
    )

    preflight = Preflight(ledger=ledger, goals=goals).run()
    print(preflight.report())
    preflight.raise_if_blocked()   # refuses to start an unspecified run

    # ---- run, under a budget, deny-by-default, fully audited ---------------
    audit = AuditLog()
    tracer = Tracer()
    agent = audited(
        Agent(
            TracedProvider(FakeProvider(["Added a bounded retry loop."]), tracer),
            tools=ToolRegistry(read_file, policy=SafePolicy(audit, allow={"read_file"})),
            budget=Budget(max_steps=8, max_usd=1.00),
        ),
        audit,
    )
    run = agent.run(REQUEST)
    print(f"\nagent: {run.text}")
    print(f"trace: {tracer.summary()}")
    print(f"audit: {audit.verify().entries} entries, chain intact={audit.verify().ok}")

    # ---- 3 + 4. Surgical Changes and verification ---------------------------
    postflight = Postflight(
        diff=PATCH,
        request_terms=["upload", "retry"],
        allowed_paths=["src/upload/"],
        goals=goals,
        baseline_lines=2,
    ).run()
    print()
    print(postflight.report())
    return 0 if postflight.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
