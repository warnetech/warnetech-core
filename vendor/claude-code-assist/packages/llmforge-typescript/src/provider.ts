/**
 * The provider boundary.
 *
 * `AnthropicProvider` is the real thing; `FakeProvider` is scripted, offline
 * and free. Everything above this line only knows `Provider`.
 */

import {
  EMPTY_USAGE,
  ProviderError,
  type Request,
  type Response,
  type StopReason,
  type ToolCall,
  type Usage,
  response as makeResponse,
} from "./types.js";

/**
 * Models that reject `budget_tokens` and accept `output_config.effort`.
 * Sending a thinking budget to one of these is a 400, not a soft downgrade.
 */
export const ADAPTIVE_THINKING_MODELS = [
  "claude-fable-5",
  "claude-mythos-5",
  "claude-opus-5",
  "claude-opus-4-8",
  "claude-opus-4-7",
  "claude-sonnet-5",
] as const;

/** Above this many output tokens the SDK requires streaming or the request can time out. */
export const STREAM_ABOVE_MAX_TOKENS = 20_000;

export interface Provider {
  readonly name: string;
  complete(request: Request): Promise<Response>;
}

function isAdaptiveModel(model: string): boolean {
  return ADAPTIVE_THINKING_MODELS.some((m) => model.startsWith(m));
}

/**
 * Translate a `Request` into Messages API params.
 *
 * Exported separately from the provider so it can be unit-tested without a
 * client, and so you can inspect exactly what would go over the wire.
 */
export function buildParams(request: Request): Record<string, unknown> {
  const params: Record<string, unknown> = {
    model: request.model,
    max_tokens: request.maxTokens,
    messages: request.messages.map((m) => ({ role: m.role, content: m.content })),
  };
  if (request.system !== undefined) params["system"] = request.system;
  if (request.tools.length) params["tools"] = request.tools;
  if (request.toolChoice) params["tool_choice"] = request.toolChoice;
  if (request.stopSequences.length) params["stop_sequences"] = request.stopSequences;
  if (request.cache) params["cache_control"] = { type: "ephemeral" };

  const outputConfig: Record<string, unknown> = {};
  if (request.effort) outputConfig["effort"] = request.effort;
  if (request.outputSchema) {
    outputConfig["format"] = { type: "json_schema", schema: request.outputSchema };
  }
  if (Object.keys(outputConfig).length) params["output_config"] = outputConfig;

  if (request.thinking) {
    const thinking = { ...request.thinking };
    // `budget_tokens` is removed on these models -- sending it is a 400.
    if (isAdaptiveModel(request.model)) delete thinking["budget_tokens"];
    params["thinking"] = thinking;
  }
  return params;
}

interface AnthropicLike {
  messages: {
    create(params: Record<string, unknown>): Promise<unknown>;
    stream(params: Record<string, unknown>): { finalMessage(): Promise<unknown> };
  };
}

/**
 * Adapter over the official `@anthropic-ai/sdk`.
 *
 * The SDK is an optional peer dependency, imported lazily, so the core and the
 * whole test suite run without it installed. Credentials are resolved by the
 * SDK itself -- do not pass a key unless you must inject a specific one.
 */
export class AnthropicProvider implements Provider {
  readonly name = "anthropic";
  #client: AnthropicLike | undefined;
  readonly #options: Record<string, unknown>;

  constructor(client?: AnthropicLike, options: Record<string, unknown> = {}) {
    this.#client = client;
    this.#options = options;
  }

  async #resolve(): Promise<AnthropicLike> {
    if (this.#client) return this.#client;
    try {
      // The SDK's `messages.create` is heavily overloaded; `AnthropicLike` is
      // the narrow slice this adapter uses. Widening through `unknown` is the
      // honest way to say "we only depend on this shape", rather than
      // reimplementing the SDK's parameter unions here and letting them drift.
      const mod = (await import("@anthropic-ai/sdk")) as unknown as {
        default: new (o: unknown) => AnthropicLike;
      };
      this.#client = new mod.default(this.#options);
      return this.#client;
    } catch {
      throw new ProviderError(
        "AnthropicProvider needs the SDK: npm install @anthropic-ai/sdk",
        { retryable: false },
      );
    }
  }

  async complete(request: Request): Promise<Response> {
    const client = await this.#resolve();
    const params = buildParams(request);
    const useStream = request.stream || request.maxTokens > STREAM_ABOVE_MAX_TOKENS;

    try {
      const message = useStream
        ? await client.messages.stream(params).finalMessage()
        : await client.messages.create(params);
      return toResponse(message);
    } catch (error) {
      throw translateError(error);
    }
  }
}

/** Map an SDK error onto a retryable / not-retryable decision. */
export function translateError(error: unknown): ProviderError {
  if (error instanceof ProviderError) return error;
  const status = (error as { status?: number })?.status;
  const message = error instanceof Error ? error.message : String(error);

  // Retry what can succeed on a second try. Retrying a 400 just burns time.
  const retryable =
    status === undefined
      ? /ECONNRESET|ETIMEDOUT|ENOTFOUND|socket hang up|fetch failed/i.test(message)
      : status === 408 || status === 409 || status === 429 || status >= 500;

  return new ProviderError(message, { retryable, ...(status !== undefined ? { status } : {}) });
}

