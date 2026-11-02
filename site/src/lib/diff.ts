import type { Difference, Rows } from "./types";

/** Index of differences by top-level area: "status", "body", "db". */
export function groupDifferences(diffs: Difference[]): Record<string, Difference[]> {
  const out: Record<string, Difference[]> = {};
  for (const d of diffs) {
    const area = d.path.startsWith("<db>") ? "db" : d.path.startsWith("<body>") ? "body" : "status";
    (out[area] ??= []).push(d);
  }
  return out;
}

export interface RowPair {
  index: number;
  reference?: Record<string, unknown>;
  candidate?: Record<string, unknown>;
  differs: boolean;
}

/** Align the two sides' rows of one table by position (Careo compares tables in order). */
export function alignRows(state: { reference: Rows; candidate: Rows }, table: string): RowPair[] {
  const a = state.reference[table] ?? [];
  const b = state.candidate[table] ?? [];
  const out: RowPair[] = [];
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    out.push({ index: i, reference: a[i], candidate: b[i], differs: JSON.stringify(a[i]) !== JSON.stringify(b[i]) });
  }
  return out;
}

export function tablesOf(state: { reference: Rows; candidate: Rows }): string[] {
  return Array.from(new Set([...Object.keys(state.reference), ...Object.keys(state.candidate)]));
}
