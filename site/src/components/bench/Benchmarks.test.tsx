import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import type { BenchmarkData } from "../../lib/types";
import { BenchmarksView } from "./Benchmarks";

const data = JSON.parse(readFileSync("public/data/benchmark.json", "utf8")) as BenchmarkData;
afterEach(cleanup);

describe("BenchmarksView", () => {
  it("shows the recomputed ratios for every method", () => {
    render(<BenchmarksView data={data} />);
    for (const m of data.methods) {
      expect(screen.getAllByText(new RegExp(`^${m.seeded_detection.numerator}/${m.seeded_detection.denominator}`)).length).toBeGreaterThan(0);
    }
    expect(screen.getByText(/Seeded fixtures, not production discoveries/)).toBeTruthy();
  });

  it("filters raw runs", () => {
    render(<BenchmarksView data={data} />);
    expect(screen.getByText(`Showing ${data.records.length} of ${data.records.length} runs`)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Method", { exact: true }), { target: { value: "D_stateful_shrinking" } });
    fireEvent.change(screen.getByLabelText("Case", { exact: true }), { target: { value: "retry_cancel" } });
    const n = data.records.filter((r) => r.method === "D_stateful_shrinking" && r.case === "retry_cancel").length;
    expect(screen.getByText(`Showing ${n} of ${data.records.length} runs`)).toBeTruthy();
  });
});
