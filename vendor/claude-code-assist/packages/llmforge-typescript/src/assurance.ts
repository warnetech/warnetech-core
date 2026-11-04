/**
 * Assurance: the four coding principles, compiled into gates that can fail.
 *
 * The Karpathy guidelines (see THIRD_PARTY_NOTICES.md) are four rules that
 * demonstrably reduce LLM coding mistakes:
 *
 *   1. Think Before Coding   -- don't assume; surface confusion and tradeoffs
 *   2. Simplicity First      -- minimum code that solves the problem
 *   3. Surgical Changes      -- touch only what the request requires
 *   4. Goal-Driven Execution -- define success criteria; loop until verified
 *
 * They ship as prose, and prose works the way all prompt guidance works: most
 * of the time, and silently not the rest of the time. You cannot tell from a
 * diff whether the agent followed rule 3 or merely read it.
 *
 * Where a mistake is cheap that trade is fine. Where it is expensive to
 * reverse, advice that cannot fail is not a control. So each principle here is
 * a check with a verdict:
 *
 *   Think Before Coding    -> AssumptionLedger
 *   Simplicity First       -> complexityBudget
 *   Surgical Changes       -> diffDiscipline
 *   Goal-Driven Execution  -> SuccessCriteria
 *
 * None of them need a model. They are static analysis over text you already
 * have: fast, free, deterministic, and incapable of hallucinating -- the
 * properties you want in the thing that says "no".
 */

export type Severity = "blocker" | "warn" | "note";

export interface Violation {
  principle: string;
  severity: Severity;
  message: string;
  /**
   * The stable, machine-readable identity of the finding. Prose messages get
   * reworded; codes do not, which is what lets `fixtures/assurance-parity.json`
   * pin the Python and TypeScript gates to the same behaviour.
   */
  code: string;
  location?: string;
  remedy?: string;
}

export class Verdict {
  readonly violations: Violation[];
  readonly checked: string[];

  constructor(violations: Violation[] = [], checked: string[] = []) {
    this.violations = violations;
    this.checked = checked;
  }

  get blockers(): Violation[] {
    return this.violations.filter((v) => v.severity === "blocker");
  }

  get passed(): boolean {
    return this.blockers.length === 0;
  }

  concat(other: Verdict): Verdict {
    return new Verdict(
      [...this.violations, ...other.violations],
      [...this.checked, ...other.checked],
    );
  }

  /** The fail-safe call. Use it where proceeding is worse than stopping. */
  throwIfBlocked(): void {
    if (this.blockers.length) throw new AssuranceError(this);
  }

  report(): string {
    if (!this.violations.length) return `assurance: pass (${this.checked.length} checks)`;
    const advisory = this.violations.length - this.blockers.length;
    const head = `assurance: ${this.blockers.length} blocking, ${advisory} advisory (${this.checked.length} checks)`;
    const lines = this.violations.map((v) => {
      const where = v.location ? ` (${v.location})` : "";
      const line = `[${v.severity.padStart(7)}] ${v.principle}${where}: ${v.message}`;
      return v.remedy ? `${line}\n          fix: ${v.remedy}` : line;
    });
    return [head, ...lines].join("\n");
  }
}

export class AssuranceError extends Error {
  readonly verdict: Verdict;
  constructor(verdict: Verdict) {
    super(verdict.report());
    this.name = "AssuranceError";
    this.verdict = verdict;
  }
}

// --------------------------------------------------------------------------- //
// 1. Think Before Coding
// --------------------------------------------------------------------------- //

/**
 * What the agent believes but was not told. Principle 1, made auditable.
 *
 * The ledger exists so a wrong assumption is visible *before* it becomes a
 * wrong implementation. An empty ledger on a non-trivial task is itself the
 * finding: no real request is fully specified, so an agent that declared no
 * assumptions did not look for them.
 */
export class AssumptionLedger {
  readonly assumptions: { claim: string; because: string }[] = [];
  readonly openQuestions: string[] = [];
  readonly rejectedAlternatives: { option: string; why: string }[] = [];
  /** Set for genuine one-liners. The guidelines exempt them, and so does this. */
  trivial = false;

