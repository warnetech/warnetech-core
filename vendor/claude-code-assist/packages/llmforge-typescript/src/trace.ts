/**
 * Tracing and cost accounting.
 *
 * Costs are estimates from a cached price snapshot (`PRICES_AS_OF`). Treat the
 * ledger as a smoke alarm, not an invoice.
 */

import { addUsage, cacheHitRate, EMPTY_USAGE, totalInput, type Usage } from "./types.js";

export const PRICES_AS_OF = "2026-06-24";

/** USD per 1M tokens: [input, output]. */
export const PRICES: Record<string, [number, number]> = {
  "claude-fable-5": [10.0, 50.0],
  "claude-mythos-5": [10.0, 50.0],
  "claude-opus-5": [5.0, 25.0],
  "claude-opus-4-8": [5.0, 25.0],
  "claude-opus-4-7": [5.0, 25.0],
  "claude-opus-4-6": [5.0, 25.0],
  "claude-sonnet-5": [3.0, 15.0],
  "claude-sonnet-4-6": [3.0, 15.0],
  "claude-haiku-4-5": [1.0, 5.0],
};

export const CACHE_WRITE_MULTIPLIER = 1.25;
export const CACHE_READ_MULTIPLIER = 0.1;

/**
 * Input/output price per 1M tokens, longest-prefix matched.
 *
 * Unknown models price at Opus rates rather than zero: a silent 0.00 in a cost
 * report is worse than a conservative over-estimate.
 */
export function priceOf(model: string): [number, number] {
  const known = Object.keys(PRICES).sort((a, b) => b.length - a.length);
  for (const key of known) {
    if (model.startsWith(key)) return PRICES[key] as [number, number];
  }
  return PRICES["claude-opus-5"] as [number, number];
}

/** Estimated dollar cost of one call, cache tiers included. */
export function costUsd(model: string, usage: Usage): number {
  const [input, output] = priceOf(model);
  const perTokenIn = input / 1_000_000;
  return (
    usage.inputTokens * perTokenIn +
    usage.cacheCreationInputTokens * perTokenIn * CACHE_WRITE_MULTIPLIER +
    usage.cacheReadInputTokens * perTokenIn * CACHE_READ_MULTIPLIER +
    usage.outputTokens * (output / 1_000_000)
  );
}

export interface Span {
  id: string;
  name: string;
  kind: string;
  parentId?: string;
  startedAt: number;
  endedAt?: number;
  durationMs: number;
  attrs: Record<string, unknown>;
  error?: string;
}

export interface TracerOptions {
  enabled?: boolean;
  onSpan?: (span: Span) => void;
}

/** Collects spans and tallies cost. */
export class Tracer {
  readonly spans: Span[] = [];
  readonly usageByModel = new Map<string, Usage>();
  readonly enabled: boolean;
  readonly #onSpan: ((span: Span) => void) | undefined;
  readonly #stack: Span[] = [];
  #counter = 0;

  constructor(options: TracerOptions = {}) {
    this.enabled = options.enabled ?? true;
    this.#onSpan = options.onSpan;
  }

  /** Open a span around `fn`. Nesting is implied by call structure. */
  async span<T>(
    name: string,
    fn: (span: Span) => Promise<T> | T,
    options: { kind?: string; attrs?: Record<string, unknown> } = {},
  ): Promise<T> {
    this.#counter += 1;
    const span: Span = {
      id: `s${this.#counter}`,
      name,
      kind: options.kind ?? "call",
      startedAt: Date.now(),
      durationMs: 0,
      attrs: { ...options.attrs },
      ...(this.#stack.length ? { parentId: this.#stack[this.#stack.length - 1]!.id } : {}),
    };
    if (!this.enabled) return fn(span);

    this.#stack.push(span);
    try {
      return await fn(span);
    } catch (error) {
      span.error = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
      throw error;
    } finally {
      span.endedAt = Date.now();
      span.durationMs = span.endedAt - span.startedAt;
      this.#stack.pop();
      this.spans.push(span);
      this.#onSpan?.(span);
    }
  }

  /** Fold one call's usage into the ledger and onto the current span. */
  recordCall(model: string, usage: Usage): void {
    this.usageByModel.set(model, addUsage(this.usageByModel.get(model) ?? EMPTY_USAGE, usage));
    const current = this.#stack[this.#stack.length - 1];
    if (current) {
      current.attrs["model"] = model;
      current.attrs["inputTokens"] = totalInput(usage);
      current.attrs["outputTokens"] = usage.outputTokens;
      current.attrs["cacheHitRate"] = Number(cacheHitRate(usage).toFixed(3));
      current.attrs["costUsd"] = Number(costUsd(model, usage).toFixed(6));
    }
  }

  get totalCostUsd(): number {
    let total = 0;
    for (const [model, usage] of this.usageByModel) total += costUsd(model, usage);
    return total;
  }

  get totalUsage(): Usage {
    let total = { ...EMPTY_USAGE };
    for (const usage of this.usageByModel.values()) total = addUsage(total, usage);
    return total;
  }

  summary(): Record<string, unknown> {
    const total = this.totalUsage;
    return {
      spans: this.spans.length,
      calls: this.spans.filter((s) => s.kind === "call").length,
      errors: this.spans.filter((s) => s.error).length,
      inputTokens: totalInput(total),
      outputTokens: total.outputTokens,
      cacheHitRate: Number(cacheHitRate(total).toFixed(3)),
      costUsd: Number(this.totalCostUsd.toFixed(4)),
      pricesAsOf: PRICES_AS_OF,
    };
  }
}

export const NULL_TRACER = new Tracer({ enabled: false });
