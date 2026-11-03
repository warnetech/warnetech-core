# Pattern catalog

Reach for the simplest tier that solves the problem. Most "we need an agent"
requests are one call with a better prompt.

## The ladder

| Tier | Use when | Cost of getting it wrong |
|---|---|---|
| **One call** | classify, extract, summarize, rewrite, answer | a bad answer |
| **One call + schema** | the output feeds code | a parse error you catch |
| **Workflow** | you know the sequence; the model fills in steps | a bad step you can retry |
| **Agent** | genuinely open-ended and model-driven | an unbounded loop touching real things |

Justify the agent tier against four questions. All four must be yes:

- **Complexity** — is the task hard to fully specify up front? ("turn this
  design doc into a PR", not "extract the title from this PDF")
- **Value** — does the outcome justify the latency and cost?
- **Viability** — is the model actually capable at this task type?
- **Cost of error** — can mistakes be caught and rolled back?

Any "no" means drop a rung.

## Single call

```python
from llmforge import Request, default_provider

provider = default_provider()
response = provider.complete(Request(
    messages=[{"role": "user", "content": f"Classify the sentiment:\n\n{text}"}],
    model="claude-opus-5",
    max_tokens=256,      # a classification does not need 16k
    effort="low",        # nor deep thinking
))
```

Two things people get wrong here: leaving `max_tokens` at a large default (it
costs nothing directly but invites verbose output), and reaching for `effort:
high` reflexively. `low` is right for genuinely simple work and materially
cheaper.

## Structured output

When the result feeds code, constrain it — and still validate, because a schema
constrains shape, not sense.

```python
from llmforge import Request, validate_json

SCHEMA = {
    "type": "object",
    "properties": {
        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        "component": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["severity", "component", "summary"],
    "additionalProperties": False,
}

response = provider.complete(Request(
    messages=[{"role": "user", "content": report}],
    output_schema=SCHEMA,
))
triage = validate_json(response.text, SCHEMA)
```

On failure, one repair round recovers most cases:

```python
from llmforge import repair_prompt, ValidationError

try:
    triage = validate_json(response.text, SCHEMA)
except ValidationError as exc:
    retry = provider.complete(Request(
        messages=[
            {"role": "user", "content": report},
            {"role": "assistant", "content": response.text},
            {"role": "user", "content": repair_prompt(response.text, str(exc), SCHEMA)},
        ],
        output_schema=SCHEMA,
    ))
    triage = validate_json(retry.text, SCHEMA)
```

Two rounds rarely help. If the second fails, the schema or the prompt is the
problem, not the draw.

## Tools

Start with a broad tool (`bash`) for reach. Promote an action to a dedicated
tool when the harness needs to do something with it:

- **gate it** — hard-to-reverse actions want a confirmation hook. `send_email`
  is easy to gate; `bash -c "curl -X POST ..."` is not.
- **check staleness** — a dedicated `edit` tool can reject a write if the file
  changed since the model read it. Bash cannot enforce that.
- **render it** — some actions deserve UI. Claude Code promotes asking a
  question to a tool so it can render a modal and block the loop.
- **parallelize it** — mark read-only tools `parallel_safe`. Through bash the
  harness cannot tell a safe `grep` from an unsafe `git push`, so it must
  serialize everything.

```python
from llmforge import tool, ToolRegistry, GatedPolicy

@tool(parallel_safe=True)
def grep(pattern: str, path: str = ".") -> str:
    """Search the repository for a regex.

    Args:
        pattern: The regular expression to search for.
        path: Directory to search under.
    """
    ...

@tool(destructive=True)
def apply_patch(diff: str) -> str:
    """Apply a unified diff to the working tree.

    Args:
        diff: The patch to apply.
    """
    ...

tools = ToolRegistry(grep, apply_patch, policy=GatedPolicy(approve=confirm_with_user))
```

The schema comes from the signature and the docstring, so it cannot drift from
the implementation — the single most common source of "the model keeps calling
this wrong".

## Agent loop

```python
from llmforge import Agent, Budget, Tracer, TracedProvider

tracer = Tracer(sink=".llmforge/trace.jsonl")
agent = Agent(
    TracedProvider(provider, tracer),
    tools=tools,
    system=SYSTEM_PROMPT,
    budget=Budget(max_steps=15, max_usd=2.00),
    effort="high",
)
run = agent.run("Find why the uploader retries twice on 503 and fix it.")

print(run.text)
print(tracer.summary())
```

`run` carries the whole transcript, every step, the usage, and the cost. If a
budget bound, `run.halted` says which — and the partial transcript is still
there, which matters because the run that hit a wall is the one you want to
inspect.

## Context assembly

```python
from llmforge import build_context

packed = build_context(
    "the uploader retries twice on 503",
    root=".",
    budget_tokens=60_000,
    pinned=["src/upload/client.py"],       # the human knows something
    recent=changed_files_from_git_status(),
)

messages = [{"role": "user", "content": f"{packed.text}\n\n{question}"}]
```

`packed.included` and `packed.elided` tell you what the model saw. When
retrieval starts returning the wrong files, `rank()` attaches a `reasons` list
to every chunk explaining its score — a retrieval step you cannot explain is
one you cannot debug.

## Untrusted content

Anything the model reads that a third party can write is an injection surface:
READMEs, issue bodies, PR comments, CI logs, dependency changelogs, fetched
pages.

```python
from llmforge import wrap_untrusted

prompt = f"Summarize this issue:\n\n{wrap_untrusted(issue_body, source='github#4471')}"
```

The envelope states provenance and authority in the same place every time, and
flags a suspicious scan result inline so the model has what the harness has.
It reduces the attack surface; it does not close it. Pair it with a policy that
gates destructive tools.

## Evals

```python
from llmforge import Suite, Case, all_of, contains, json_valid, under_tokens

suite = Suite("triage", [
    Case("high severity is detected", DATA_LOSS_REPORT,
         all_of(json_valid(SCHEMA), contains('"high"'))),
    Case("stays terse", MINOR_REPORT,
         all_of(json_valid(SCHEMA), under_tokens(200))),
])

result = suite.run(provider, repeats=5)
print(result.report())
result.assert_at_least(0.9)
```

`repeats=5` is the point. A prompt that passes three times in five is not a
passing prompt, and a single-shot suite cannot see the difference. Gate on a
rate rather than perfection: a suite that must be 100% green gets deleted the
first Friday it goes red for a reason nobody has time to fix.
