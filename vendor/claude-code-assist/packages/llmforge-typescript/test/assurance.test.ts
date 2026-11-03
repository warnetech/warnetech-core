/**
 * The four principles, as gates that must actually fail when violated.
 *
 * Every test answers one question: if an agent ignored this principle, would
 * the gate catch it? A gate that only passes is decoration.
 */

import { describe, expect, it } from "vitest";
import {
  AssumptionLedger,
  SuccessCriteria,
  complexityBudget,
  diffDiscipline,
  parseDiff,
  postflight,
  preflight,
} from "../src/index.js";

// --------------------------------------------------------------------------- //
// 1. Think Before Coding
// --------------------------------------------------------------------------- //

describe("AssumptionLedger", () => {
  it("blocks an empty ledger on a non-trivial task", () => {
    const verdict = new AssumptionLedger("rewrite the scheduler").check();
    expect(verdict.passed).toBe(false);
    expect(verdict.blockers[0]?.message).toContain("no assumptions declared");
  });

  it("exempts trivial tasks, as the guidelines intend", () => {
    expect(new AssumptionLedger("fix typo").markTrivial().check().passed).toBe(true);
  });

  it("warns on an unsupported assumption without blocking", () => {
    const verdict = new AssumptionLedger("x").assume("the queue is FIFO").check();
    expect(verdict.passed).toBe(true);
    expect(verdict.violations.some((v) => v.severity === "warn")).toBe(true);
  });

  it("passes cleanly when assumptions cite evidence", () => {
    const verdict = new AssumptionLedger("x")
      .assume("the queue is FIFO", "queue.ts:31 uses shift()")
      .check();
    expect(verdict.violations).toHaveLength(0);
  });

  it("blocks on open questions by default", () => {
    const ledger = new AssumptionLedger("x").assume("a", "b").ask("which tenant?");
    expect(ledger.check().passed).toBe(false);
    expect(ledger.check({ blockingQuestions: false }).passed).toBe(true);
  });

  it("renders unsupported assumptions visibly", () => {
    expect(new AssumptionLedger("x").assume("guessed").render()).toContain("UNSUPPORTED");
  });
});

// --------------------------------------------------------------------------- //
// 2. Simplicity First
// --------------------------------------------------------------------------- //

describe("complexityBudget", () => {
  it("blocks bloat against a declared budget", () => {
    const code = Array.from({ length: 60 }, (_, i) => `const x${i} = ${i};`).join("\n");
    expect(complexityBudget(code, { maxLines: 20 }).passed).toBe(false);
    expect(complexityBudget(code, { maxLines: 100 }).passed).toBe(true);
  });

  it("blocks a rewrite far larger than what it replaces", () => {
    const code = Array.from({ length: 100 }, (_, i) => `const y${i} = ${i};`).join("\n");
    const verdict = complexityBudget(code, { baselineLines: 20 });
    expect(verdict.passed).toBe(false);
    expect(verdict.blockers[0]?.message).toContain("5.0x growth");
  });

  it("does not count comments or blanks against the budget", () => {
    expect(complexityBudget("// a comment\n\n// another\nconst x = 1;\n", { maxLines: 1 }).passed).toBe(
      true,
    );
  });

  it("flags speculative generality", () => {
    const code = "export abstract class AbstractHandlerBase {}\nfunction makeHandlerFactory() {}\n";
    const messages = complexityBudget(code).violations.map((v) => v.message);
    expect(messages.some((m) => m.includes("abstract-base"))).toBe(true);
    expect(messages.some((m) => m.includes("factory"))).toBe(true);
  });

  it("flags a swallowed error", () => {
    const messages = complexityBudget("try { go(); } catch {}\n").violations.map((v) => v.message);
    expect(messages.some((m) => m.includes("swallowed-error"))).toBe(true);
  });

  it("warns on deep nesting", () => {
    const code = Array.from({ length: 7 }, (_, i) => `${"  ".repeat(i)}if (x${i}) {`).join("\n");
    expect(complexityBudget(code).violations.some((v) => v.message.includes("nesting"))).toBe(true);
  });
});

// --------------------------------------------------------------------------- //
// 3. Surgical Changes
// --------------------------------------------------------------------------- //

const DIFF_WITH_DRIVE_BY = `diff --git a/src/upload.ts b/src/upload.ts
--- a/src/upload.ts
+++ b/src/upload.ts
@@ -10,3 +10,4 @@
 function upload(path) {
+  retry(3);
   return put(path);
diff --git a/src/colors.ts b/src/colors.ts
--- a/src/colors.ts
+++ b/src/colors.ts
@@ -1,2 +1,2 @@
-const RED   = "#ff0000";
+const RED = "#ff0000";
`;

