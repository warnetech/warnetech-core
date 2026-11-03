/**
 * Ensemble: draw k candidates, then choose between them.
 *
 * **Claim.** When verifying an answer is cheaper than producing one, spending a
 * fixed budget on k samples plus selection beats spending it all on one very
 * careful sample. Code is the ideal case: generation is expensive and
 * open-ended, "does the test pass" is nearly free.
 *
 * **How it would fail.** Correlated errors. k samples from one model at one
 * temperature are not k independent draws; if the model misreads the prompt it
 * misreads it every time, and the ensemble converts one wrong answer into a
 * *confident* wrong answer. Diversify what you can via `vary`, and treat
 * unanimous agreement on a hard question as suspicious, not reassuring.
 *
 * **Measurement.** pass@1 of best-of-k against a single sample at k-times the
 * effort, at equal dollar spend. If they tie, the extra latency is a loss.
 */

import type { Provider } from "../provider.js";
import { costUsd } from "../trace.js";
import { request as makeRequest, type Request, type Response } from "../types.js";

export interface Candidate {
  text: string;
  index: number;
  score: number;
  detail: string;
  response: Response;
}

export interface EnsembleResult {
  winner: string;
  candidates: Candidate[];
  /** Share of candidates clustering with the winner. Confidence, roughly. */
  agreement: number;
  costUsd: number;
  selector: string;
  unanimous: boolean;
  /** No option cleared half the votes -- a good trigger to escalate. */
  contested: boolean;
}

export interface SampleOptions {
  k?: number;
  model?: string;
  system?: string;
  maxTokens?: number;
  effort?: Request["effort"];
  /** Per-sample overrides that decorrelate the draws. */
  vary?: Partial<Request>[];
  parallel?: boolean;
}

async function sample(
  provider: Provider,
  base: Request,
  options: Required<Pick<SampleOptions, "k" | "parallel">> & { vary?: Partial<Request>[] },
): Promise<Response[]> {
  const requests: Request[] = [];
  for (let i = 0; i < options.k; i += 1) {
    const overrides = options.vary?.length
      ? options.vary[i % options.vary.length]
      : undefined;
    requests.push({
      ...base,
      ...overrides,
      metadata: { ...base.metadata, sample: i },
    });
  }
  if (!options.parallel) {
    const out: Response[] = [];
    for (const req of requests) out.push(await provider.complete(req));
    return out;
  }
  return Promise.all(requests.map((req) => provider.complete(req)));
}

function summarize(
  candidates: Candidate[],
  winner: string,
  agreement: number,
  cost: number,
  selector: string,
): EnsembleResult {
  return {
    winner,
    candidates,
    agreement,
    costUsd: cost,
    selector,
    unanimous: agreement >= 1,
    contested: agreement < 0.5,
  };
}

/** Draw k candidates and keep the highest-scoring one. */
export async function bestOfN(
  provider: Provider,
  prompt: string,
  score: (text: string) => number,
  options: SampleOptions = {},
): Promise<EnsembleResult> {
  const model = options.model ?? "claude-opus-5";
  const base = makeRequest({
    messages: [{ role: "user", content: prompt }],
    model,
    maxTokens: options.maxTokens ?? 8000,
    metadata: { lab: "ensemble" },
    ...(options.system ? { system: options.system } : {}),
    ...(options.effort ? { effort: options.effort } : {}),
  });

  const responses = await sample(provider, base, {
    k: options.k ?? 4,
    parallel: options.parallel ?? true,
    ...(options.vary ? { vary: options.vary } : {}),
  });

  const candidates: Candidate[] = responses.map((response, index) => ({
    text: response.text,
    index,
    score: score(response.text),
    detail: "",
    response,
  }));
  candidates.sort((a, b) => b.score - a.score);

  const best = candidates[0] as Candidate;
  const ties = candidates.filter((c) => c.score === best.score).length;
  const cost = responses.reduce((sum, r) => sum + costUsd(r.model || model, r.usage), 0);
  return summarize(candidates, best.text, ties / candidates.length, cost, "score");
}

/**
 * Canonical form for agreement clustering.
 *
 * Deliberately aggressive. Two answers that differ only in formatting are the
 * same answer, and treating them as different is what makes naive
 * self-consistency report 0% agreement on everything.
 */
export function normalize(text: string): string {
  return text.trim().toLowerCase().replace(/\s+/g, " ").replace(/[.!,;:]+$/, "");
}

/**
 * Take the plurality answer across k samples.
 *
 * `extract` reduces a response to the thing being voted on -- a label, a final
 * number. Voting on raw prose produces k clusters of size 1 and tells you
 * nothing.
 *
 * The useful output is often not `winner` but `agreement`: low agreement
 * reliably signals an ambiguous question or a guessing model, and it is a
 * better escalation trigger than any confidence a model states about itself.
 */
export async function selfConsistency(
  provider: Provider,
  prompt: string,
  options: SampleOptions & { extract?: (text: string) => string } = {},
): Promise<EnsembleResult> {
  const model = options.model ?? "claude-opus-5";
  const extract = options.extract ?? normalize;
  const base = makeRequest({
    messages: [{ role: "user", content: prompt }],
    model,
    maxTokens: options.maxTokens ?? 2000,
    metadata: { lab: "ensemble", selector: "selfConsistency" },
    ...(options.system ? { system: options.system } : {}),
  });

  const responses = await sample(provider, base, {
    k: options.k ?? 5,
    parallel: options.parallel ?? true,
    ...(options.vary ? { vary: options.vary } : {}),
  });

  const keys = responses.map((r) => extract(r.text));
  const votes = new Map<string, number>();
  for (const key of keys) votes.set(key, (votes.get(key) ?? 0) + 1);

  let winningKey = keys[0] as string;
  let count = 0;
  for (const [key, n] of votes) {
    if (n > count) [winningKey, count] = [key, n];
  }
  const winnerIndex = keys.indexOf(winningKey);

  const candidates: Candidate[] = responses.map((response, index) => ({
    text: response.text,
    index,
    score: votes.get(keys[index] as string) ?? 0,
    detail: keys[index] as string,
    response,
  }));
  const cost = responses.reduce((sum, r) => sum + costUsd(r.model || model, r.usage), 0);

  return summarize(
    candidates,
    (responses[winnerIndex] as Response).text,
    count / responses.length,
    cost,
    "selfConsistency",
  );
}
