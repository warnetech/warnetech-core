# Handoff

Working on `warnetech-core`, branch `claude/warnetech-core-agi-setup-2x0okx`. PR #9 is open: signed webhooks, local web UI (`/ui`), `warnetech_memory`, `docs/PRD.md`, and the interpreter now refuses to run code on macOS/Windows unless `WARNETECH_ALLOW_UNENFORCED_MEMORY=1` (macOS CI showed the memory limit is not enforced there). 861 tests pass locally. CI for the last push has not been looked at.

**Next action:** get PR #9's check runs (`mcp__github__pull_request_read`, method `get_check_runs`, pullNumber 9), read any failed job log, fix it. macOS now gets a runner, so its result is real.

Uncommitted: none (only Clawness state files the tooling rewrites).

Still not built: remote MCP over HTTPS (`POST /mcp`). The MCP server lives in `mcp-servers/warnetech_mcp/`, outside the installed package; move it into `src/warnetech_core/mcp_server/` with a shim first.

Traps:
- PRs #6 and #8 merged before my later commits. After a merge, `git fetch origin main`, rebase the branch onto it, push with `--force-with-lease`, open a new PR.
- The Codeflash stop hook always fails (expired certificate on `app.codeflash.ai`). Nothing to fix; do not disable TLS checks.
- This session cannot reach the user's phone. Termux repo is `~/warnetech-core-termux`; the full test suite has not been run natively there (the user can run it and paste the tail).

Open questions: none blocking. Unanswered from the user: make the macOS CI job non-blocking with a timeout? PRD decisions D1 to D7 (`docs/PRD.md` section 6). Rotating the leaked Supabase key is the user's job (PRD `SEC-004`).

**Autostart:** yes
