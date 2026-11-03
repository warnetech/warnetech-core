"""Command line entry point.

``aicli "prompt"`` is shorthand for ``aicli run "prompt"``. The answer goes to
stdout; estimates, routing decisions, cloud notices and errors go to stderr,
so ``aicli "..." --format json | jq`` always sees clean output.

Exit codes: 0 ok, 1 a provider or batch item failed, 2 usage or config error,
3 refused by a budget.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__, config, pricing, templates
from .audit import AuditLog
from .mcp import MCPClient, MCPError, MCPHub
from .providers import Completion, ProviderError
from .router import STRATEGIES, AllProvidersFailed, Router, Target, ToolLoopError
from .storage import Run, Store, since_iso

COMMANDS = ("run", "chat", "compare", "batch", "rerun", "export", "list", "history",
            "search", "stats", "sessions", "audit", "budget", "templates", "mcp",
            "project", "config")
QUIT_WORDS = (":quit", ":q", ":exit")
RUN_FLAGS = ("-m", "--model", "-f", "--format", "-s", "--system", "-q", "--quiet",
             "-t", "--template", "-F", "--file", "--route", "--mcp")


class BudgetExceeded(Exception):
    pass


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


# --- shared plumbing ---------------------------------------------------------

@dataclass
class Ctx:
    args: argparse.Namespace
    cfg: config.Config
    router: Router
    store: Store
    audit: AuditLog

    @property
    def quiet(self) -> bool:
        return bool(getattr(self.args, "quiet", False))

    def say(self, msg: str) -> None:
        if not self.quiet:
            _err(msg)

    @property
    def project(self) -> str | None:
        return str(self.cfg.project_root) if self.cfg.project_root else None


def make_ctx(args: argparse.Namespace) -> Ctx:
    cfg = config.load(warn=_err)
    if getattr(args, "local_only", False):
        cfg.local_only = True
    if getattr(args, "allow_cloud", False):
        cfg.local_only = False
    audit = AuditLog(config.home() / "audit.jsonl")
    quiet = bool(getattr(args, "quiet", False))
    router = Router(cfg, audit, notify=(lambda _m: None) if quiet else _err)
    return Ctx(args, cfg, router, Store(config.home() / "history.db"), audit)


def _parse_vars(pairs: list[str] | None) -> dict[str, str]:
    out = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep:
            raise config.ConfigError(f"--var expects key=value, got {pair!r}")
        out[key] = value
    return out


def build_prompt(ctx: Ctx, text: str, allow_stdin: bool = True) -> tuple[str, dict[str, Any]]:
    """Positional text / stdin, through a template if given, with -F context in front."""
    args, root = ctx.args, ctx.cfg.project_root
    tpl = templates.load(args.template, root) if getattr(args, "template", None) else None
    wants_input = tpl is None or "{{input}}" in tpl.body.replace(" ", "")
    if allow_stdin and (text == "-" or (not text and wants_input and not sys.stdin.isatty())):
        text = sys.stdin.read()
    defaults: dict[str, Any] = {}
    if tpl:
        variables = _parse_vars(getattr(args, "var", None))
        if text:
            variables["input"] = text
        text = templates.render(tpl, variables, root)
        defaults = dict(tpl.defaults)
        defaults["template"] = tpl.name
    blocks = [templates.context_block(spec, root) for spec in getattr(args, "file", None) or []]
    prompt = "\n\n".join([*blocks, text]) if blocks else text
    if not prompt.strip():
        raise config.ConfigError("no prompt given (pass text, pipe stdin, or use -t TEMPLATE)")
    return prompt, defaults


def run_params(ctx: Ctx, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    """Everything needed to replay a run. CLI flags beat template front matter."""
    a, d = ctx.args, defaults or {}
    params = {
        "model": getattr(a, "model", None) or d.get("model"),
        "system": getattr(a, "system", None) or d.get("system"),
        "max_tokens": getattr(a, "max_tokens", None) or d.get("max_tokens")
        or ctx.cfg.max_tokens,
        "temperature": getattr(a, "temperature", None)
        if getattr(a, "temperature", None) is not None else d.get("temperature"),
        "seed": getattr(a, "seed", None),
        "route": getattr(a, "route", None),
        "template": d.get("template"),
        "vars": _parse_vars(getattr(a, "var", None)) or None,
    }
    return {k: v for k, v in params.items() if v is not None}


def choose_chain(ctx: Ctx, model: str | None, prompt_tokens: int, max_tokens: int,
                 route: str | None = None) -> list[Target]:
    if route:
        ranked = ctx.router.rank(
            route, prompt_tokens, max_tokens,
            min_quality=getattr(ctx.args, "min_quality", None),
            max_cost=getattr(ctx.args, "max_cost", None),
            profile=lambda t: ctx.store.model_profile(t.provider, t.model))
        if not ranked:
            raise AllProvidersFailed([])
        ctx.say(f"route {route}: " + ", ".join(
            f"{i}) {r.target} ~{pricing.fmt_usd(r.est_cost)}"
            + (f" {r.latency_ms}ms" if r.latency_ms is not None else "")
            + (f" q{r.quality}" if r.quality is not None else "")
            for i, r in enumerate(ranked, 1)))
        return [r.target for r in ranked]
    chain = ctx.router.candidates(model)
    if not chain:
        raise AllProvidersFailed([])
    return chain


def check_budget(ctx: Ctx, chain: list[Target], prompt_tokens: int, max_tokens: int,
                 calls: int = 1) -> None:
    """Refuse up front if the worst case could cross a daily or monthly ceiling."""
    limits = [(ctx.cfg.daily_usd, "today", "daily"), (ctx.cfg.monthly_usd, "month", "monthly")]
    if all(limit is None for limit, _, _ in limits):
        return
    ceilings = [pricing.cost(ctx.router.price(t), prompt_tokens, max_tokens) for t in chain]
    known = [c for c in ceilings if c is not None]
    if len(known) < len(ceilings):
        ctx.say("! some candidates have no price on file; the budget check cannot cover them")
    worst = max(known, default=0.0) * calls
    for limit, since, label in limits:
        if limit is None:
            continue
        spent = ctx.store.spent_since(since_iso(since))
        if spent + worst > limit and not getattr(ctx.args, "over_budget", False):
            raise BudgetExceeded(
                f"{label} budget ${limit:.2f}: spent ${spent:.4f}, this could add up to "
                f"${worst:.4f}. Use --over-budget to proceed anyway.")


def _cost(ctx: Ctx, target: Target, c: Completion) -> float | None:
    return pricing.cost(ctx.router.price(target), c.input_tokens, c.output_tokens)


def ask(ctx: Ctx, messages: list[dict[str, Any]], params: dict[str, Any],
        session: str | None = None, mcp_servers: list[str] | None = None,
        ) -> tuple[Completion, Target, float | None, Target | None]:
    """One routed call: estimate, budget, call (with tools if asked), record."""
    prompt = messages[-1]["content"]
    max_tokens = params["max_tokens"]
    prompt_tokens = pricing.estimate_tokens(" ".join(str(m["content"]) for m in messages))
    chain = choose_chain(ctx, params.get("model"), prompt_tokens, max_tokens, params.get("route"))
    check_budget(ctx, chain, prompt_tokens, max_tokens)
    first = chain[0]
    ceiling = pricing.cost(ctx.router.price(first), prompt_tokens, max_tokens)
    ctx.say(f"→ {first}  ~{prompt_tokens} input tokens, est. ≤ {pricing.fmt_usd(ceiling)}")
    save = not getattr(ctx.args, "no_save", False)

    def on_fallback(target: Target, why: str) -> None:
        ctx.say(f"! {target} failed: {why}")
        if save:
            ctx.store.add(Run(provider=target.provider, model=target.model, prompt=prompt,
                              response=None, status="error", error=why, session=session,
                              project=ctx.project, params=params))

    call_kw = {"max_tokens": max_tokens, "system": params.get("system"),
               "params": params, "on_fallback": on_fallback}
    if mcp_servers:
        missing = [n for n in mcp_servers if n not in ctx.cfg.mcp]
        if missing:
            raise config.ConfigError(f"no [mcp.servers.{missing[0]}] in config")

        def on_tool(server: str, tool: str, is_error: bool) -> None:
            ctx.audit.write("tool_call", server=server, tool=tool, local=True,
                            status="error" if is_error else "ok")
            ctx.say(f"  ⚙ {server}.{tool}{' (error)' if is_error else ''}")

        with MCPHub([ctx.cfg.mcp[n] for n in mcp_servers], on_call=on_tool) as hub:
            if not hub.specs:
                ctx.say("! no tools allowed on those MCP servers (check `allow` in the config)")
            ctx.say(f"  tools: {', '.join(s.name for s in hub.specs) or 'none'}")
            result = ctx.router.complete(messages, chain, tools=hub.specs, run_tool=hub.run,
                                         max_steps=getattr(ctx.args, "max_steps", 8), **call_kw)
    else:
        result = ctx.router.complete(messages, chain, **call_kw)

    c, target, fb = result.completion, result.target, result.fell_back_from
    cost = _cost(ctx, target, c)
    if fb:
        ctx.say(f"↪ fell back from {fb} to {target}")
    if save:
        ctx.store.add(Run(provider=target.provider, model=target.model, prompt=prompt,
                          response=c.text, input_tokens=c.input_tokens,
                          output_tokens=c.output_tokens, cost_usd=cost, latency_ms=c.latency_ms,
                          fallback_from=str(fb) if fb else None, session=session,
                          project=ctx.project, params=params))
    steps = f", {c.steps} steps" if c.steps > 1 else ""
    ctx.say(f"✓ {target}  {c.input_tokens}→{c.output_tokens} tokens, "
            f"{pricing.fmt_usd(cost)}, {c.latency_ms} ms{steps}")
    return c, target, cost, fb


def result_dict(c: Completion, target: Target, cost: float | None,
                fb: Target | None) -> dict[str, Any]:
    return {"text": c.text, "provider": target.provider, "model": c.model,
            "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
            "cost_usd": cost, "latency_ms": c.latency_ms, "stop_reason": c.stop_reason,
            "steps": c.steps, "fallback_from": str(fb) if fb else None}


def render(fmt: str, c: Completion, target: Target, cost: float | None,
           fb: Target | None) -> str:
    if fmt == "json":
        return json.dumps(result_dict(c, target, cost, fb), indent=2)
    if fmt == "markdown":
        meta = (f"*{target} · {c.input_tokens}→{c.output_tokens} tokens · "
                f"{pricing.fmt_usd(cost)} · {c.latency_ms} ms*")
        return f"{c.text}\n\n---\n{meta}"
    return c.text


# --- commands ----------------------------------------------------------------

def cmd_run(ctx: Ctx) -> int:
    prompt, defaults = build_prompt(ctx, " ".join(ctx.args.prompt))
    params = run_params(ctx, defaults)
    c, target, cost, fb = ask(ctx, [{"role": "user", "content": prompt}], params,
                              mcp_servers=ctx.args.mcp)
    print(render(ctx.args.format or ctx.cfg.format, c, target, cost, fb))
    return 0


def cmd_chat(ctx: Ctx) -> int:
    a = ctx.args
    session = a.resume or (ctx.store.latest_session() if a.continue_ else None)
    messages = ctx.store.conversation(session) if session else []
    if (a.resume or a.continue_) and not messages:
        raise config.ConfigError(f"no saved turns for session {session!r}")
    session = session or uuid.uuid4().hex[:8]
    params = run_params(ctx)
    total: float | None = 0.0
    label = ctx.router.resolve(params.get("model"))
    resumed = f", resumed with {len(messages) // 2} turns" if messages else ""
    _err(f"chat with {label} — session {session}{resumed}; {' / '.join(QUIT_WORDS)} to leave")
    while True:
        try:
            line = input("you> ")
        except EOFError:
            break
        if line.strip() in QUIT_WORDS:
            break
        if not line.strip():
            continue
        messages.append({"role": "user", "content": line})
        try:
            c, target, cost, _ = ask(ctx, messages, params, session)
        except (AllProvidersFailed, BudgetExceeded) as exc:
            _err(str(exc))
            messages.pop()
            continue
        messages.append({"role": "assistant", "content": c.text})
        total = None if total is None or cost is None else total + cost
        print(f"{target.model}> {c.text}")
    _err(f"session {session} total: {pricing.fmt_usd(total)} "
         f"(resume with: aicli chat --resume {session})")
    return 0


def cmd_compare(ctx: Ctx) -> int:
    a = ctx.args
    if not a.model or len(a.model) < 2:
        raise config.ConfigError("compare needs at least two -m models")
    prompt, defaults = build_prompt(ctx, " ".join(a.prompt))
    targets = [ctx.router.resolve(m) for m in a.model]
    if ctx.cfg.local_only:
        blocked = [t for t in targets if not ctx.router.is_local(t)]
        if blocked:
            raise config.ConfigError(f"local_only is on: {blocked[0]} is a cloud model")
    params = run_params(ctx, defaults)
    max_tokens = params["max_tokens"]
    messages = [{"role": "user", "content": prompt}]
    check_budget(ctx, targets, pricing.estimate_tokens(prompt), max_tokens, calls=len(targets))
    session = "cmp-" + uuid.uuid4().hex[:8]

    def one(target: Target):
        try:
            r = ctx.router.complete(messages, [target], max_tokens=max_tokens,
                                    system=params.get("system"), params=params)
            return target, r.completion, None
        except AllProvidersFailed as exc:
            return target, None, exc.attempts[0][1]

    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        results = list(pool.map(one, targets))

    rows = []
    for target, c, error in results:
        cost = _cost(ctx, target, c) if c else None
        if not a.no_save:
            ctx.store.add(Run(
                provider=target.provider, model=target.model, prompt=prompt,
                response=c.text if c else None, status="ok" if c else "error", error=error,
                input_tokens=c.input_tokens if c else 0,
                output_tokens=c.output_tokens if c else 0, cost_usd=cost,
                latency_ms=c.latency_ms if c else None, session=session,
                project=ctx.project, params={**params, "model": str(target)}))
        rows.append({"target": str(target), "text": c.text if c else None, "error": error,
                     "input_tokens": c.input_tokens if c else 0,
                     "output_tokens": c.output_tokens if c else 0,
                     "cost_usd": cost, "latency_ms": c.latency_ms if c else None})

    if (a.format or "text") == "json":
        print(json.dumps({"session": session, "prompt": prompt, "results": rows}, indent=2))
    else:
        for r in rows:
            meta = (f"error: {r['error']}" if r["error"] else
                    f"{r['input_tokens']}→{r['output_tokens']} tokens · "
                    f"{pricing.fmt_usd(r['cost_usd'])} · {r['latency_ms']} ms")
            print(f"### {r['target']}  ({meta})\n\n{r['text'] or ''}\n")
        ctx.say(f"saved as session {session}")
    return 1 if any(r["error"] for r in rows) else 0


def _batch_items(source: str) -> list[dict[str, Any]]:
    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    items = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            obj = None
        item = obj if isinstance(obj, dict) else {"prompt": line}
        item.setdefault("id", n)
        items.append(item)
    return items


def cmd_batch(ctx: Ctx) -> int:
    a = ctx.args
    items = _batch_items(a.input)
    if not items:
        raise config.ConfigError("batch input is empty")
    tpl = templates.load(a.template, ctx.cfg.project_root) if a.template else None
    base_vars = _parse_vars(a.var)
    params = run_params(ctx, tpl.defaults if tpl else None)
    max_tokens = params["max_tokens"]

    prepared = []
    for item in items:
        if tpl:
            variables = {**base_vars, **{k: str(v) for k, v in item.get("vars", {}).items()}}
            if "prompt" in item:
                variables["input"] = item["prompt"]
            prompt = templates.render(tpl, variables, ctx.cfg.project_root)
        elif "prompt" in item:
            prompt = item["prompt"]
        else:
            raise config.ConfigError(f"batch item {item['id']}: no prompt (and no -t template)")
        prepared.append((item, prompt))

    avg_tokens = sum(pricing.estimate_tokens(p) for _, p in prepared) // len(prepared)
    chains: dict[str | None, list[Target]] = {}
    for item, _ in prepared:
        spec = item.get("model") or params.get("model")
        if spec not in chains:
            chains[spec] = choose_chain(ctx, spec, avg_tokens, max_tokens, params.get("route"))
    for spec, chain in chains.items():
        n = sum(1 for item, _ in prepared if (item.get("model") or params.get("model")) == spec)
        check_budget(ctx, chain, avg_tokens, max_tokens, calls=n)
    session = "batch-" + uuid.uuid4().hex[:8]
    ctx.say(f"batch {session}: {len(prepared)} prompts, {a.jobs} at a time")

    def one(entry):
        item, prompt = entry
        chain = chains[item.get("model") or params.get("model")]
        try:
            r = ctx.router.complete([{"role": "user", "content": prompt}], chain,
                                    max_tokens=max_tokens, system=params.get("system"),
                                    params=params)
            return item, prompt, r, None
        except AllProvidersFailed as exc:
            return item, prompt, None, "; ".join(f"{t}: {w}" for t, w in exc.attempts)

    out = open(a.output, "w", encoding="utf-8") if a.output else sys.stdout  # noqa: SIM115
    ok = failed = 0
    total = 0.0
    try:
        with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
            for item, prompt, r, error in pool.map(one, prepared):
                if r:
                    c, target, fb = r.completion, r.target, r.fell_back_from
                    cost = _cost(ctx, target, c)
                    total += cost or 0
                    ok += 1
                    record = {"id": item["id"], **result_dict(c, target, cost, fb)}
                else:
                    failed += 1
                    record = {"id": item["id"], "error": error}
                if not a.no_save:
                    ctx.store.add(Run(
                        provider=r.target.provider if r else "-",
                        model=r.target.model if r else "-", prompt=prompt,
                        response=r.completion.text if r else None,
                        status="ok" if r else "error", error=error,
                        input_tokens=r.completion.input_tokens if r else 0,
                        output_tokens=r.completion.output_tokens if r else 0,
                        cost_usd=record.get("cost_usd"),
                        latency_ms=r.completion.latency_ms if r else None,
                        session=session, project=ctx.project, params=params))
                out.write(json.dumps(record) + "\n")
                out.flush()
    finally:
        if out is not sys.stdout:
            out.close()
    _err(f"batch {session}: {ok} ok, {failed} failed, total ${total:.4f}")
    return 1 if failed else 0


def _load_ref(ctx: Ctx, ref: str) -> dict[str, Any]:
    path = Path(ref)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    if not ref.isdigit():
        raise config.ConfigError(f"{ref!r} is neither a run id nor an exported run file")
    row = ctx.store.get(int(ref))
    if row is None:
        raise config.ConfigError(f"no run #{ref}")
    return row


def cmd_rerun(ctx: Ctx) -> int:
    row = _load_ref(ctx, ctx.args.ref)
    params = {k: v for k, v in (row.get("params") or {}).items() if k != "route"}
    params["model"] = ctx.args.model or f"{row['provider']}/{row['model']}"
    params.setdefault("max_tokens", ctx.cfg.max_tokens)
    params["rerun_of"] = row.get("id")
    c, target, cost, fb = ask(ctx, [{"role": "user", "content": row["prompt"]}], params)
    print(render(ctx.args.format or ctx.cfg.format, c, target, cost, fb))
    return 0


def cmd_export(ctx: Ctx) -> int:
    row = ctx.store.get(ctx.args.id)
    if row is None:
        raise config.ConfigError(f"no run #{ctx.args.id}")
    doc = json.dumps({"aicli_version": __version__, **row}, indent=2, sort_keys=True)
    if ctx.args.output:
        Path(ctx.args.output).write_text(doc + "\n", encoding="utf-8")
        _err(f"wrote {ctx.args.output} (replay with: aicli rerun {ctx.args.output})")
    else:
        print(doc)
    return 0


def cmd_list(ctx: Ctx) -> int:
    rows: list[dict[str, Any]] = []
    for name, pcfg in ctx.cfg.providers.items():
        provider = ctx.router.provider(name)
        ok, why = provider.available()
        row: dict[str, Any] = {"provider": name, "type": provider.type, "available": ok,
                               "local": pcfg.is_local, "detail": why}
        if ctx.args.what == "models":
            try:
                row["models"] = provider.list_models() if ok else []
            except ProviderError as exc:
                row["models"], row["detail"] = [], str(exc)
        rows.append(row)
    if ctx.args.format == "json":
        print(json.dumps(rows, indent=2))
        return 0
    for row in rows:
        mark = "✓" if row["available"] else "✗"
        where = "local" if row["local"] else "cloud"
        print(f"{mark} {row['provider']:<12} {row['type']:<10} {where:<6} {row['detail']}")
        for model in row.get("models", []):
            print(f"    {row['provider']}/{model}")
    if ctx.args.what == "models" and ctx.cfg.aliases:
        print("\naliases:")
        for alias, target in ctx.cfg.aliases.items():
            print(f"    {alias:<12} → {target}")
    if ctx.cfg.local_only:
        print("\nlocal_only is on: cloud providers will not be called.")
    return 0


def _print_runs(rows: list[dict[str, Any]], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(rows, indent=2))
        return
    for r in rows:
        head = (f"#{r['id']} {r['created_at']} {r['provider']}/{r['model']} "
                f"{r['input_tokens']}→{r['output_tokens']} tok {pricing.fmt_usd(r['cost_usd'])}")
        if r["status"] != "ok":
            head = f"#{r['id']} {r['created_at']} {r['provider']}/{r['model']} " \
                   f"[error: {r['error']}]"
        if fmt == "markdown":
            print(f"## {head}\n\n**Prompt**\n\n{r['prompt']}\n\n**Response**\n\n"
                  f"{r['response'] or '_none_'}\n")
        else:
            snippet = (r["response"] or "").replace("\n", " ")
            print(f"{head}\n  > {r['prompt'][:100]}\n  < {snippet[:100]}")


def cmd_history(ctx: Ctx) -> int:
    project = ctx.project if ctx.args.project else None
    if ctx.args.project and not project:
        raise config.ConfigError("not inside a project (no .aicli.toml found)")
    _print_runs(ctx.store.history(ctx.args.limit, ctx.args.model, project), ctx.args.format)
    return 0


def cmd_search(ctx: Ctx) -> int:
    rows = ctx.store.search(" ".join(ctx.args.query), ctx.args.limit)
    if not rows:
        _err("no matches")
    else:
        _print_runs(rows, ctx.args.format)
    return 0


def cmd_stats(ctx: Ctx) -> int:
    try:
        since = since_iso(ctx.args.since)
    except ValueError as exc:
        raise config.ConfigError(str(exc)) from None
    rows = ctx.store.stats(ctx.args.session, since)
    if ctx.args.format == "json":
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("no calls recorded yet")
        return 0
    print(f"{'model':<36} {'calls':>5} {'err':>4} {'in':>8} {'out':>8} {'avg ms':>7}  cost")
    total = 0.0
    for r in rows:
        total += r["cost_usd"] or 0
        if r["unpriced"]:
            cost = "partly unpriced"
        elif r["cost_usd"] is None:
            cost = "—"  # only failed calls: nothing was billed
        else:
            cost = pricing.fmt_usd(r["cost_usd"])
        print(f"{r['provider'] + '/' + r['model']:<36} {r['calls']:>5} {r['errors']:>4} "
              f"{r['input_tokens'] or 0:>8} {r['output_tokens'] or 0:>8} "
              f"{r['avg_latency_ms'] or 0:>7}  {cost}")
    print(f"\ntotal (priced calls): ${total:.4f}   prices as of {pricing.PRICES_AS_OF}")
    return 0


def cmd_sessions(ctx: Ctx) -> int:
    rows = ctx.store.sessions(ctx.args.limit)
    if ctx.args.format == "json":
        print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        print(f"{r['session']:<16} {r['last']}  {r['turns']:>3} turns  "
              f"{pricing.fmt_usd(r['cost_usd']):<12} {r['model']}  "
              f"{(r['first_prompt'] or '')[:50]!r}")
    if not rows:
        print("no sessions yet")
    return 0


def cmd_audit(ctx: Ctx) -> int:
    rows = ctx.audit.read(ctx.args.limit, cloud_only=ctx.args.cloud)
    if ctx.args.format == "json":
        print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        where = "local" if r.get("local") else "CLOUD"
        if r["event"] == "model_call":
            print(f"{r['ts']} {where:<5} {r['status']:<5} {r['target']:<34} → {r['host']} "
                  f"{r['bytes_out']}B out  sha:{r['prompt_sha256']}")
        else:
            print(f"{r['ts']} {where:<5} {r['status']:<5} tool {r['server']}.{r['tool']}")
    cloud = sum(1 for r in rows if not r.get("local", True))
    print(f"\n{len(rows)} events, {cloud} left this machine. Log: {ctx.audit.path}")
    return 0


def cmd_budget(ctx: Ctx) -> int:
    for limit, since, label in ((ctx.cfg.daily_usd, "today", "daily"),
                                (ctx.cfg.monthly_usd, "month", "monthly")):
        spent = ctx.store.spent_since(since_iso(since))
        cap = f"${limit:.2f}" if limit is not None else "no limit"
        left = f", ${max(0.0, limit - spent):.4f} left" if limit is not None else ""
        print(f"{label:<8} spent ${spent:.4f} of {cap}{left}")
    return 0


def cmd_templates(ctx: Ctx) -> int:
    root = ctx.cfg.project_root
    if ctx.args.action == "list":
        found = templates.list_all(root)
        for name, path in found.items():
            tpl = templates.parse(name, path.read_text(encoding="utf-8"), path)
            needs = ", ".join(tpl.variables()) or "-"
            print(f"{name:<20} vars: {needs:<24} {path}")
        if not found:
            dirs = " or ".join(str(d) for d in templates.search_dirs("templates", root))
            print(f"no templates yet; add .md files to {dirs}")
        return 0
    if not ctx.args.name:
        raise config.ConfigError("templates show needs a NAME")
    tpl = templates.load(ctx.args.name, root)
    print(tpl.path.read_text(encoding="utf-8") if tpl.path else tpl.body)
    return 0


def cmd_mcp(ctx: Ctx) -> int:
    a = ctx.args
    if a.action == "list":
        for name, s in ctx.cfg.mcp.items():
            allow = ", ".join(s.allow) or "(nothing allowed)"
            print(f"{name:<14} {s.command} {' '.join(s.args)}\n               allow: {allow}")
        if not ctx.cfg.mcp:
            print("no MCP servers configured; see [mcp.servers.*] in `aicli config show`")
        return 0
    if not a.server or a.server not in ctx.cfg.mcp:
        raise config.ConfigError(f"unknown MCP server {a.server!r}")
    cfg = ctx.cfg.mcp[a.server]
    try:
        with MCPClient(cfg) as client:
            if a.action == "tools":
                for tool in client.list_tools():
                    mark = "allowed" if ("*" in cfg.allow or tool["name"] in cfg.allow) \
                        else "denied "
                    print(f"{mark}  {tool['name']:<28} {tool.get('description', '')[:70]}")
                return 0
            if not a.tool:
                raise config.ConfigError("mcp call needs SERVER TOOL")
            args = json.loads(a.args) if a.args else {}
            text, is_error = client.call_tool(a.tool, args)
            ctx.audit.write("tool_call", server=a.server, tool=a.tool, local=True,
                            status="error" if is_error else "ok")
            print(text)
            return 1 if is_error else 0
    except MCPError as exc:
        _err(str(exc))
        return 1


def cmd_project(ctx: Ctx) -> int:
    a = ctx.args
    if a.action == "init":
        root = Path.cwd()
        made = config.init_project(root)
        for path in made:
            print(f"wrote {path}")
        print(f"project ready at {root} (trusted)")
        return 0
    root = config.find_project()
    if root is None:
        raise config.ConfigError("no .aicli.toml here or in any parent directory")
    if a.action == "trust":
        config.trust(root)
        print(f"trusted {root / config.PROJECT_FILE} (re-run after it changes)")
    else:
        state = "trusted" if config.is_trusted(root) else "NOT trusted (ignored)"
        print(f"{root / config.PROJECT_FILE}: {state}")
    return 0


def cmd_config(ctx: Ctx) -> int:
    action = ctx.args.action
    if action == "init":
        print(f"wrote {config.init(force=ctx.args.force)}")
    elif action == "path":
        print(config.config_path())
    elif action == "show":
        path = config.config_path()
        print(path.read_text() if path.exists() else
              f"# {path} does not exist; these are the built-in defaults\n"
              + config.DEFAULT_CONFIG)
    elif action == "detect":
        ctx.args.what, ctx.args.format = "providers", "text"
        return cmd_list(ctx)
    return 0


# --- parser ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aicli", description="One command, any model.")
    p.add_argument("--version", action="version", version=f"aicli {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def privacy(sp):
        g = sp.add_mutually_exclusive_group()
        g.add_argument("--local-only", action="store_true", help="refuse cloud providers")
        g.add_argument("--allow-cloud", action="store_true",
                       help="lift local_only for this command")

    def model_opts(sp, multi=False):
        if multi:
            sp.add_argument("-m", "--model", action="append", help="repeat for each model")
        else:
            sp.add_argument("-m", "--model", help="provider/model or alias")
        sp.add_argument("--max-tokens", type=int)
        sp.add_argument("-s", "--system", help="system prompt")
        sp.add_argument("--temperature", type=float)
        sp.add_argument("--seed", type=int, help="passed to providers that support it")
        sp.add_argument("-q", "--quiet", action="store_true", help="nothing on stderr but errors")
        sp.add_argument("--no-save", action="store_true", help="do not record in history")
        sp.add_argument("--over-budget", action="store_true", help="ignore budget ceilings")
        privacy(sp)

    def prompt_opts(sp):
        sp.add_argument("-t", "--template", help="template name (see `aicli templates list`)")
        sp.add_argument("--var", action="append", metavar="K=V", help="template variable")
        sp.add_argument("-F", "--file", action="append", metavar="PATH|FRAGMENT",
                        help="add a file or fragment as context (repeatable)")

    def route_opts(sp):
        sp.add_argument("--route", choices=STRATEGIES, help="pick from [routing] pool")
        sp.add_argument("--min-quality", type=int)
        sp.add_argument("--max-cost", type=float, help="USD worst-case ceiling per call")

    run = sub.add_parser("run", help="send one prompt (the default command)")
    run.add_argument("prompt", nargs="*", help="prompt text; '-' or a pipe reads stdin")
    run.add_argument("-f", "--format", choices=config.VALID_FORMATS)
    run.add_argument("--mcp", action="append", metavar="SERVER", help="offer this MCP server's tools")
    run.add_argument("--max-steps", type=int, default=8, help="tool-loop ceiling")
    model_opts(run)
    prompt_opts(run)
    route_opts(run)
    run.set_defaults(fn=cmd_run)

    chat = sub.add_parser("chat", help="multi-turn conversation, saved locally")
    model_opts(chat)
    route_opts(chat)
    chat.add_argument("--resume", metavar="SESSION")
    chat.add_argument("--continue", dest="continue_", action="store_true",
                      help="resume the most recent chat")
    chat.set_defaults(fn=cmd_chat)

    cmp_ = sub.add_parser("compare", help="same prompt, several models, side by side")
    cmp_.add_argument("prompt", nargs="*")
    cmp_.add_argument("-f", "--format", choices=("text", "json"))
    model_opts(cmp_, multi=True)
    prompt_opts(cmp_)
    cmp_.set_defaults(fn=cmd_compare)

    batch = sub.add_parser("batch", help="run a JSONL / text file of prompts")
    batch.add_argument("input", help="file, or '-' for stdin")
    batch.add_argument("-o", "--output", help="JSONL results (default stdout)")
    batch.add_argument("-j", "--jobs", type=int, default=4)
    batch.add_argument("-t", "--template")
    batch.add_argument("--var", action="append", metavar="K=V")
    model_opts(batch)
    route_opts(batch)
    batch.set_defaults(fn=cmd_batch)

    rerun = sub.add_parser("rerun", help="replay a past run (id or exported file)")
    rerun.add_argument("ref")
    rerun.add_argument("-f", "--format", choices=config.VALID_FORMATS)
    model_opts(rerun)
    rerun.set_defaults(fn=cmd_rerun)

    export = sub.add_parser("export", help="write one run as JSON, for version control")
    export.add_argument("id", type=int)
    export.add_argument("-o", "--output")
    export.set_defaults(fn=cmd_export)

    ls = sub.add_parser("list", help="providers and models")
    ls.add_argument("what", choices=("providers", "models"))
    ls.add_argument("-f", "--format", choices=("text", "json"), default="text")
    ls.set_defaults(fn=cmd_list)

    hist = sub.add_parser("history", help="recent prompts and responses")
    hist.add_argument("-m", "--model")
    hist.add_argument("--project", action="store_true", help="only this project's runs")
    srch = sub.add_parser("search", help="full-text search over history")
    srch.add_argument("query", nargs="+")
    for sp, fn in ((hist, cmd_history), (srch, cmd_search)):
        sp.add_argument("-n", "--limit", type=int, default=20)
        sp.add_argument("-f", "--format", choices=config.VALID_FORMATS, default="text")
        sp.set_defaults(fn=fn)

    st = sub.add_parser("stats", help="calls, tokens, latency and cost per model")
    st.add_argument("--session")
    st.add_argument("--since", help="today | month | 24h | 7d | 30d")
    st.add_argument("-f", "--format", choices=("text", "json"), default="text")
    st.set_defaults(fn=cmd_stats)

    ses = sub.add_parser("sessions", help="saved chat sessions")
    ses.add_argument("-n", "--limit", type=int, default=20)
    ses.add_argument("-f", "--format", choices=("text", "json"), default="text")
    ses.set_defaults(fn=cmd_sessions)

    aud = sub.add_parser("audit", help="what left this machine, and where it went")
    aud.add_argument("--cloud", action="store_true", help="only calls that left the machine")
    aud.add_argument("-n", "--limit", type=int, default=50)
    aud.add_argument("-f", "--format", choices=("text", "json"), default="text")
    aud.set_defaults(fn=cmd_audit)

    sub.add_parser("budget", help="spend against daily / monthly ceilings").set_defaults(
        fn=cmd_budget)

    tp = sub.add_parser("templates", help="list | show NAME")
    tp.add_argument("action", choices=("list", "show"))
    tp.add_argument("name", nargs="?")
    tp.set_defaults(fn=cmd_templates)

    mc = sub.add_parser("mcp", help="list | tools SERVER | call SERVER TOOL --args JSON")
    mc.add_argument("action", choices=("list", "tools", "call"))
    mc.add_argument("server", nargs="?")
    mc.add_argument("tool", nargs="?")
    mc.add_argument("--args", help="JSON object of tool arguments")
    mc.set_defaults(fn=cmd_mcp)

    pj = sub.add_parser("project", help="init | trust | show (.aicli.toml in this repo)")
    pj.add_argument("action", choices=("init", "trust", "show"))
    pj.set_defaults(fn=cmd_project)

    cf = sub.add_parser("config", help="init | show | path | detect")
    cf.add_argument("action", choices=("init", "show", "path", "detect"))
    cf.add_argument("--force", action="store_true")
    cf.set_defaults(fn=cmd_config)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `aicli "prompt"` and `aicli -m x "prompt"` both mean `aicli run ...`.
    is_prompt = bool(argv) and argv[0] not in COMMANDS and not argv[0].startswith("-")
    if is_prompt or (argv and argv[0] in RUN_FLAGS):
        argv.insert(0, "run")
    args = build_parser().parse_args(argv)
    ctx = None
    try:
        ctx = make_ctx(args)
        return args.fn(ctx)
    except config.ConfigError as exc:
        _err(f"config error: {exc}")
        return 2
    except BudgetExceeded as exc:
        _err(f"budget: {exc}")
        return 3
    except (AllProvidersFailed, ToolLoopError, MCPError) as exc:
        _err(str(exc))
        return 1
    except BrokenPipeError:
        # `aicli ... | head` closed the pipe early: that is the reader's choice, not an error.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    finally:
        if ctx is not None:
            ctx.store.close()
