---
description: Run the assurance gates over the current change — assumptions, simplicity, surgical scope, success criteria.
---

Run this repository's assurance gates against the working tree and report the
verdict. Do not fix anything yet; report first.

1. Capture the diff: `git diff HEAD` (fall back to `git diff --cached`, then
   `git diff`, whichever is non-empty).

2. Run the gates:

```bash
cd packages/llmforge-python && PYTHONPATH=src python3 - <<'PY'
import subprocess, sys
from llmforge.assurance import complexity_budget, diff_discipline

diff = subprocess.run(["git", "diff", "HEAD"], capture_output=True, text=True,
                      cwd="../..").stdout
if not diff.strip():
    print("no changes in the working tree"); sys.exit(0)

verdict = diff_discipline(diff, request_terms=REQUEST_TERMS, allowed_paths=ALLOWED_PATHS)
added = "\n".join(l[1:] for l in diff.splitlines()
                  if l.startswith("+") and not l.startswith("+++"))
verdict = verdict + complexity_budget(added)
print(verdict.report())
sys.exit(0 if verdict.passed else 1)
PY
```

Substitute `REQUEST_TERMS` with the nouns from what the user actually asked for
and `ALLOWED_PATHS` with the paths the request authorizes. If you cannot state
those, say so — that is itself a finding, and it means the request was never
scoped.

3. Report, in this order:
   - **Blockers** — each with the file, why it fires, and what you propose.
   - **Advisory** — one line each, no elaboration.
   - **Not checked** — what the gates cannot see (behavioral correctness,
     performance, whether the change is the *right* change). Never let a green
     verdict imply more assurance than it earned.

4. For each blocker, either fix it or state plainly why the gate is wrong here.
   "The gate is wrong" is a legitimate answer and should be argued, not
   asserted — a false positive you suppress silently is how a gate stops
   being believed.