describe("diffDiscipline", () => {
  it("attributes hunks to the right files", () => {
    expect(parseDiff(DIFF_WITH_DRIVE_BY).map((h) => h.file)).toEqual([
      "src/upload.ts",
      "src/colors.ts",
    ]);
  });

  it("flags a pure reformatting hunk", () => {
    const verdict = diffDiscipline(DIFF_WITH_DRIVE_BY);
    expect(verdict.violations.some((v) => v.message.includes("pure reformatting"))).toBe(true);
  });

  it("blocks an out-of-scope file", () => {
    const verdict = diffDiscipline(DIFF_WITH_DRIVE_BY, { allowedPaths: ["src/upload.ts"] });
    expect(verdict.passed).toBe(false);
    expect(verdict.blockers[0]?.message).toContain("src/colors.ts");
  });

  it("warns on a file unrelated to the request terms", () => {
    const verdict = diffDiscipline(DIFF_WITH_DRIVE_BY, { requestTerms: ["upload", "retry"] });
    expect(verdict.violations.some((v) => v.message.includes("does not obviously relate"))).toBe(
      true,
    );
  });

  it("blocks a deleted comment", () => {
    // The specific failure the guidelines name: removing what you did not understand.
    const diff = [
      "+++ b/src/jwt.ts",
      "@@ -1,3 +1,2 @@",
      "-// leeway is 60s because the auth server clock drifts; see INC-2231",
      " function verify(token) {",
      "   return true;",
    ].join("\n");
    const verdict = diffDiscipline(diff);
    expect(verdict.passed).toBe(false);
    expect(verdict.blockers[0]?.message).toContain("INC-2231");
  });

  it("does not flag a comment that merely moved", () => {
    const diff = [
      "+++ b/src/jwt.ts",
      "@@ -1,3 +1,3 @@",
      "-// leeway is 60s",
      " function verify(token) {",
      "+// leeway is 60s",
    ].join("\n");
    expect(diffDiscipline(diff).passed).toBe(true);
  });

  it("passes an in-scope change cleanly", () => {
    const diff = [
      "+++ b/src/upload.ts",
      "@@ -10,2 +10,3 @@",
      " function upload(path) {",
      "+  retry(3);",
    ].join("\n");
    expect(
      diffDiscipline(diff, { requestTerms: ["upload"], allowedPaths: ["src/"] }).passed,
    ).toBe(true);
  });
});

// --------------------------------------------------------------------------- //
// 4. Goal-Driven Execution
// --------------------------------------------------------------------------- //

describe("SuccessCriteria", () => {
  it("blocks a run with no criteria", () => {
    expect(new SuccessCriteria("do the thing").check().passed).toBe(false);
  });

  it.each(["make it work", "fix it", "refactor", "improve"])(
    "blocks the vague criterion %s",
    (vague) => {
      expect(new SuccessCriteria("x").require(vague, () => [true, ""]).check().passed).toBe(false);
    },
  );

  it("accepts a verifiable criterion", () => {
    expect(
      new SuccessCriteria("x").require("upload.test.ts passes", () => [true, "ok"]).check().passed,
    ).toBe(true);
  });

  it("warns, but does not block, when a criterion has no verifier", () => {
    const verdict = new SuccessCriteria("x").require("the docs mention retries").check();
    expect(verdict.passed).toBe(true);
    expect(verdict.violations.some((v) => v.severity === "warn")).toBe(true);
  });

  it("reports unmet criteria", async () => {
    const verdict = await new SuccessCriteria("x")
      .require("a passes", () => [true, ""])
      .require("b passes", () => [false, "2 failed"])
      .verify();
    expect(verdict.passed).toBe(false);
    expect(verdict.blockers[0]?.remedy).toBe("2 failed");
  });

  it("treats a throwing verifier as a failed criterion, not a crash", async () => {
    const verdict = await new SuccessCriteria("x")
      .require("tests pass", () => {
        throw new Error("vitest not installed");
      })
      .verify();
    expect(verdict.passed).toBe(false);
    expect(verdict.blockers[0]?.remedy).toContain("vitest not installed");
  });

  it("awaits async verifiers", async () => {
    const verdict = await new SuccessCriteria("x")
      .require("async check", async () => [false, "async failure"])
      .verify();
    expect(verdict.blockers[0]?.remedy).toBe("async failure");
  });

  it("renders the plan in the guidelines' step -> verify form", () => {
    expect(new SuccessCriteria("x").step("add retry", "unit test passes").render()).toContain(
      "add retry -> verify: unit test passes",
    );
  });
});

// --------------------------------------------------------------------------- //
// composition
// --------------------------------------------------------------------------- //

describe("composition", () => {
  it("blocks a preflight with nothing supplied", () => {
    expect(preflight().passed).toBe(false);
  });

  it("combines both preflight gates", () => {
    const verdict = preflight({
      ledger: new AssumptionLedger("x").markTrivial(),
      goals: new SuccessCriteria("x").require("t passes", () => [true, ""]),
    });
    expect(verdict.passed).toBe(true);
  });

  it("combines diff and complexity in postflight", async () => {
    const verdict = await postflight({
      diff: DIFF_WITH_DRIVE_BY,
      code: "const x = 1;\n".repeat(50),
      allowedPaths: ["src/upload.ts"],
      maxLines: 10,
    });
    const messages = verdict.blockers.map((v) => v.message);
    expect(messages.some((m) => m.includes("outside the declared scope"))).toBe(true);
    expect(messages.some((m) => m.includes("exceeds the declared budget"))).toBe(true);
  });

  it("renders a readable report", () => {
    const report = new SuccessCriteria("x").check().report();
    expect(report).toContain("blocking");
    expect(report).toContain("Goal-Driven Execution");
  });
});
