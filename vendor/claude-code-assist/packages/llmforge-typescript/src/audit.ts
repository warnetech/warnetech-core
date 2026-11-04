/**
 * Tamper-evident audit trail and fail-safe authority controls.
 *
 * An ordinary log answers "what did the agent do". It does not answer "is this
 * log the same log that was written" -- and on any path where someone will
 * later be asked to account for an action, that is the question that gets asked.
 *
 * **What this is not.** A hash chain proves *internal* consistency. Someone who
 * can rewrite the whole file can rewrite the whole chain. Real tamper-evidence
 * needs an external anchor: ship `head` somewhere the agent cannot reach and
 * compare. This makes that cheap; it cannot do it for you, and pretending
 * otherwise would be exactly the unearned assurance this package exists to
 * avoid.
 */

import { createHash } from "node:crypto";
import { redact } from "./guard.js";
import { ALLOW, deny, type Decision, type Tool, type ToolPolicy } from "./tools.js";
import type { ToolCall } from "./types.js";

export const GENESIS = "0".repeat(64);

function sha256(payload: string): string {
  return createHash("sha256").update(payload, "utf8").digest("hex");
}

export interface Entry {
  seq: number;
  at: number;
  actor: string;
  action: string;
  detail: Record<string, unknown>;
  prev: string;
  digest: string;
}

/** Recompute an entry's digest from its own contents. */
export function digestOf(entry: Omit<Entry, "digest">): string {
  return sha256(
    JSON.stringify({
      seq: entry.seq,
      at: entry.at,
      actor: entry.actor,
      action: entry.action,
      detail: entry.detail,
      prev: entry.prev,
    }),
  );
}

export interface ChainCheck {
  ok: boolean;
  entries: number;
  brokenAt?: number;
  reason: string;
}

/**
 * Append-only, hash-chained record of everything an agent did.
 *
 * Secrets are redacted on write, not on read. A credential that reaches the log
 * has already leaked -- it is on disk, in backups, and in whatever ships logs
 * off the host. Redaction after the fact is theatre.
 */
export class AuditLog {
  readonly entries: Entry[] = [];
  readonly #sink: ((entry: Entry) => void) | undefined;

  constructor(options: { sink?: (entry: Entry) => void; entries?: Entry[] } = {}) {
    this.#sink = options.sink;
    if (options.entries) this.entries.push(...options.entries);
  }

  /**
   * The digest committing to the entire chain so far. Publish it somewhere the
   * agent cannot write -- that is what turns internal consistency into actual
   * tamper-evidence.
   */
  get head(): string {
    return this.entries.length ? (this.entries[this.entries.length - 1] as Entry).digest : GENESIS;
  }

  record(actor: string, action: string, detail: Record<string, unknown> = {}): Entry {
    const clean: Record<string, unknown> = {};
    const redacted: string[] = [];
    for (const [key, value] of Object.entries(detail)) {
      if (typeof value === "string") {
        const result = redact(value);
        clean[key] = result.text;
        if (result.found.length) redacted.push(`${key}:${result.found.join(",")}`);
      } else {
        clean[key] = value;
      }
    }
    if (redacted.length) clean["_redacted"] = redacted;

    const base = {
      seq: this.entries.length,
      at: Date.now(),
      actor,
      action,
      detail: clean,
      prev: this.head,
    };
    const entry: Entry = { ...base, digest: digestOf(base) };
    this.entries.push(entry);
    this.#sink?.(entry);
    return entry;
  }