  constructor(readonly task = "") {}

  assume(claim: string, because = ""): this {
    this.assumptions.push({ claim, because });
    return this;
  }

  ask(question: string): this {
    this.openQuestions.push(question);
    return this;
  }

  rejected(option: string, why: string): this {
    this.rejectedAlternatives.push({ option, why });
    return this;
  }

  markTrivial(): this {
    this.trivial = true;
    return this;
  }

  check({ blockingQuestions = true } = {}): Verdict {
    const violations: Violation[] = [];
    if (this.trivial) return new Verdict([], ["think-before-coding"]);

    if (!this.assumptions.length) {
      violations.push({
        principle: "Think Before Coding",
        severity: "blocker",
        message: "no assumptions declared for a non-trivial task",
        code: "think.no-assumptions",
        remedy:
          "state what you inferred that the request did not say, or mark the task trivial if it genuinely is",
      });
    }
    for (const { claim, because } of this.assumptions) {
      if (!because) {
        violations.push({
          principle: "Think Before Coding",
          severity: "warn",
          message: `assumption has no evidence: ${JSON.stringify(claim)}`,
        code: "think.unsupported-assumption",
          remedy: "cite what in the codebase or request supports it, or move it to open questions",
        });
      }
    }
    if (blockingQuestions && this.openQuestions.length) {
      violations.push({
        principle: "Think Before Coding",
        severity: "blocker",
        message: `${this.openQuestions.length} unanswered question(s): ${this.openQuestions.slice(0, 3).join("; ")}`,
        code: "think.open-questions",
        remedy: "answer them, or proceed explicitly under a stated assumption",
      });
    }
    return new Verdict(violations, ["think-before-coding"]);
  }

  render(): string {
    const lines = [this.task ? `## Assumptions for: ${this.task}` : "## Assumptions"];
    lines.push(
      ...(this.assumptions.length
        ? this.assumptions.map(
            ({ claim, because }) => `- ${claim}${because ? ` (because ${because})` : " (UNSUPPORTED)"}`,
          )
        : ["- (none declared)"]),
    );
    if (this.rejectedAlternatives.length) {
      lines.push("", "## Considered and rejected");
      lines.push(...this.rejectedAlternatives.map(({ option, why }) => `- ${option}: ${why}`));
    }
    if (this.openQuestions.length) {
      lines.push("", "## Open questions");
      lines.push(...this.openQuestions.map((q) => `- ${q}`));
    }
    return lines.join("\n");
  }
}

// --------------------------------------------------------------------------- //
// 2. Simplicity First
// --------------------------------------------------------------------------- //

// Shapes that appear when a model builds for imagined future requirements.
//
// Removed: `unused-config-knob`, which claimed to flag "a configuration point
// added without a second caller". Whether a config point has a second caller is
// a cross-file fact, and a single-file regex cannot see callers -- so it never
// could do what it claimed. In practice it fired on every `model:` and
// `options:` in the repository (`model` contains `mode`; `options` contains
// `option`), which is the cry-wolf failure mode: a check with a bad
// false-positive rate gets ignored, and an ignored check is worse than no check
// because it still occupies the slot. Detecting this properly needs call-graph
// analysis, not a pattern table.
//
// Every pattern here matches BOTH Python and TypeScript/JavaScript syntax. A
// TypeScript harness routinely reviews Python diffs and vice versa; a gate that
// only recognises its own host language silently passes everything else, which
// is worse than not running -- it reports "clean" on code it never understood.
//
// Kept in lockstep with SPECULATIVE_PATTERNS in the Python package. The parity
// fixtures fail if the two drift.
const SPECULATIVE_PATTERNS: [string, RegExp, string][] = [
  [
    "abstract-base",
    /^\s*(?:export\s+)?(?:abstract\s+)?class\s+\w*(?:Base|Abstract|Generic)\w*/m,
    "an abstraction introduced for a single implementation; write the concrete version and extract later if a second one arrives",
  ],
  [
    // Case-insensitive: `HandlerFactory` and `make_handler_factory` are the
    // same smell. `Provider` is deliberately absent -- it is legitimate domain
    // vocabulary often enough that flagging it trains people to ignore this
    // whole category.
    "factory",
    /^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|const|let)\s+\w*(?:Factory|Builder|Manager)\w*/im,
    "an indirection layer; if there is exactly one thing being built, construct it directly",
  ],
  [
    "swallowed-error",
    /except\s+\w*(?:Exception|Error)?\s*(?:as\s+\w+\s*)?:\s*\n\s*pass|catch\s*(?:\([^)]*\))?\s*\{\s*\}/m,
    "error handling for a scenario that is either impossible (delete it) or possible (handle it properly)",
  ],
  [
    "todo-scaffold",
    /(?:#|\/\/)\s*(?:TODO|FIXME|for future use|not implemented yet)/i,
    "scaffolding for work that was not requested",
  ],
];

