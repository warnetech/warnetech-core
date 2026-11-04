"""Run a model from weights in a local folder, through Hugging Face transformers.

Opt-in and offline. The weights are read from a folder you put there; nothing is
downloaded and no token is used. Weights stay out of git (``models/`` is ignored).

Safety choices, all fixed in code rather than left to configuration:

- ``local_files_only=True``: transformers may not reach the network.
- ``trust_remote_code=False``: a model folder can ship Python files, and loading
  them runs that code. We never do.
- A model is addressed by a plain directory name under one configured root. The
  resolved path must stay inside the root, symlinks included.

Resource use is bounded: one model is held in memory at a time, loaded on first
use (not at start-up), generation is serialised, and an over-long prompt is
refused before it reaches the model.

transformers and torch are an optional extra (``pip install 'warnetech-core[hf]'``,
Python 3.11+) and are imported only when a model is first loaded. This path has
been tested with stand-ins for transformers, not with real weights.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import ConfigError, ProviderError
from ..registry import ModelSpec
from ..types import CompletionRequest, CompletionResponse, TokenUsage
from .base import Provider

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

Loader = Callable[[Path], tuple[Any, Any]]


def load_transformers_model(directory: Path) -> tuple[Any, Any]:
    """Load ``(tokenizer, model)`` from a local folder, offline."""
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise ConfigError(
            "the local-weights backend needs the optional extra: "
            "pip install 'warnetech-core[hf]' (Python 3.11 or newer)"
        ) from exc

    safe = {"local_files_only": True, "trust_remote_code": False}
    tokenizer = AutoTokenizer.from_pretrained(str(directory), **safe)
    try:
        model = AutoModelForCausalLM.from_pretrained(
            str(directory), device_map="auto", dtype="auto", **safe
        )
    except TypeError:  # transformers releases before `dtype` spell it `torch_dtype`
        model = AutoModelForCausalLM.from_pretrained(
            str(directory), device_map="auto", torch_dtype="auto", **safe
        )
    return tokenizer, model


class TransformersProvider(Provider):
    name = "transformers"

    def __init__(
        self, models_root: str | Path, *, loader: Loader = load_transformers_model
    ) -> None:
        self.models_root = Path(models_root).resolve()
        self._loader = loader
        self._load_lock = threading.Lock()
        self._generate_lock = threading.Lock()
        self._loaded: tuple[str, Any, Any] | None = None  # at most one model in memory

    def model_dir(self, name: str) -> Path:
        """The folder for ``name`` under the root, checked before anything is read."""
        if not _NAME_RE.fullmatch(name):
            raise ConfigError(f"invalid local model name: {name!r}")
        path = (self.models_root / name).resolve()
        if not path.is_relative_to(self.models_root):
            raise ConfigError(f"model folder for {name!r} resolves outside {self.models_root}")
        if not (path / "config.json").is_file():
            raise ConfigError(f"no model at {path} (expected a config.json)")
        return path

    def _get(self, name: str) -> tuple[Any, Any]:
        with self._load_lock:
            if self._loaded is not None and self._loaded[0] == name:
                return self._loaded[1], self._loaded[2]
            self._loaded = None  # release the previous model before loading the next
            tokenizer, model = self._loader(self.model_dir(name))
            self._loaded = (name, tokenizer, model)
            return tokenizer, model

    def _generate(self, request: CompletionRequest, spec: ModelSpec) -> tuple[str, int, int]:
        tokenizer, model = self._get(spec.provider_model)
        messages = [{"role": m.role, "content": m.content} for m in request.messages]
        if getattr(tokenizer, "chat_template", None):
            # Render to text first: the return type of tokenised templates differs
            # between transformers major versions, a plain string does not.
            prompt = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        else:
            prompt = "\n".join(f"{m['role']}: {m['content']}" for m in messages) + "\nassistant:"

        inputs = tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
        inputs = inputs.to(model.device)
        prompt_tokens = int(inputs["input_ids"].shape[-1])
        if prompt_tokens > spec.context_window:
            raise ProviderError(
                f"prompt is {prompt_tokens} tokens; {spec.alias} allows {spec.context_window}",
                provider=self.name,
                retryable=False,
            )

        options: dict[str, Any] = {
            "max_new_tokens": min(request.max_tokens, spec.max_output_tokens),
        }
        if request.temperature > 0:
            options.update(do_sample=True, temperature=request.temperature)
        else:
            options.update(do_sample=False)
        if request.stop:
            options.update(stop_strings=request.stop, tokenizer=tokenizer)

        with self._generate_lock:  # one generation at a time keeps memory use predictable
            output = model.generate(**inputs, **options)
        new_tokens = output[0][prompt_tokens:]
        text = tokenizer.decode(new_tokens, skip_special_tokens=True)
        return text, prompt_tokens, len(new_tokens)

    async def complete(self, request: CompletionRequest, spec: ModelSpec) -> CompletionResponse:
        started = time.perf_counter()
        try:
            text, tokens_in, tokens_out = await asyncio.to_thread(self._generate, request, spec)
        except (ConfigError, ProviderError):
            raise
        except (RuntimeError, MemoryError, OSError) as exc:
            # CUDA/host out-of-memory and unreadable weights will not fix themselves
            # on an immediate retry, so mark them non-retryable and let fallback run.
            raise ProviderError(
                f"local generation failed: {exc}", provider=self.name, retryable=False
            ) from exc
        return CompletionResponse(
            id=f"hf-{int(time.time() * 1000)}",
            text=text,
            model_alias=spec.alias,
            provider=self.name,
            provider_model=spec.provider_model,
            usage=TokenUsage(input_tokens=tokens_in, output_tokens=tokens_out),
            cost_usd=0.0,
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason="stop",
        )

    async def aclose(self) -> None:
        with self._load_lock:
            self._loaded = None  # drop the model so its memory can be reclaimed
