/**
 * Parity: the TypeScript and Python gates must agree, case for case.
 *
 * Two implementations of one contract drift. That is not hypothetical -- the
 * project this discipline is borrowed from records the cost in its own source:
 *
 *   "New encryption: extend warnetech_envelope -- never add a second
 *    implementation; that is what broke the CLI/Worker channel."
 *
 * The assurance gates are exactly that shape: one contract, two
 * implementations. `fixtures/assurance-parity.json` is the pin. This file runs
 * every case through the TypeScript gates; `packages/llmforge-python/tests/test_parity.py`
 * runs the identical file through the Python ones. A finding added to one
 * language and not the other fails here.
 *
 * Assertions are on the machine-readable `code`, never on prose.
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  AssumptionLedger,
  SuccessCriteria,
  complexityBudget,
  diffDiscipline,
  type Verdict,
} from "../src/index.js";

const here = dirname(fileURLToPath(import.meta.url));
const FIXTURES = JSON.parse(
  readFileSync(join(here, "../../../fixtures/assurance-parity.json"), "utf8"),
) as {
  complexity: { name: string; code: string; options: Record<string, number>; expect: string[][] }[];
  diff: {
    name: string;
    diff: string;
    options: { requestTerms?: string[]; allowedPaths?: string[]; allowFormatting?: boolean };
    expect: string[][];
  }[];
  criteria: {
    name: string;
    criteria: { description: string; verified: boolean | null }[];
    expect: string[][];
  }[];
  ledger: {
    name: string;
    task: string;
    trivial: boolean;
    assumptions: [string, string][];
    openQuestions: string[];
    expect: string[][];
  }[];
};

/**
 * The comparable shape of a verdict: (code, severity) pairs, sorted.
 *
 * Order is not part of the contract -- pinning it would make the fixture fail
 * for a reason nobody cares about.
 */
function signature(verdict: Verdict): string[][] {
  return verdict.violations
    .map((v) => [v.code, v.severity])
    .sort((a, b) => (a.join() < b.join() ? -1 : 1));
}

const sortExpect = (rows: string[][]) =>
  [...rows].sort((a, b) => (a.join() < b.join() ? -1 : 1));

describe("complexity parity", () => {
  it.each(FIXTURES.complexity.map((c) => [c.name, c] as const))("%s", (_name, testCase) => {
    const o = testCase.options;
    const verdict = complexityBudget(testCase.code, {
      ...(o["maxLines"] !== undefined ? { maxLines: o["maxLines"] } : {}),
      ...(o["maxDefinitions"] !== undefined ? { maxDefinitions: o["maxDefinitions"] } : {}),
      ...(o["baselineLines"] !== undefined ? { baselineLines: o["baselineLines"] } : {}),
      ...(o["maxNesting"] !== undefined ? { maxNesting: o["maxNesting"] } : {}),
    });
    expect(signature(verdict)).toEqual(sortExpect(testCase.expect));
  });
});

describe("diff parity", () => {
  it.each(FIXTURES.diff.map((c) => [c.name, c] as const))("%s", (_name, testCase) => {
    const verdict = diffDiscipline(testCase.diff, {
      ...(testCase.options.requestTerms ? { requestTerms: testCase.options.requestTerms } : {}),
      ...(testCase.options.allowedPaths ? { allowedPaths: testCase.options.allowedPaths } : {}),
      ...(testCase.options.allowFormatting !== undefined
        ? { allowFormatting: testCase.options.allowFormatting }
        : {}),
    });
    expect(signature(verdict)).toEqual(sortExpect(testCase.expect));
  });
});

describe("criteria parity", () => {
  it.each(FIXTURES.criteria.map((c) => [c.name, c] as const))("%s", (_name, testCase) => {
    const goals = new SuccessCriteria(testCase.name);
    for (const criterion of testCase.criteria) {
      if (criterion.verified === null) goals.require(criterion.description);
      else goals.require(criterion.description, () => [criterion.verified as boolean, ""]);
    }
    expect(signature(goals.check())).toEqual(sortExpect(testCase.expect));
  });
});

describe("ledger parity", () => {
  it.each(FIXTURES.ledger.map((c) => [c.name, c] as const))("%s", (_name, testCase) => {
    const ledger = new AssumptionLedger(testCase.task);
    if (testCase.trivial) ledger.markTrivial();
    for (const [claim, because] of testCase.assumptions) ledger.assume(claim, because);
    for (const question of testCase.openQuestions) ledger.ask(question);
    expect(signature(ledger.check())).toEqual(sortExpect(testCase.expect));
  });
});

it("every emitted violation carries a namespaced code", () => {
  // An empty code breaks parity silently, which is the failure mode this whole
  // file exists to prevent.
  const verdicts = [
    new AssumptionLedger("x").check(),
    new SuccessCriteria("x").require("make it work").check(),
    complexityBudget("class HandlerBase:\n    pass\n", { maxLines: 0 }),
    diffDiscipline("+++ b/a.py\n@@ -1,2 +1,1 @@\n-# why\n x = 1\n", { allowedPaths: ["src/"] }),
  ];
  for (const verdict of verdicts) {
    for (const violation of verdict.violations) {
      expect(violation.code, `violation without a code: ${violation.message}`).toBeTruthy();
      expect(violation.code).toContain(".");
    }
  }
});