interface RawBlock {
  type?: string;
  text?: string;
  id?: string;
  name?: string;
  input?: Record<string, unknown>;
}

/** Normalize an SDK `Message` into `Response`. */
export function toResponse(message: unknown): Response {
  const raw = message as {
    content?: RawBlock[];
    usage?: Partial<Record<string, number>>;
    stop_reason?: StopReason;
    stop_details?: { category?: string };
    model?: string;
  };

  const texts: string[] = [];
  const toolCalls: ToolCall[] = [];
  for (const block of raw.content ?? []) {
    if (block.type === "text" && typeof block.text === "string") texts.push(block.text);
    else if (block.type === "tool_use" && block.id && block.name) {
      toolCalls.push({ id: block.id, name: block.name, input: block.input ?? {} });
    }
  }

  const usage: Usage = {
    inputTokens: raw.usage?.["input_tokens"] ?? 0,
    outputTokens: raw.usage?.["output_tokens"] ?? 0,
    cacheCreationInputTokens: raw.usage?.["cache_creation_input_tokens"] ?? 0,
    cacheReadInputTokens: raw.usage?.["cache_read_input_tokens"] ?? 0,
  };

  // stop_details is populated only on a refusal; guard before reading it.
  const refusalCategory =
    raw.stop_reason === "refusal" ? raw.stop_details?.category : undefined;

  return {
    text: texts.join(""),
    ...(raw.stop_reason ? { stopReason: raw.stop_reason } : {}),
    toolCalls,
    usage,
    model: raw.model ?? "",
    content: raw.content ?? [],
    ...(refusalCategory ? { refusalCategory } : {}),
    raw: message,
  };
}

// --------------------------------------------------------------------------- //
// Fake
// --------------------------------------------------------------------------- //

export type Script = Response | string | ((request: Request) => Response | string);

/**
 * A scripted provider for tests, examples and CI.
 *
 * When the script runs out the last entry repeats, so a one-element script is a
 * constant provider. Every request lands on `.requests`, which is usually the
 * thing you actually want to assert on.
 */
export class FakeProvider implements Provider {
  readonly name = "fake";
  readonly requests: Request[] = [];
  calls = 0;
  readonly #script: Script[];

  constructor(script: Script[] | Script = ["ok"]) {
    this.#script = Array.isArray(script) ? script : [script];
    if (this.#script.length === 0) {
      throw new Error("FakeProvider needs at least one scripted reply");
    }
  }

  async complete(request: Request): Promise<Response> {
    this.requests.push(request);
    const index = Math.min(this.calls, this.#script.length - 1);
    this.calls += 1;

    let entry = this.#script[index] as Script;
    if (typeof entry === "function") entry = entry(request);
    if (typeof entry === "string") {
      return makeResponse({
        text: entry,
        stopReason: "end_turn",
        model: request.model,
        usage: {
          ...EMPTY_USAGE,
          inputTokens: Math.max(1, JSON.stringify(request.messages).length / 4) | 0,
          outputTokens: (entry.length / 4) | 0,
        },
      });
    }
    return entry;
  }
}

/** Build an `end_turn` text response. Sugar for fake scripts. */
export function textResponse(text: string, init: Partial<Response> = {}): Response {
  return makeResponse({ text, stopReason: "end_turn", ...init });
}

/** Build a `tool_use` response. Sugar for fake scripts. */
export function toolResponse(calls: ToolCall[], init: Partial<Response> = {}): Response {
  return makeResponse({ text: "", stopReason: "tool_use", toolCalls: calls, ...init });
}

// --------------------------------------------------------------------------- //
// Retry
// --------------------------------------------------------------------------- //

export interface RetryOptions {
  attempts?: number;
  baseDelayMs?: number;
  maxDelayMs?: number;
  sleep?: (ms: number) => Promise<void>;
}

/**
 * Wrap a provider with jittered exponential backoff on retryable errors.
 *
 * The SDK already retries twice. This is the outer layer for long agent runs
 * where waiting a minute beats losing an hour of work. Non-retryable errors
 * propagate immediately.
 */
export function withRetry(provider: Provider, options: RetryOptions = {}): Provider {
  const attempts = options.attempts ?? 3;
  const baseDelay = options.baseDelayMs ?? 1000;
  const maxDelay = options.maxDelayMs ?? 30_000;
  const sleep = options.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms)));

  return {
    name: `retry(${provider.name})`,
    async complete(request: Request): Promise<Response> {
      let last: unknown;
      for (let attempt = 0; attempt < attempts; attempt += 1) {
        try {
          return await provider.complete(request);
        } catch (error) {
          const err = translateError(error);
          if (!err.retryable || attempt === attempts - 1) throw err;
          last = err;
          const delay = Math.min(maxDelay, baseDelay * 2 ** attempt);
          await sleep(delay * (0.5 + Math.random() / 2));
        }
      }
      throw last;
    },
  };
}
