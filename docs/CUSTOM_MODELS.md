# Custom models on local Llama, Mistral and Gemma

A custom model here is a **base model plus a system prompt and sampling settings,
optionally with a LoRA adapter you trained elsewhere**, created in Ollama and
reachable through the gateway as an alias. This repo does not pre-train a model
from scratch, and it does not run training itself.

## The three shipped models

| Name | Alias | Base | For |
|---|---|---|---|
| `warnetech-general` | `custom.general` | llama3.2:3b | Quick Q&A; runs on CPU |
| `warnetech-instruct` | `custom.instruct` | mistral:7b | Exact instruction following, JSON output |
| `warnetech-summarise` | `custom.summarise` | gemma2:2b | Short, faithful summaries |

Specs are YAML files in `config/custom_models/`. Check each base tag with
`ollama show <tag>` on your host; tags and sizes change over time.

## Build and use one

    warnetech custom list                       # specs and their aliases
    warnetech custom show warnetech-general     # the Modelfile it will produce
    warnetech ollama install general            # base model must be installed first
    warnetech custom create warnetech-general   # ollama create ... -f <Modelfile>

Set `WARNETECH_CUSTOM_MODELS_DIR=config/custom_models` and the aliases resolve in
the gateway (`custom.general`, `custom.instruct`, `custom.summarise`). A spec can
set `provider: ollama-<node>` to live on a machine named in `OLLAMA_NODES`.

## Check it still behaves

    warnetech custom eval config/evals/custom.yaml --model custom.general

Each case is a prompt plus deterministic checks (`contains`, `not_contains`,
`regex`, `max_chars`). This catches regressions, such as a model that stops
following its system prompt. It does **not** measure overall quality; a model can
pass every case and still be poor.

## Fine-tune (outside this repo)

1. Collect examples as chat records, one JSON object per line:
   `{"messages": [{"role": "user", ...}, {"role": "assistant", ...}]}`.
2. `warnetech custom dataset my.jsonl --out-dir ./data/training --val-fraction 0.1`
   validates them, drops exact duplicates, and writes `train.jsonl` and
   `validation.jsonl`. Nothing is written if any record has a problem.
3. Train a LoRA adapter on a machine with a GPU, using the tool of your choice.
4. Put the adapter next to the spec and set `adapter: my-lora.gguf` (a relative
   path, inside the spec's directory). Re-run `warnetech custom create`.

The split is by content hash, not shuffled, so a record held out for validation
stays held out when the file grows or is reordered. A very small file can land
entirely on one side; the command says so.

## What is checked, and why

- **Modelfile injection.** A spec is data that becomes text for Ollama. Names, base
  tags, parameters, stop strings and the description are validated so a value
  cannot add a directive. A system prompt cannot contain `"""`. Unknown parameters
  and out-of-range values are rejected, and so is a boolean given as a number.
- **Adapter paths** must be relative and stay inside the spec's directory,
  including through symlinks. The file must exist before Ollama is called.
- **Credentials in training data** are rejected, not scrubbed. A secret that
  reaches training data is baked into the weights and cannot be removed later.
  The check recognises common key formats only; it is not a full secret scanner.
  Review personal data in your examples yourself.

## Not verified

The commands that call Ollama (`create`, `ollama install`) have been tested against
a fake Ollama only, not a real server. Run `warnetech custom create` once on your
own machine and confirm the model appears in `ollama list`.
