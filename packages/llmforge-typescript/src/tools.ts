/**
 * Tool definition, schemas, and gated dispatch.
 *
 * TypeScript types vanish at runtime, so unlike the Python side there is no
 * signature to read a schema from -- you write the JSON Schema. In exchange you
 * get compile-time checking of the handler's input, which the Python side
 * cannot offer. `defineTool` ties the two together: one schema, one inferred
 * input type, no drift.
 */

import type { ToolCall, ToolResult } from "./types.js";

export interface JsonSchema {
  type: "object";
  properties: Record<string, unknown>;
  required?: string[];
  additionalProperties?: boolean;
}

export interface ToolSpec<I = Record<string, unknown>> {
  name: string;
  description: string;
  inputSchema: JsonSchema;
  run: (input: I) => unknown | Promise<unknown>;
  /** Emit `strict: true` so the API guarantees inputs validate. */
  strict?: boolean;
  /** Read-only and side-effect free: the harness may fan these out. */
  parallelSafe?: boolean;
  /** Hard to reverse. Policies should gate these by default. */
  destructive?: boolean;
  tags?: string[];
}

export interface Tool<I = Record<string, unknown>> extends Required<Omit<ToolSpec<I>, "tags">> {
  tags: Set<string>;
}

export function defineTool<I = Record<string, unknown>>(spec: ToolSpec<I>): Tool<I> {
  return {
    strict: true,
    parallelSafe: false,
    destructive: false,
    ...spec,
    inputSchema: {
      // Required for `strict: true`, and it stops Claude inventing keys.
      additionalProperties: false,
      required: [],
      ...spec.inputSchema,
    },
    tags: new Set(spec.tags ?? []),
  };
}

/** The definition to put in `request.tools`. */
export function toolDefinition(tool: Tool<never>): Record<string, unknown> {
  const spec: Record<string, unknown> = {
    name: tool.name,
    description: tool.description,
    input_schema: tool.inputSchema,
  };
  if (tool.strict) spec["strict"] = true;
  return spec;
}

// --------------------------------------------------------------------------- //
// Policy
// --------------------------------------------------------------------------- //

export interface Decision {
  allow: boolean;
  reason: string;
}

export const ALLOW: Decision = { allow: true, reason: "" };
export function deny(reason: string): Decision {
  return { allow: false, reason };
}

export interface ToolPolicy {
  check(tool: Tool<never>, call: ToolCall): Decision;
}

/** Allows everything. The right default only when nothing is irreversible. */
export const OPEN_POLICY: ToolPolicy = { check: () => ALLOW };

export interface GatedPolicyOptions {
  approve?: (tool: Tool<never>, call: ToolCall) => boolean;
  allowTags?: string[];
  denyTools?: string[];
}

/**
 * Denies destructive tools unless explicitly approved.
 *
 * In a CLI `approve` is a prompt; in a server it is a queued approval; in CI it
 * is `() => false`, which is exactly what you want from an unattended run.
 */
export function gatedPolicy(options: GatedPolicyOptions = {}): ToolPolicy {
  const approve = options.approve ?? (() => false);
  const allowTags = new Set(options.allowTags ?? []);
  const denyTools = new Set(options.denyTools ?? []);

  return {
    check(tool, call) {
      if (denyTools.has(tool.name)) return deny(`tool '${tool.name}' is denied by policy`);
      for (const tag of tool.tags) if (allowTags.has(tag)) return ALLOW;
      if (tool.destructive && !approve(tool, call)) {
        return deny(`'${tool.name}' is destructive and was not approved by the operator`);
      }
      return ALLOW;
    },
  };
}

// --------------------------------------------------------------------------- //
// Registry
// --------------------------------------------------------------------------- //

/**
 * A named set of tools, plus dispatch.
 *
 * Insertion order is preserved on purpose: reordering the tool list changes the
 * request prefix and invalidates the prompt cache.
 */
export class ToolRegistry {
  readonly #tools = new Map<string, Tool<never>>();
  policy: ToolPolicy;

  constructor(tools: Tool<never>[] | Tool<never> = [], policy: ToolPolicy = OPEN_POLICY) {
    for (const tool of Array.isArray(tools) ? tools : [tools]) this.add(tool);
    this.policy = policy;
  }

  add(tool: Tool<never>): this {
    if (this.#tools.has(tool.name)) throw new Error(`duplicate tool name: ${tool.name}`);
    this.#tools.set(tool.name, tool);
    return this;
  }

  get(name: string): Tool<never> | undefined {
    return this.#tools.get(name);
  }

  get size(): number {
    return this.#tools.size;
  }

  definitions(): Record<string, unknown>[] {
    return [...this.#tools.values()].map(toolDefinition);
  }

  /**
   * Run one tool call, converting every failure into an error result.
   *
   * A tool that throws must not end the run: Claude can often recover from a
   * readable error, and a crashed loop loses all the work before it.
   */
  async dispatch(call: ToolCall): Promise<ToolResult> {
    const tool = this.#tools.get(call.name);
    if (!tool) {
      const known = [...this.#tools.keys()].sort().join(", ") || "(none)";
      return {
        toolUseId: call.id,
        content: `Error: no tool named '${call.name}'. Available tools: ${known}.`,
        isError: true,
      };
    }

    const verdict = this.policy.check(tool, call);
    if (!verdict.allow) {
      return {
        toolUseId: call.id,
        content: `Error: refused by policy. ${verdict.reason}`,
        isError: true,
      };
    }

    try {
      const value = await tool.run(call.input as never);
      return { toolUseId: call.id, content: stringify(value), isError: false };
    } catch (error) {
      const name = error instanceof Error ? error.name : "Error";
      const message = error instanceof Error ? error.message : String(error);
      return { toolUseId: call.id, content: `Error: ${name}: ${message}`, isError: true };
    }
  }
}

/** Coerce a tool return value into text for a `tool_result` block. */
export function stringify(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "(no output)";
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return String(value);
  }
}