export interface ComplexityOptions {
  maxLines?: number;
  maxDefinitions?: number;
  maxNesting?: number;
  /** Lines the change replaces. The strongest available signal of bloat. */
  baselineLines?: number;
  ratio?: number;
}

/**
 * Measure what a change added and flag speculative generality.
 *
 * Nothing can decide "could be 50 lines" mechanically -- but the signals that
 * correlate with it are measurable: size against a declared budget, size
 * against the code being replaced, nesting depth, and the specific shapes that
 * appear when a model builds for imagined future requirements.
 */
export function complexityBudget(code: string, options: ComplexityOptions = {}): Verdict {
  const { maxLines, maxDefinitions, maxNesting = 4, baselineLines, ratio = 4 } = options;
  const violations: Violation[] = [];

  // Both comment styles, in both languages. Counting `#` as code in TypeScript
  // (or `//` in Python) makes the same file measure differently depending on
  // which implementation ran -- pinned by fixtures/assurance-parity.json.
  const lines = code
    .split("\n")
    .filter((l) => l.trim() && !l.trim().startsWith("//") && !l.trim().startsWith("#"));
  const added = lines.length;

  if (maxLines !== undefined && added > maxLines) {
    violations.push({
      principle: "Simplicity First",
      severity: "blocker",
      message: `${added} lines exceeds the declared budget of ${maxLines}`,
        code: "simplicity.line-budget",
      remedy:
        "cut to the minimum that solves the stated problem, or raise the budget deliberately and say why",
    });
  }

  if (baselineLines && added > baselineLines * ratio) {
    violations.push({
      principle: "Simplicity First",
      severity: "blocker",
      message: `${added} lines replaces ${baselineLines} (${(added / baselineLines).toFixed(1)}x growth)`,
        code: "simplicity.growth-ratio",
      remedy:
        "a rewrite this much larger is usually solving problems nobody asked about; identify what is not required",
    });
  }

  const definitions = (code.match(/^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+\w+/gm) ?? [])
    .length;
  if (maxDefinitions !== undefined && definitions > maxDefinitions) {
    violations.push({
      principle: "Simplicity First",
      severity: "warn",
      message: `${definitions} definitions exceeds ${maxDefinitions}`,
        code: "simplicity.definition-count",
      remedy: "collapse single-use helpers into their caller",
    });
  }

  const nesting = maxIndentDepth(code);
  if (nesting > maxNesting) {
    violations.push({
      principle: "Simplicity First",
      severity: "warn",
      message: `nesting depth ${nesting} exceeds ${maxNesting}`,
        code: "simplicity.nesting-depth",
      remedy: "invert conditions and return early",
    });
  }

  for (const [name, pattern, remedy] of SPECULATIVE_PATTERNS) {
    const match = pattern.exec(code);
    if (match) {
      violations.push({
        principle: "Simplicity First",
        severity: "note",
        message: `possible speculative generality (${name}): ${JSON.stringify(match[0].trim().slice(0, 60))}`,
        code: "simplicity.speculative",
        remedy,
      });
    }
  }

  return new Verdict(violations, ["simplicity-first"]);
}

