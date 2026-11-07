# Interpreter: natural-language computer control with local models

Describe a task. A local model (Ollama or LM Studio) writes code. You approve it.
It runs in a workspace with limits, and the output goes back to the model. No
internet and no tokens once the models are installed.

## Use it

    warnetech interpreter run "count the lines in every .py file" --workspace ./work

| Parameter | Meaning |
|---|---|
| `task` | what you want, in plain language |
| `--model` | registry alias, default `chat.local` |
| `--workspace` | the only directory it may read or write |
| `--mode` | `ask` (approve each action, default) or `dry-run` (show proposals, run nothing) |

`chat.local` exists once `WARNETECH_LOCAL_MODELS=config/local_models.yaml` is set
(see `docs/OLLAMA.md`). For LM Studio, set `LMSTUDIO_HOST` and register a model
with `provider: lmstudio` in `models.yaml`. Add `WARNETECH_LOCAL_ONLY=1` to
guarantee nothing leaves the machine.

## Call it from code, agents, or other tools

    from warnetech_core.interpreter import Interpreter, Executor, Workspace, Policy

    ws = Workspace("./work")
    interpreter = Interpreter(gateway, Executor(ws), approver=my_approver)
    report = await interpreter.run("summarise notes.txt")

- **Agents:** `register_interpreter_tools(registry, workspace, ...)` adds
  `workspace_read`, `workspace_write` and `workspace_list` to a `ToolRegistry`.
  `run_code` is added only if you pass both an executor and an approver.
- **MCP:** the `warnetech_interpret` tool returns the code the model would run.
  It never executes it, because an MCP client has no way to ask a person.

## What protects you, and what does not

Enforced:
- Every action goes to an approver first. The default approver denies, so a
  missing or non-interactive approver runs nothing.
- Files are confined to the workspace. Paths are resolved, so `..`, absolute paths
  and symlinks that point outside are rejected.
- The child process gets a scrubbed environment: no API keys or tokens from the
  parent. It has no stdin, a timeout that kills the whole process group, output
  caps, and memory, CPU and file-size limits. **The memory limit is enforced on
  Linux only.** On macOS it did not stop a 1 GiB allocation under a 256 MB limit
  (GitHub's macOS runner), and Windows has no such limit. Elsewhere the executor
  **refuses to run code** unless you set `WARNETECH_ALLOW_UNENFORCED_MEMORY=1` in the
  environment (only a clear yes counts: `1`, `true`, `yes`, `on`). The model and MCP
  clients cannot set it. With it set, the CLI still prints a warning, and approval is
  the only protection against a program that uses a lot of memory.
- Only the first code block in a reply counts, and only languages on the
  allowlist (`python` by default; `bash` must be enabled explicitly).
- Every proposal, denial and execution is written to an audit log. Output and
  environment are never logged.
- `auto` mode (no approval) cannot be chosen from the CLI or MCP.

Not enforced:
- **It is not a sandbox.** Approved code can still read any file you can read, use
  the network, and start processes. Approval and the audit log are the real
  controls. For untrusted tasks, run the whole thing in a container with
  `--network none` and a read-only root filesystem.
- Program output goes back to the model, and a program can print text that reads
  like an instruction. It is labelled as data, and every later action still needs
  approval, but a careless approver can still be talked into things.
- Resource limits use POSIX `setrlimit`. On Windows only the timeout, output cap
  and environment scrubbing apply.
