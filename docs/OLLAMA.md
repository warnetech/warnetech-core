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

## Local-only operation

Models run here with no internet and no tokens once they are installed.

1. **Install once, while online:** `warnetech ollama install general`,
   `... install instruction`, `... install summarise`. Each pulls one model.
   Roles map to tags in `config/local_models.yaml`. A raw tag also works.
2. **Route to them:** set `WARNETECH_LOCAL_MODELS=config/local_models.yaml`. The
   `chat.local`, `extract.local` and `summarise.local` aliases then resolve to the
   local models.
3. **Go offline:** set `WARNETECH_LOCAL_ONLY=1`. No hosted provider is registered,
   so no request can leave the machine or spend tokens. A fallback to a hosted
   model fails cleanly rather than calling out.

`warnetech ollama plan` shows each role, its approximate size, and whether it fits
this host. Local CPU runs need about the same memory as the model file, so a
2 GB model needs roughly 3 GB free.

## Several machines

Name each Ollama host in `OLLAMA_NODES`:

    OLLAMA_NODES=gpu=http://10.0.0.5:11434,laptop=http://10.0.0.9:11434

Each becomes a provider named `ollama-<node>`. Point a model at a node with
`provider: ollama-gpu` in `models.yaml`. Usage is recorded per node, so spend and
latency can be compared across machines. Every machine must be on a network the
gateway can reach. Nothing is authenticated: keep Ollama on a private network,
or put a reverse proxy with authentication in front of it.

## Containers

`docker-compose.yml` runs an `ollama` service. Its models live in the
`ollama-models` volume, so restarting or rebuilding the container does not
download anything again. Run `docker compose exec ollama ollama pull <tag>` once,
or use `warnetech ollama install` pointed at that host. The image tag is `latest`;
pin it to a specific release before relying on it.

## Model choice

| Role | Model | Why it is used for this |
|---|---|---|
| general | llama3.2:3b | Small and quick; runs acceptably on CPU |
| instruction | mistral:7b | Follows instructions and produces structured output reliably |
| summarise | gemma2:2b | Smallest footprint for condensing text |

These are starting points. Check each tag with `ollama show <tag>` on the host,
since tags and their sizes change over time. The catalogue can hold more models;
a fourth role is not configured yet.