// A line that opens a block, in either language: a Python compound statement
// ending in `:`, or a C-family line ending in `{`.
const BLOCK_OPENER =
  /^(?:async\s+)?(?:if|elif|else|for|while|with|try|except|finally|def|class|match|case)\b.*:\s*(?:#.*)?$|\{\s*(?:\/\/.*)?$/;

/**
 * Nesting depth, measured on lines that actually open a block.
 *
 * Raw indentation is a bad proxy. A wrapped call argument or a multi-line
 * object literal is indented two, four, sixteen spaces without nesting
 * anything -- measuring indentation alone reported depth 8 for a file whose
 * real nesting is 3, a false positive that trains people to ignore the check.
 *
 * Counting only block openers measures the thing the check is named after. The
 * indent unit is inferred from those lines rather than assumed, so a file does
 * not measure differently depending on which language's implementation ran --
 * pinned by fixtures/assurance-parity.json.
 */
function maxIndentDepth(code: string, tabWidth = 4): number {
  const openers: number[] = [];
  for (const line of code.split("\n")) {
    const stripped = line.trim();
    if (!stripped || !BLOCK_OPENER.test(stripped)) continue;
    const leading = line.length - line.trimStart().length;
    openers.push(line.slice(0, leading).replace(/\t/g, " ".repeat(tabWidth)).length);
  }

  const indents = openers.filter((width) => width > 0);
  if (!indents.length) return 0;
  const unit = Math.min(...indents);
  return Math.max(...openers.map((width) => Math.floor(width / unit)));
}

// --------------------------------------------------------------------------- //
// 3. Surgical Changes
// --------------------------------------------------------------------------- //

export interface Hunk {
  file: string;
  header: string;
  added: string[];
  removed: string[];
}

const HUNK_RE = /^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@/;
const FILE_RE = /^\+\+\+ b\/(.+)$/;
const COMMENT_RE = /^\s*(?:\/\/|#|\*|\/\*|<!--|--)\s*\S/;

/** Parse a unified diff into hunks. Tolerant of git's extra headers. */
export function parseDiff(diff: string): Hunk[] {
  const hunks: Hunk[] = [];
  let current: Hunk | undefined;
  let filename = "?";

  for (const line of diff.split("\n")) {
    const fileMatch = FILE_RE.exec(line);
    if (fileMatch) {
      filename = fileMatch[1] as string;
      continue;
    }
    if (HUNK_RE.test(line)) {
      current = { file: filename, header: line, added: [], removed: [] };
      hunks.push(current);
      continue;
    }
    if (!current) continue;
    if (line.startsWith("+") && !line.startsWith("+++")) current.added.push(line.slice(1));
    else if (line.startsWith("-") && !line.startsWith("---")) current.removed.push(line.slice(1));
  }
  return hunks;
}

const normalizeLines = (lines: string[]) => lines.map((l) => l.replace(/\s+/g, " ").trim());

export function isPureFormatting(hunk: Hunk): boolean {
  if (!hunk.added.length || !hunk.removed.length) return false;
  const a = normalizeLines(hunk.added);
  const b = normalizeLines(hunk.removed);
  return a.length === b.length && a.every((line, i) => line === b[i]);
}

export function removedComments(hunk: Hunk): string[] {
  const kept = new Set(normalizeLines(hunk.added));
  return hunk.removed.filter(
    (line) => COMMENT_RE.test(line) && !kept.has(line.replace(/\s+/g, " ").trim()),
  );
}

export interface DiffOptions {
  /** Nouns from the actual request. What makes traceability checkable at all. */
  requestTerms?: string[];
  allowedPaths?: string[];
  allowFormatting?: boolean;
}

/**
 * Check that every hunk traces to the request. Principle 3, enforced.
 *
 * The gate with the most teeth, because "touch only what you must" is the
 * principle an agent violates most often and a reviewer notices least -- a
 * drive-by rename buried in a 400-line diff reads as noise, gets skimmed, and
 * lands.
 */
export function diffDiscipline(diff: string, options: DiffOptions = {}): Verdict {
  const { requestTerms = [], allowedPaths, allowFormatting = false } = options;
  const violations: Violation[] = [];
  const terms = new Set(requestTerms.map((t) => t.toLowerCase()));
  const hunks = parseDiff(diff);
  const touched = [...new Set(hunks.map((h) => h.file))].sort();

  for (const path of touched) {
    if (allowedPaths && !allowedPaths.some((p) => path.startsWith(p))) {
      violations.push({
        principle: "Surgical Changes",
        severity: "blocker",
        message: `edit outside the declared scope: ${path}`,
        code: "surgical.out-of-scope",
        location: path,
        remedy: `the request authorized ${JSON.stringify(allowedPaths)}; raise the scope explicitly or revert this file`,
      });
    } else if (terms.size && ![...pathTerms(path)].some((t) => terms.has(t))) {
      violations.push({
        principle: "Surgical Changes",
        severity: "warn",
        message: `${path} does not obviously relate to the request`,
        code: "surgical.unrelated-file",
        location: path,
        remedy: "state which part of the request required this file, or drop it from the change",
      });
    }
  }

  for (const hunk of hunks) {
    if (isPureFormatting(hunk) && !allowFormatting) {
      violations.push({
        principle: "Surgical Changes",
        severity: "warn",
        message: "hunk is pure reformatting",
        code: "surgical.pure-formatting",
        location: `${hunk.file} ${hunk.header.trim()}`,
        remedy: "revert it; formatting churn costs review attention and hides the real change",
      });
    }
    for (const comment of removedComments(hunk)) {
      violations.push({
        principle: "Surgical Changes",
        severity: "blocker",
        message: `comment removed without replacement: ${JSON.stringify(comment.trim().slice(0, 70))}`,
        code: "surgical.comment-deleted",
        location: hunk.file,
        remedy:
          "restore it, or say what you learned that makes it wrong; a comment is often the only record of why the code is like this",
      });
    }
  }

  return new Verdict(violations, ["surgical-changes"]);
}

function pathTerms(path: string): Set<string> {
  return new Set(
    path
      .split(/[/_.\-]/)
      .filter((p) => p.length > 2)
      .map((p) => p.toLowerCase()),
  );
}

// --------------------------------------------------------------------------- //
// 4. Goal-Driven Execution
// --------------------------------------------------------------------------- //

export type Verifier = () => Promise<[boolean, string]> | [boolean, string];

export interface Criterion {
  description: string;
  verify?: Verifier;
}

/** Phrasings that describe an activity rather than an outcome. */
const VAGUE_CRITERIA =
  /^\s*(?:make it work|fix it|improve|clean up|handle errors|add tests|refactor|optimi[sz]e|better|properly|correctly)\s*\.?\s*$/i;

/**
 * The goal of a run, stated so a machine can decide whether it was met.
 *
 * Its real job is refusal. A loop launched without criteria cannot know when to
 * stop, so it stops when it runs out of budget or when it feels finished.
 * Neither is a result.
 */
export class SuccessCriteria {
  readonly criteria: Criterion[] = [];
  readonly plan: string[] = [];

  constructor(readonly task = "") {}

  require(description: string, verify?: Verifier): this {
    this.criteria.push(verify ? { description, verify } : { description });
    return this;
  }

  /** Add a plan step in the guidelines' `[step] -> verify: [check]` form. */
  step(action: string, verify: string): this {
    this.plan.push(`${action} -> verify: ${verify}`);
    return this;
  }

  /** Pre-flight: are these criteria good enough to launch a loop on? */
  check({ requireVerifiers = true } = {}): Verdict {
    const violations: Violation[] = [];
    if (!this.criteria.length) {
      violations.push({
        principle: "Goal-Driven Execution",
        severity: "blocker",
        message: "no success criteria defined",
        code: "goal.no-criteria",
        remedy:
          "state what must be true when this is done, as something that can be checked -- e.g. 'test_x passes', not 'it works'",
      });
    }
    for (const criterion of this.criteria) {
      if (VAGUE_CRITERIA.test(criterion.description)) {
        violations.push({
          principle: "Goal-Driven Execution",
          severity: "blocker",
          message: `criterion is not verifiable: ${JSON.stringify(criterion.description)}`,
        code: "goal.vague-criterion",
          remedy:
            "restate it as an observable outcome: 'fix the bug' -> 'the test reproducing it passes'",
        });
      }
      if (requireVerifiers && !criterion.verify) {
        violations.push({
          principle: "Goal-Driven Execution",
          severity: "warn",
          message: `criterion has no automated verifier: ${JSON.stringify(criterion.description)}`,
        code: "goal.no-verifier",
          remedy:
            "attach a callable so the loop can decide for itself; without one a human must adjudicate every iteration",
        });
      }
    }
    return new Verdict(violations, ["goal-driven-execution"]);
  }

  /** Post-flight: run every verifier and report what did not hold. */
  async verify(): Promise<Verdict> {
    const violations: Violation[] = [];
    for (const criterion of this.criteria) {
      let ok = false;
      let detail = "no verifier attached";
      if (criterion.verify) {
        try {
          [ok, detail] = await criterion.verify();
        } catch (error) {
          const name = error instanceof Error ? error.name : "Error";
          const message = error instanceof Error ? error.message : String(error);
          [ok, detail] = [false, `verifier threw ${name}: ${message}`];
        }
      }
      if (!ok) {
        violations.push({
          principle: "Goal-Driven Execution",
          severity: "blocker",
          message: `unmet: ${criterion.description}`,
        code: "goal.unmet",
          remedy: detail,
        });
      }
    }
    return new Verdict(
      violations,
      this.criteria.map((c) => `criterion:${c.description}`),
    );
  }

  render(): string {
    const lines = [this.task ? `## Goal: ${this.task}` : "## Goal", "", "Done when:"];
    lines.push(
      ...(this.criteria.length
        ? this.criteria.map((c, i) => `${i + 1}. ${c.description}`)
        : ["  (none)"]),
    );
    if (this.plan.length) {
      lines.push("", "Plan:");
      lines.push(...this.plan.map((s, i) => `${i + 1}. ${s}`));
    }
    return lines.join("\n");
  }
}

// --------------------------------------------------------------------------- //
// Composition
// --------------------------------------------------------------------------- //

/**
 * Everything checked before code is generated.
 *
 * The cheapest possible moment: an assumption caught here costs one
 * conversation turn, the same assumption caught in review costs a day, and in
 * production it costs whatever it costs.
 */
export function preflight(
  options: { ledger?: AssumptionLedger; goals?: SuccessCriteria } = {},
): Verdict {
  let verdict = new Verdict();
  if (options.ledger) verdict = verdict.concat(options.ledger.check());
  if (options.goals) verdict = verdict.concat(options.goals.check());
  if (!options.ledger && !options.goals) {
    verdict = verdict.concat(
      new Verdict([
        {
          principle: "Assurance",
          severity: "blocker",
          message: "preflight ran with neither an assumption ledger nor success criteria",
        code: "assurance.no-preflight-input",
          remedy:
            "an unbounded task with no stated goal cannot be verified; supply at least one",
        },
      ]),
    );
  }
  return verdict;
}

export interface PostflightOptions {
  diff?: string;
  code?: string;
  requestTerms?: string[];
  allowedPaths?: string[];
  goals?: SuccessCriteria;
  maxLines?: number;
  baselineLines?: number;
}

/** Everything checked after a change exists, before it is accepted. */
export async function postflight(options: PostflightOptions = {}): Promise<Verdict> {
  let verdict = new Verdict();
  if (options.diff) {
    verdict = verdict.concat(
      diffDiscipline(options.diff, {
        ...(options.requestTerms ? { requestTerms: options.requestTerms } : {}),
        ...(options.allowedPaths ? { allowedPaths: options.allowedPaths } : {}),
      }),
    );
  }
  if (options.code) {
    verdict = verdict.concat(
      complexityBudget(options.code, {
        ...(options.maxLines !== undefined ? { maxLines: options.maxLines } : {}),
        ...(options.baselineLines !== undefined ? { baselineLines: options.baselineLines } : {}),
      }),
    );
  }
  if (options.goals) verdict = verdict.concat(await options.goals.verify());
  return verdict;
}