  /**
   * Walk the chain and report the first entry that does not hold.
   *
   * Two independent failures are detected: an entry whose digest does not match
   * its own contents (edited), and one whose `prev` does not match the previous
   * digest (removed, reordered, or inserted).
   */
  verify(): ChainCheck {
    let previous = GENESIS;
    for (const entry of this.entries) {
      if (entry.prev !== previous) {
        return {
          ok: false,
          entries: this.entries.length,
          brokenAt: entry.seq,
          reason: `entry ${entry.seq} links to ${entry.prev.slice(0, 12)}, expected ${previous.slice(0, 12)} -- an entry was removed, reordered, or inserted`,
        };
      }
      if (digestOf(entry) !== entry.digest) {
        return {
          ok: false,
          entries: this.entries.length,
          brokenAt: entry.seq,
          reason: `entry ${entry.seq} digest does not match its contents -- it was edited`,
        };
      }
      previous = entry.digest;
    }
    return { ok: true, entries: this.entries.length, reason: "" };
  }

  byAction(action: string): Entry[] {
    return this.entries.filter((e) => e.action === action);
  }
}

/**
 * The two-person rule for high-consequence actions.
 *
 * One approver can be socially engineered, can be the agent wearing a
 * convincing hat, or can simply be tired at 2am. Two *distinct* approvers is
 * the oldest control there is for actions that cannot be undone.
 */
export class DualControl {
  readonly #pending = new Map<string, Set<string>>();
  readonly approvers: Set<string>;

  constructor(
    readonly log: AuditLog,
    approvers: Iterable<string>,
    readonly required = 2,
  ) {
    this.approvers = new Set(approvers);
    if (this.approvers.size < required) {
      throw new Error(
        `dual control needs at least ${required} distinct approvers, got ${this.approvers.size}`,
      );
    }
  }

  /** Record one approval. Returns true once the threshold is met. */
  approve(action: string, approver: string): boolean {
    if (!this.approvers.has(approver)) {
      this.log.record(approver, "approval.rejected", {
        action,
        reason: "not an authorized approver",
      });
      return false;
    }
    const held = this.#pending.get(action) ?? new Set<string>();
    held.add(approver);
    this.#pending.set(action, held);
    this.log.record(approver, "approval.granted", {
      action,
      held: held.size,
      required: this.required,
    });
    return held.size >= this.required;
  }

  authorized(action: string): boolean {
    return (this.#pending.get(action)?.size ?? 0) >= this.required;
  }

  revoke(action: string): void {
    this.#pending.delete(action);
    this.log.record("system", "approval.revoked", { action });
  }
}

export interface SafePolicyOptions {
  allow?: Iterable<string>;
  dualControl?: DualControl;
  onDeny?: (tool: Tool<never>, call: ToolCall, reason: string) => void;
}

/**
 * Deny-by-default tool authority, with everything recorded.
 *
 * The usual policy allows a tool unless something objects. That default is
 * right when the worst case is an error message and wrong when it is
 * irreversible: an agent that acquires a tool through a config change, a merge,
 * or an MCP server it did not have yesterday inherits authority nobody granted.
 *
 * Here nothing runs unless its name is on the list. New tools arrive denied --
 * noisy the first time, correct every time.
 */
export function safePolicy(log: AuditLog, options: SafePolicyOptions = {}): ToolPolicy {
  const allow = new Set(options.allow ?? []);
  const { dualControl, onDeny } = options;

  const refuse = (tool: Tool<never>, call: ToolCall, reason: string): Decision => {
    log.record("policy", "tool.denied", { tool: call.name, reason });
    onDeny?.(tool, call, reason);
    return deny(reason);
  };

  return {
    check(tool, call) {
      if (!allow.has(call.name)) {
        return refuse(
          tool,
          call,
          `'${call.name}' is not in the allowed set (${[...allow].sort().join(", ") || "empty"}); authority is granted explicitly, never inherited`,
        );
      }
      if (tool.destructive && dualControl && !dualControl.authorized(call.name)) {
        return refuse(
          tool,
          call,
          `'${call.name}' is destructive and lacks ${dualControl.required} approvals`,
        );
      }
      log.record("policy", "tool.allowed", {
        tool: call.name,
        input: JSON.stringify(call.input).slice(0, 400),
      });
      return ALLOW;
    },
  };
}
