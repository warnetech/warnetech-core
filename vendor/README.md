# vendor/

Repositories bundled into warnetech-core so they work offline and can be called on
demand. Each is a **squashed `git subtree`**: the files are copied in, and git
records the source commit. Nothing is fetched at run time.

| Directory | Source | Commit | What it gives you |
|---|---|---|---|
| `claude-code-assist/` | warnetech/claude-code-assist, branch `claude/llm-integration-repo-1aicun` | `5e19707` | llmforge (Python and TypeScript), the `aicli` multi-model CLI, assurance gates, labs |
| `cli-ai-command-center-prd/` | warnetech/CLI-AI-Command-Center-PRD, `main` | `7d29861` | the product requirements document `aicli` is built from |

`vendor.json` is the manifest. Code looks repositories up through it, never by
joining a name onto a path.

## Use them

    warnetech vendor list                        # what is bundled, and the source commits
    warnetech vendor path claude-code-assist     # absolute path

From Python:

    from warnetech_core.integrations.vendor import vendor_path, run_aicli

    result = run_aicli(["--version"])            # runs the bundled aicli in a child process

`aicli` is a separate product with its own provider layer. It does not go through
this repo's gateway, so its calls are not covered by the gateway's budgets or
usage tracking. Run it with `WARNETECH_LOCAL_ONLY`-style discipline yourself if you
need no-network behaviour.

## Update one

    scripts/vendor_sync.sh claude-code-assist

Needs network access and a clean working tree. Then copy the new
`git-subtree-split` commit it prints into `vendor.json`.

## Things to know

- **Which branch of claude-code-assist.** Its `main` is an older snapshot without
  `aicli`. The complete work, including `aicli`, is on
  `claude/llm-integration-repo-1aicun`, so that is what is bundled. The two
  branches have diverged. Reconcile them upstream before switching the ref.
- **Do not edit files here.** Change the upstream repository and run the sync
  script, or the next update will conflict. `claude-code-assist` has its own
  rules for changing `packages/aicli` (see its `CLAUDE.md`).
- **Nested instructions.** `claude-code-assist/CLAUDE.md` and
  `claude-code-assist/packages/aicli/CLAUDE.md` load automatically when working
  inside those directories. They govern that subtree, not this repo.
- **Tests and lint.** `pytest` is scoped to `tests/` and `ruff` to `src tests`, so
  the bundled packages' own tests and style do not run as ours. Run them from
  inside the vendored package if you need them.
- **Licences.** `claude-code-assist` is MIT and its `LICENSE` is kept. The PRD
  repository declares no licence; treat it as internal until its owner adds one.
- **Test fixtures that look like keys.** The redaction tests in
  `claude-code-assist/packages/llmforge-*` contain fake keys
  (`sk-ant-api03-AAAA…`, AWS's documented `AKIAIOSFODNN7EXAMPLE`). They are not
  credentials, but a secret scanner may flag them.
