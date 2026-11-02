// Invariants of the published data: every number shown must follow from the raw records.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { BenchmarkData, DemoData } from "./lib/types";

const bench = JSON.parse(readFileSync("public/data/benchmark.json", "utf8")) as BenchmarkData;
const demo = JSON.parse(readFileSync("public/data/demo.json", "utf8")) as DemoData;

describe("benchmark data", () => {
  it("has one record per (case, method, seed)", () => {
    const expected = bench.cases.length * (1 + 3 * bench.meta.seeds);
    expect(bench.records).toHaveLength(expected);
  });
  it("recomputes detection and false-alarm ratios from the records", () => {
    for (const m of bench.methods) {
      const runs = bench.records.filter((r) => r.method === m.id);
      const seeded = runs.filter((r) => r.seeded);
      const clean = runs.filter((r) => !r.seeded);
      expect(m.seeded_detection.denominator).toBe(seeded.length);
      expect(m.seeded_detection.numerator).toBe(seeded.filter((r) => r.detected).length);
      expect(m.false_alarms_on_clean.denominator).toBe(clean.length);
      expect(m.false_alarms_on_clean.numerator).toBe(clean.filter((r) => r.outcome !== "NO_DIFFERENCES_OBSERVED").length);
      for (const c of bench.cases) {
        const cr = runs.filter((r) => r.case === c.name);
        expect(m.per_case_detection[c.name]).toMatchObject({ numerator: cr.filter((r) => r.detected).length, denominator: cr.length });
      }
    }
  });
  it("keeps negative and missed cases", () => {
    expect(bench.cases.some((c) => !c.seeded)).toBe(true);
    expect(bench.records.some((r) => r.seeded && !r.detected)).toBe(true);
  });
  it("respects the search budget", () => {
    for (const r of bench.records.filter((r) => r.method !== "A_unit_tests")) {
      expect(r.search_operations ?? 0).toBeLessThanOrEqual(bench.meta.budget_operations);
    }
  });
  it("references a full source commit", () => {
    expect(bench.meta.source_commit).toMatch(/^[0-9a-f]{40}$/);
  });
});

describe("demo data", () => {
  it("is labelled as precomputed and verified", () => {
    expect(demo.generated_from.precomputed).toBe(true);
    expect(demo.outcome).toBe("DIFFERENCE_CONFIRMED");
    expect(demo.verification.reproduced).toBe(demo.verification.checks);
  });
  it("evidence corresponds to the minimized sequence and the exported regression", () => {
    expect(demo.evidence.map((e) => e.step)).toEqual(demo.minimized_sequence.steps);
    expect((demo.regression_json as { steps: unknown }).steps).toEqual(demo.minimized_sequence.steps);
    expect(demo.evidence.at(-1)?.verdict).toBe("MISMATCH");
    expect(demo.evidence.slice(0, -1).every((e) => e.verdict === "MATCH")).toBe(true);
  });
  it("contains no local machine paths", () => {
    expect(JSON.stringify(demo)).not.toMatch(/\/Users\/|\/home\//);
  });
});
