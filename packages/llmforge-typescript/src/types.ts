/**
 * Provider-neutral request/response types.
 *
 * Deliberately thin. Everything above the provider boundary -- the agent loop,
 * the assurance gates, the labs -- is exercisable against `FakeProvider` with
 * no network and no SDK installed.
 *
 * These do not replace the Anthropic SDK's types. When you need full response
 * fidelity (thinking blocks, citations, container ids), reach through
 * `Response.raw`, which always holds the untouched SDK object.
 */

export type Role = "user" | "assistant" | "system";

export type StopReason =
  | "end_turn"
  | "max_tokens"
  | "stop_sequence"
  | "tool_use"
  | "pause_turn"
  | "refusal";

export type Effort = "low" | "medium" | "high" | "xhigh" | "max";

export interface Usage {
  inputTokens: number;
  outputTokens: number;
  cacheCreationInputTokens: number;
  cacheReadInputTokens: number;
}

export const EMPTY_USAGE: Usage = {
  inputTokens: 0,
  outputTokens: 0,
  cacheCreationInputTokens: 0,
  cacheReadInputTokens: 0,
};

export function addUsage(a: Usage, b: Usage): Usage {
  return {
    inputTokens: a.inputTokens + b.inputTokens,
    outputTokens: a.outputTokens + b.outputTokens,
    cacheCreationInputTokens: a.cacheCreationInputTokens + b.cacheCreationInputTokens,
    cacheReadInputTokens: a.cacheReadInputTokens + b.cacheReadInputTokens,
  };
}

/** Every input token, however it was billed. */
export function totalInput(usage: Usage): number {
  return usage.inputTokens + usage.cacheCreationInputTokens + usage.cacheReadInputTokens;
}

/** Share of input tokens served from cache. 0 when there is no input. */
export function cacheHitRate(usage: Usage): number {
  const total = totalInput(usage);
  return total === 0 ? 0 : usage.cacheReadInputTokens / total;
}

export interface ToolCall {
  id: string;
  name: string;
  input: Record<string, unknown>;
}

export interface ToolResult {
  toolUseId: string;
  content: string;
  isError: boolean;
}

export function toResultBlock(result: ToolResult): Record<string, unknown> {
  const block: Record<string, unknown> = {
    type: "tool_result",
    tool_use_id: result.toolUseId,
    content: result.content,
  };
  if (result.isError) block["is_error"] = true;
  return block;
}

export interface Message {
  role: Role;
  content: unknown;
}

export interface Request {
  messages: Message[];
  model: string;
  system?: string | Record<string, unknown>[];
  tools: Record<string, unknown>[];
  maxTokens: number;
  effort?: Effort;
  thinking?: Record<string, unknown>;
  outputSchema?: Record<string, unknown>;
  toolChoice?: Record<string, unknown>;
  stopSequences: string[];
  /** Set the top-level `cache_control` breakpoint on the last cacheable block. */
  cache: boolean;
  /** Required by the SDKs whenever maxTokens is large; the adapter forces it on. */
  stream: boolean;
  /** Free-form tags carried into traces. Never sent to the API. */
  metadata: Record<string, unknown>;
}

export function request(init: Partial<Request> & { messages: Message[] }): Request {
  return {
    model: "claude-opus-5",
    tools: [],
    maxTokens: 16000,
    stopSequences: [],
    cache: false,
    stream: false,
    metadata: {},
    ...init,
  };
}

export interface Response {
  text: string;
  stopReason?: StopReason;
  toolCalls: ToolCall[];
  usage: Usage;
  model: string;
  /** The raw content-block list, echoed back verbatim into the transcript. */
  content: unknown[];
  refusalCategory?: string;
  raw?: unknown;
}

export function response(init: Partial<Response> & { text: string }): Response {
  return {
    toolCalls: [],
    usage: { ...EMPTY_USAGE },
    model: "",
    content: [],
    ...init,
  };
}

export class ProviderError extends Error {
  readonly retryable: boolean;
  readonly status?: number;

  constructor(message: string, options: { retryable?: boolean; status?: number } = {}) {
    super(message);
    this.name = "ProviderError";
    this.retryable = options.retryable ?? false;
    if (options.status !== undefined) this.status = options.status;
  }
}
