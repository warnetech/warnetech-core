/**
 * Ladder: escalate from cheap to strong only when a verifier says to.
 *
 * **Claim.** Most requests in a production workload are easy. Routing all of
 * them to the strongest model pays the hard-case price on every case. With a
 * verifier cheaper than generation, a ladder lands near top-model quality at a
 * fraction of the spend, because the expensive rung only runs where it is
 * needed.
 *
 * The technique lives or dies on one thing: **the verifier must be trustworthy
 * in the negative direction.** A verifier that wrongly says "good" silently
 * ships the cheap model's mistake and you will not see it in aggregate metrics.
 * One that wrongly says "bad" merely costs money. Prefer verifiers that fail
 * closed -- tests, compilers, schema validation -- over ones that opine.
 *
 * **Safety-critical note.** Where an error is expensive to reverse, do not
 * ladder, or set `requireTopRung` so the strongest model signs off even when a
 * cheaper rung passed. Saving four cents is not a reason to accept a different
 * risk profile on a path that can hurt someone.
 *
 * **How it would fail.** Verifier miscalibration, and distribution shift: the
 * mix of easy to hard cases moves, escalation rate moves with it, and the
 * budgeted savings evaporate. Track escalation rate as a first-class metric.
 */

import type { Provider } from "../provider.js";
import { costUsd } from "../trace.js";
import { request as makeRequest, type Effort } from "../types.js";

export interface Rung {
  model: string;
  effort?: Effort;
  maxTokens?: number;
  label?: string;
}

export const DEFAULT_LADDER: Rung[] = [
  { model: "claude-haiku-4-5", maxTokens: 4000 },
  { model: "claude-sonnet-5", effort: "medium" },
  { model: "claude-opus-5", effort: "high" },
];

export function labelOf(rung: Rung): string {
  return rung.label ?? `${rung.model}/${rung.effort ?? "default"}`;
}

export interface Attempt {
  rung: Rung;
  text: string;
  accepted: boolean;
  reason: string;
  costUsd: number;
}

export interface LadderResult {
  text: string;
  accepted: boolean;
  attempts: Attempt[];
  costUsd: number;
  rungsUsed: number;
  escalated: boolean;
  settledOn: string;
}

export type Verifier = (text: string) => Promise<[boolean, string]> | [boolean, string];

export interface LadderOptions {
  rungs?: Rung[];
  system?: string;
  /** Force the top rung to sign off even when a cheaper one passed. */
  requireTopRung?: boolean;
}

/** Walk the ladder until the verifier accepts, or the rungs run out. */
export async function escalate(
  provider: Provider,
  prompt: string,
  verify: Verifier,
  options: LadderOptions = {},
): Promise<LadderResult> {
  const rungs = options.rungs ?? DEFAULT_LADDER;
  const attempts: Attempt[] = [];
  let total = 0;
  let winner = "";
  let accepted = false;

  for (const rung of rungs) {
    const response = await provider.complete(
      makeRequest({
        messages: [{ role: "user", content: prompt }],
        model: rung.model,
        maxTokens: rung.maxTokens ?? 8000,
        metadata: { lab: "ladder", rung: labelOf(rung) },
        ...(options.system ? { system: options.system } : {}),
        ...(rung.effort ? { effort: rung.effort } : {}),
      }),
    );

    const cost = costUsd(response.model || rung.model, response.usage);
    total += cost;
    const [ok, reason] = await verify(response.text);
    attempts.push({ rung, text: response.text, accepted: ok, reason, costUsd: cost });

    if (ok) {
      winner = response.text;
      accepted = true;
      const isTop = rung === rungs[rungs.length - 1];
      if (!options.requireTopRung || isTop) break;
    }
  }

  // Nothing verified. Return the top rung's attempt and say so plainly --
  // returning a rejected answer as if it passed is what this module exists to
  // avoid.
  if (!accepted && attempts.length) {
    winner = (attempts[attempts.length - 1] as Attempt).text;
  }

  const last = attempts[attempts.length - 1];
  return {
    text: winner,
    accepted,
    attempts,
    costUsd: total,
    rungsUsed: attempts.length,
    escalated: attempts.length > 1,
    settledOn: last ? labelOf(last.rung) : "",
  };
}

/**
 * Dollars saved against always running the top rung. Can be negative.
 *
 * Negative is the interesting case: a ladder that escalates most of the time
 * costs *more* than going straight to the strong model, because you paid for
 * the failed rungs too.
 */
export function savingsVs(result: LadderResult, topRungCost: number): number {
  return topRungCost - result.costUsd;
}
