/**
 * The agent loop.
 *
 * The SDK's tool runner handles the common case well; reach for it first. This
 * exists for what the runner does not expose, and every one of these shows up
 * in real integrations:
 *
 * - Budgets that bind: steps, wall-clock, and dollars, checked every turn.
 * - `pause_turn` resumption. The runner returns a paused turn as if it were
 *   final -- a silently truncated answer, no error, no warning.
 * - Refusals. `stop_reason: "refusal"` is an HTTP 200; code that reads content
 *   without checking gets an empty string.
 * - Transcript ownership, so you can compact, fork, or checkpoint it.
 */

import type { Provider } from "./provider.js";
import { ToolRegistry } from "./tools.js";
import { costUsd, NULL_TRACER, type Tracer } from "./trace.js";
import {
  addUsage,
  EMPTY_USAGE,
  request as makeRequest,
  toResultBlock,
  type Effort,
  type Message,
  type Request,
  type Response,
  type ToolCall,
  type ToolResult,
  type Usage,
} from "./types.js";

/**
 * Hard limits on a single run. Defaults are finite on purpose: an agent with
 * no ceiling is an outage waiting for the right prompt.
 */
export interface Budget {
  maxSteps: number;
  maxSeconds: number;
  maxUsd: number;
  maxPauseResumes: number;
  /** "throw" or "stop" -- stop returns the partial run instead. */
  onExceeded: "throw" | "stop";
}

export const DEFAULT_BUDGET: Budget = {
  maxSteps: 20,
  maxSeconds: 600,
  maxUsd: 5.0,
  maxPauseResumes: 5,
  onExceeded: "throw",
};

export interface Step {
  index: number;
  response: Response;
  results: ToolResult[];
  seconds: number;
}

export interface RunResult {
  text: string;
  messages: Message[];
  steps: Step[];
  usage: Usage;
  costUsd: number;
  stopReason?: string;
  /** Set when a budget stopped the run early. */
  halted?: string;
}

export class BudgetExceeded extends Error {
  readonly run: RunResult;
  constructor(message: string, run: RunResult) {
    super(message);
    this.name = "BudgetExceeded";
    this.run = run;
  }
}

export function toolCallsOf(run: RunResult): ToolCall[] {
  return run.steps.flatMap((s) => s.response.toolCalls);
}

export function toolErrorsOf(run: RunResult): ToolResult[] {
  return run.steps.flatMap((s) => s.results.filter((r) => r.isError));
}

export interface AgentOptions {
  tools?: ToolRegistry;
  system?: string | Record<string, unknown>[];
  model?: string;
  maxTokens?: number;
  effort?: Effort;
  thinking?: Record<string, unknown>;
  budget?: Partial<Budget>;
  tracer?: Tracer;
  cache?: boolean;
  onStep?: (agent: Agent, step: Step) => void | Promise<void>;
}

/** A model, a tool registry, and a bounded loop over the two. */
export class Agent {
  readonly provider: Provider;
  readonly tools: ToolRegistry;
  readonly budget: Budget;
  readonly tracer: Tracer;
  readonly model: string;
  readonly maxTokens: number;
  readonly system: string | Record<string, unknown>[] | undefined;
  readonly effort: Effort | undefined;
  readonly thinking: Record<string, unknown> | undefined;
  readonly cache: boolean;
  onStep: ((agent: Agent, step: Step) => void | Promise<void>) | undefined;

  constructor(provider: Provider, options: AgentOptions = {}) {
    this.provider = provider;
    this.tools = options.tools ?? new ToolRegistry();
    this.budget = { ...DEFAULT_BUDGET, ...options.budget };
    this.tracer = options.tracer ?? NULL_TRACER;
    this.model = options.model ?? "claude-opus-5";
    this.maxTokens = options.maxTokens ?? 16000;
    this.system = options.system;
    this.effort = options.effort;
    this.thinking = options.thinking;
    this.cache = options.cache ?? true;
    this.onStep = options.onStep;
  }

