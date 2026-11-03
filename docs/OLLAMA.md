# Running local models with Ollama

warnetech-core can run any model that Ollama serves, through the same gateway
as hosted models, so retries, budgets and usage tracking apply to local calls.

## Setup

1. Install Ollama and pull a model: `ollama pull llama3.1:8b`.
2. Set `OLLAMA_HOST=http://localhost:11434` (or a LAN host) in `.env`.
3. Add the model to `models.yaml` (see `models.yaml.example`) and point an alias at it.

## Commands

| Command | What it does |
|---|---|
| `warnetech ollama status` | Host CPU, RAM and GPU memory, and whether Ollama answers |
| `warnetech ollama models` | Installed models, their size, and whether each fits this host |
| `warnetech ollama loaded` | Models currently in memory |
| `warnetech ollama pull <model>` | Download a model |
| `warnetech ollama recommend` | The largest installed model that fits this host |

## How the fit check works

Budget is the GPU's free VRAM if a GPU is found, otherwise available RAM, minus
2 GB reserved. A model fits when its on-disk size times 1.2 (overhead for the
KV cache and runtime) is within that budget. The 1.2 factor is a rule of thumb,
not a measurement. If memory cannot be read, the check reports "no" rather than
guessing.

## Design notes

- The `ollama` CLI is only used for management. Inference always goes through
  the gateway and the OpenAI-compatible endpoint at `/v1`.
- Commands are run as fixed argument lists with no shell. Model names are
  validated before they reach the process.
- Each `warnetech` command takes at most four parameters. The limit is enforced
  when a command is registered (`warnetech_core/cli_core.py`).
