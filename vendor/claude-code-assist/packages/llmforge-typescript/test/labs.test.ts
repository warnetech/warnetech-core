import { describe, expect, it } from "vitest";
import { FakeProvider, response } from "../src/index.js";
import { bestOfN, escalate, savingsVs, selfConsistency } from "../src/labs/index.js";

describe("ensemble", () => {
  it("bestOfN picks the highest scorer", async () => {
    const provider = new FakeProvider(["aa", "aaaa", "a"]);
    const result = await bestOfN(provider, "go", (t) => t.length, { k: 3, parallel: false });
    expect(result.winner).toBe("aaaa");
    expect(result.candidates).toHaveLength(3);
  });

  it("selfConsistency takes the plurality and reports agreement", async () => {
    const result = await selfConsistency(new FakeProvider(["yes", "yes", "no"]), "?", {
      k: 3,
      parallel: false,
    });
    expect(result.winner).toBe("yes");
    expect(result.agreement).toBeCloseTo(2 / 3);
    expect(result.unanimous).toBe(false);
    expect(result.contested).toBe(false);
  });

  it("flags a contested question", async () => {
    const result = await selfConsistency(new FakeProvider(["a", "b", "c", "d"]), "?", {
      k: 4,
      parallel: false,
    });
    expect(result.contested).toBe(true);
  });

  it("clusters formatting variants", async () => {
    const result = await selfConsistency(new FakeProvider(["Yes.", "  yes ", "no"]), "?", {
      k: 3,
      parallel: false,
    });
    expect(result.agreement).toBeCloseTo(2 / 3);
  });

  it("applies vary overrides to decorrelate samples", async () => {
    const provider = new FakeProvider(["a", "b"]);
    await bestOfN(provider, "go", (t) => t.length, {
      k: 2,
      parallel: false,
      vary: [{ effort: "medium" }, { effort: "high" }],
    });
    expect(provider.requests.map((r) => r.effort)).toEqual(["medium", "high"]);
  });
});

describe("ladder", () => {
  it("stops at the first rung that verifies", async () => {
    const result = await escalate(new FakeProvider(["GOOD"]), "t", (t) => [t === "GOOD", ""]);
    expect(result.accepted).toBe(true);
    expect(result.rungsUsed).toBe(1);
    expect(result.escalated).toBe(false);
  });

  it("escalates until accepted", async () => {
    const result = await escalate(new FakeProvider(["bad", "bad", "GOOD"]), "t", (t) => [
      t === "GOOD",
      "want GOOD",
    ]);
    expect(result.accepted).toBe(true);
    expect(result.rungsUsed).toBe(3);
    expect(result.settledOn).toBe("claude-opus-5/high");
  });

  it("reports failure rather than returning a rejected answer", async () => {
    const result = await escalate(new FakeProvider(["bad"]), "t", () => [false, "never"]);
    expect(result.accepted).toBe(false);
    expect(result.rungsUsed).toBe(3);
  });

  it("requireTopRung keeps climbing after an early pass", async () => {
    const result = await escalate(new FakeProvider(["GOOD"]), "t", (t) => [t === "GOOD", ""], {
      requireTopRung: true,
    });
    expect(result.accepted).toBe(true);
    expect(result.rungsUsed).toBe(3);
  });

  it("reports negative savings when the ladder escalates", async () => {
    const provider = new FakeProvider([
      response({
        text: "bad",
        usage: {
          inputTokens: 0,
          outputTokens: 1000,
          cacheCreationInputTokens: 0,
          cacheReadInputTokens: 0,
        },
      }),
    ]);
    const result = await escalate(provider, "t", () => [false, ""]);
    expect(savingsVs(result, 0)).toBeLessThan(0);
  });

  it("awaits an async verifier", async () => {
    const result = await escalate(new FakeProvider(["GOOD"]), "t", async (t) => [
      t === "GOOD",
      "",
    ]);
    expect(result.accepted).toBe(true);
  });
});
