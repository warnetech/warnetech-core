# Task routing and pipelines

Small local models are good at different things. Routing sends each task to the
one suited to it, and pipelines chain them.

## Route a task

    warnetech route explain "Summarise this meeting"   # which model, and why
    warnetech route ask "Extract the dates as JSON"    # send it there
    warnetech route ask "hello" --kind chat            # skip the guess

`config/routing.yaml` lists models per kind of task, in order of preference
(`summarise`, `extract`, `code`, `chat`). The router takes the first whose provider
is registered in this process. With `WARNETECH_LOCAL_ONLY=1`, hosted models are
skipped automatically. If none is available it says which it tried.

The kind is guessed from the first 2000 characters with a short list of rules, not
a model call. It will misjudge unusual phrasing; use `--kind` when it matters.

## Chain models

    warnetech route pipeline config/pipelines/summarise-then-extract.yaml "<text>"

A step names a model alias, or `route:<kind>` to let the router choose, and a
prompt using `{input}` (the original text) and `{previous}` (the last output). Only
those two tokens are replaced; other braces are left alone and nothing in a
template is evaluated. At most 8 steps; input up to 20,000 characters; 8,000
characters of each output are passed on.

A step's output goes into the next prompt as data. Say so in the template, and do
not treat a pipeline over untrusted text as safe from prompt injection.

## Several machines

    OLLAMA_NODES=gpu=http://10.0.0.5:11434,laptop=http://10.0.0.9:11434
    warnetech ollama nodes     # which are up, and which models each holds

A custom model can set `provider: ollama-gpu` to live on a named machine.

## Not covered

Routing checks that a provider is registered, not that the model is installed on
it or that the host has memory to spare. Use `warnetech ollama nodes` and
`warnetech ollama plan` for that.
