# CLAUDE.md

Start here if you are a person or an AI agent arriving in a fresh clone. This
file is the map. `scripts/check_layout.py` checks it in CI, so it stays true.

## Map

```
packages/llmforge-python/      llmforge, Python library: provider, loop, context, guard, evals, assurance, audit, labs
packages/llmforge-typescript/  llmforge, TypeScript library (@llmforge/core): the same core, mirrored
packages/aicli/                aicli, multi-model CLI; a git subtree of warnetech/CLI-AI-Command-Center-PRD
fixtures/                      parity cases both llmforge implementations must pass
labs/                          one frontier technique per numbered folder: claim, protocol, failure, measurement
docs/                          numbered essays (00-09), read in order; 09 lists every credential
examples/                      numbered, runnable offline with no credentials
scripts/                       repository checks; check_layout.py enforces this map
.claude/                       skills and slash commands for Claude Code sessions
.github/                       CI (change-aware) and Dependabot
```

Names say where code came from. The `llmforge-*` folders are one library in two
languages. `aicli` is a separate product that does **not** import llmforge;
it has its own stdlib-only provider layer. The three never share a `src/`
folder or a package name.

## Commands

```bash
make install    # all three packages, with dev tools
make check      # lint + typecheck + every test + examples + layout; all offline
make doctor     # credential source and provider reachability; never prints secrets
make layout     # only the structure contract
```

## Rules that keep this repo coherent

1. **Changes land on `main` through a pull request.** CI runs on PRs and on
   `main`. A branch without a PR gets no CI, so open the PR early as a draft.
2. **One source of truth per concept.**
   - The assurance gates change in `fixtures/assurance-parity.json` first, then in
     both `llmforge-*` packages (see `.claude/skills/llm-integration`).
   - **aicli's upstream is `warnetech/CLI-AI-Command-Center-PRD`.** Change it
     there, then bring it in with
     `git subtree pull --prefix=packages/aicli <remote> <branch>`. If you change
     `packages/aicli/` here, send it back with `git subtree push` so the two
     copies do not drift.
3. **Structure changes update the map.** When you rename or add a top-level
   folder or a package, edit the Map above and the README Layout in the same
   commit. `make layout` will tell you what you missed.
4. **Numbered things stay contiguous**: docs from `00`, labs and examples from `01`.
5. **No secrets in files.** Credentials come from the environment. The
   checklist is `docs/09-credentials.md`. Nothing in `make check` needs one.
6. **Zero required runtime dependencies** in every package. Optional extras
   are imported lazily.

## Glossary

- **EASECM** ("easy, cheap method"): when the user says EASECM, finish the
  task by the simplest and cheapest route that still meets the requirement.
  That means reuse before building, the stdlib before a dependency, one file
  before a framework, and a config change before code. It does **not** mean
  skipping tests, safety checks or review. Cheap means less work, not less
  verification.
- **Gate**: a check that returns a verdict and can fail a run (`docs/07-assurance.md`).
- **Lab**: an unproven technique with a falsifiable claim (`labs/README.md`).
