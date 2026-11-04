# Running model weights from a local folder

An opt-in backend that loads a model straight from a folder on disk with Hugging
Face `transformers`. It needs no internet, no token and no download: the folder is
read with `local_files_only=True`. Weights stay out of git and out of Docker images.

**When to use it.** You have specific weights (for example your own fine-tune) or a
GPU you want to serve from. For everyday local use, Ollama (`docs/OLLAMA.md`) is
lighter: its `mistral:7b` is a quantised build of about 4 GB, while the same model
in 16-bit is roughly 14 to 15 GB of memory.

## Set up

1. Install the extra (Python 3.11 or newer): `pip install 'warnetech-core[hf]'`.
2. Put the model in a folder named for it, once, from a connected machine:

       models/
         mistral-7b/
           config.json
           tokenizer.json            (and the other tokenizer files)
           model.safetensors         (or several shard files plus an index)

   Check the model's licence on its page before use.
3. Point the gateway at the folder that *contains* model folders:

       WARNETECH_HF_MODELS_DIR=./models
       WARNETECH_LOCAL_MODELS=config/local_models.yaml
       WARNETECH_LOCAL_ONLY=1        # optional: refuse every hosted provider

4. Call it by alias. `local.mistral-hf` is already defined and maps to the
   `mistral-7b` folder. Add another model by adding a folder and a `models:` entry
   with `provider: transformers` and `provider_model: <folder name>`.

## In a container

    WARNETECH_API_TOKEN=<choose a token> docker compose --profile hf up --build llm-runtime

The `hf` profile keeps a plain `docker compose up` unchanged. `./models` is mounted
read-only. The service listens on `127.0.0.1:8082` only, and refuses to start
without a token. It needs an NVIDIA GPU and the NVIDIA container toolkit; without
a GPU a 7B model on CPU is very slow. `.dockerignore` keeps `models/` out of the
build, so weights are never copied into the image.

## What is enforced

- **No network and no remote code.** `local_files_only=True` and
  `trust_remote_code=False` are fixed in code. A model folder can ship Python files;
  they are never run.
- **Confined paths.** A model is a plain folder name under one root. Names with
  `/`, `..` or a leading dot are refused, and a symlink pointing outside is refused.
- **Bounded memory.** One model is in memory at a time, loaded on first use, with
  the previous one released first. Generation is one at a time. A prompt longer than
  the model's context window is refused before it reaches the model.
- **Out-of-memory is not retried.** It is reported so the gateway can fall back.
- **Git and Docker.** `models/`, `*.safetensors` and `*.gguf` are git-ignored.

## Not verified

Tested with stand-ins for `transformers`, not with real weights, a GPU, or a built
image. Run it once on your machine with the real model and confirm the reply, and
`docker compose --profile hf up --build` on a machine with a GPU. transformers 5
and torch require Python 3.11 or newer, so the backend is unavailable on 3.10.
