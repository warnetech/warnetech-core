# Examples

| Folder | What it shows | Try it |
|---|---|---|
| `templates/`, `fragments/` | Reusable prompts with `{{vars}}`, `{{input}}`, `{{> fragment}}` and front-matter defaults | copy into `.aicli/` (project) or `~/.aicli/` (user), then `git diff \| aicli -t review --var language=python` |
| `batch/` | JSONL batches: plain prompts, per-item models, template variables | `aicli batch examples/batch/prompts.jsonl -m echo/echo` · `aicli batch examples/batch/topics.jsonl -t summarize` |
| `mcp/` | Two MCP servers with deny-by-default allow lists | `aicli mcp tools files` |
| `github-actions/` | PR review and a nightly batch using this repo's `action.yml` | copy into `.github/workflows/` |
| `ci-config.toml` | Cheapest-first routing with a daily budget, for CI | `config:` input of the action |