  #request(messages: Message[]): Request {
    return makeRequest({
      messages,
      model: this.model,
      maxTokens: this.maxTokens,
      tools: this.tools.definitions(),
      cache: this.cache,
      ...(this.system !== undefined ? { system: this.system } : {}),
      ...(this.effort ? { effort: this.effort } : {}),
      ...(this.thinking ? { thinking: this.thinking } : {}),
    });
  }

  /**
   * Drive the loop until the model stops asking for tools.
   *
   * `messages` continues an existing transcript. It is copied, never mutated.
   */
  async run(prompt: string | unknown[], messages: Message[] = []): Promise<RunResult> {
    const transcript: Message[] = [...messages, { role: "user", content: prompt }];
    const run: RunResult = {
      text: "",
      messages: transcript,
      steps: [],
      usage: { ...EMPTY_USAGE },
      costUsd: 0,
    };

    const started = Date.now();
    let pauseResumes = 0;

    return this.tracer.span(
      "agent.run",
      async () => {
        for (;;) {
          const halt = this.#checkBudget(run, started);
          if (halt) return this.#halt(run, halt);

          const stepStarted = Date.now();
          const response = await this.provider.complete(this.#request(transcript));

          run.usage = addUsage(run.usage, response.usage);
          run.costUsd += costUsd(response.model || this.model, response.usage);
          if (response.stopReason) run.stopReason = response.stopReason;

          const step: Step = { index: run.steps.length, response, results: [], seconds: 0 };
          run.steps.push(step);

          // A refusal is an HTTP 200 with empty-looking content. Stop here
          // rather than looping on a response that will never carry tools.
          if (response.stopReason === "refusal") {
            run.text = response.text;
            run.halted = `refusal:${response.refusalCategory ?? "unspecified"}`;
            step.seconds = (Date.now() - stepStarted) / 1000;
            await this.onStep?.(this, step);
            return run;
          }

          transcript.push({
            role: "assistant",
            content: response.content.length ? response.content : response.text,
          });

          // A server tool paused mid-turn. Re-send to continue; the paused
          // assistant turn is already the last entry.
          if (response.stopReason === "pause_turn") {
            pauseResumes += 1;
            step.seconds = (Date.now() - stepStarted) / 1000;
            await this.onStep?.(this, step);
            if (pauseResumes > this.budget.maxPauseResumes) {
              run.text = response.text;
              return this.#halt(run, "maxPauseResumes");
            }
            continue;
          }

          if (response.toolCalls.length === 0) {
            run.text = response.text;
            step.seconds = (Date.now() - stepStarted) / 1000;
            await this.onStep?.(this, step);
            return run;
          }

          // Every tool_use block needs a matching tool_result, and they all go
          // back in ONE user message -- splitting them across messages teaches
          // the model to stop calling tools in parallel.
          const results: ToolResult[] = [];
          for (const call of response.toolCalls) {
            results.push(
              await this.tracer.span(
                `tool.${call.name}`,
                async (span) => {
                  const result = await this.tools.dispatch(call);
                  span.attrs["isError"] = result.isError;
                  span.attrs["resultChars"] = result.content.length;
                  return result;
                },
                { kind: "tool", attrs: { tool: call.name } },
              ),
            );
          }
          step.results = results;
          transcript.push({ role: "user", content: results.map(toResultBlock) });
          step.seconds = (Date.now() - stepStarted) / 1000;
          await this.onStep?.(this, step);
        }
      },
      { kind: "run", attrs: { model: this.model } },
    );
  }

  #checkBudget(run: RunResult, started: number): string | undefined {
    if (run.steps.length >= this.budget.maxSteps) return "maxSteps";
    if ((Date.now() - started) / 1000 > this.budget.maxSeconds) return "maxSeconds";
    if (run.costUsd > this.budget.maxUsd) return "maxUsd";
    return undefined;
  }

  #halt(run: RunResult, reason: string): RunResult {
    run.halted = reason;
    if (!run.text && run.steps.length) {
      run.text = run.steps[run.steps.length - 1]!.response.text;
    }
    if (this.budget.onExceeded === "throw") {
      throw new BudgetExceeded(
        `run halted: ${reason} (steps=${run.steps.length}, cost=$${run.costUsd.toFixed(4)})`,
        run,
      );
    }
    return run;
  }
}
