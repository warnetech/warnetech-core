"""What the gates actually catch, shown by making them fire.

A gate that only ever passes is decoration. This prints the four failures the
assurance layer exists to catch:

    python examples/02_gate_catches_a_drive_by.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/llmforge-python/src"))

from llmforge.assurance import (  # noqa: E402
    AssumptionLedger,
    SuccessCriteria,
    complexity_budget,
    diff_discipline,
)

# A model asked to "add retry to the uploader" also tidied an unrelated file
# and deleted a comment it did not understand.
DIFF = """\
+++ b/src/upload/client.py
@@ -18,3 +18,4 @@
 def upload(path):
+    retry(3)
     return _put(path).body
+++ b/src/render/colors.py
@@ -1,2 +1,2 @@
-RED   = "#ff0000"
+RED = "#ff0000"
+++ b/src/auth/jwt.py
@@ -4,3 +4,2 @@
-# leeway is 60s because the auth server clock drifts; see INC-2231
 def verify(token):
     ...
"""

BLOATED = """\
class AbstractUploaderBase:
    pass


def make_uploader_factory(config, options, strategy, backend):
    try:
        return build(config)
    except Exception:
        pass
"""


def show(title: str, verdict) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")
    print(verdict.report())


def main() -> None:
    show(
        "1. Think Before Coding -- an agent that declared no assumptions",
        AssumptionLedger("rewrite the upload scheduler").check(),
    )
    show(
        "2. Simplicity First -- scaffolding many times the size of what it replaces",
        complexity_budget(BLOATED, baseline_lines=1),
    )
    show(
        "3. Surgical Changes -- a drive-by reformat and a deleted comment",
        diff_discipline(DIFF, request_terms=["upload", "retry"],
                        allowed_paths=["src/upload/"]),
    )
    show(
        "4. Goal-Driven Execution -- 'make it work' is not a criterion",
        SuccessCriteria("fix the uploader").require("make it work").check(),
    )

    print(
        "\nEvery one of these is a real failure mode from the Karpathy guidelines,\n"
        "caught mechanically instead of hoped away. See docs/07-assurance.md."
    )


if __name__ == "__main__":
    main()
