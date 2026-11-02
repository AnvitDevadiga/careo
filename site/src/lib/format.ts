import type { Ratio } from "./types";

export function ratio(r: Ratio): string {
  return `${r.numerator}/${r.denominator}`;
}

export function percent(r: Ratio): string {
  if (!r.denominator) return "n/a";
  return `${Math.round((r.numerator / r.denominator) * 100)}%`;
}

export function num(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "n/a";
  return Number.isInteger(v) ? String(v) : v.toFixed(digits);
}

export function seconds(v: number | null | undefined): string {
  if (v === null || v === undefined) return "n/a";
  return v < 1 ? `${Math.round(v * 1000)} ms` : `${v.toFixed(2)} s`;
}

export function json(v: unknown): string {
  return v === undefined ? "(absent)" : JSON.stringify(v);
}

/** One-line human description of a scenario step, e.g. `cancel_booking(id=booking#1)`. */
export function describeStep(step: { op: string; params?: Record<string, unknown>; query?: Record<string, unknown>; body?: unknown }): string {
  const args: string[] = [];
  const show = (v: unknown): string =>
    v && typeof v === "object" && "$ref" in (v as Record<string, unknown>)
      ? String((v as Record<string, unknown>)["$ref"])
      : JSON.stringify(v);
  for (const [k, v] of Object.entries(step.params ?? {})) args.push(`${k}=${show(v)}`);
  for (const [k, v] of Object.entries(step.query ?? {})) args.push(`${k}=${show(v)}`);
  if (step.body && typeof step.body === "object") {
    for (const [k, v] of Object.entries(step.body as Record<string, unknown>)) args.push(`${k}=${show(v)}`);
  }
  return `${step.op}(${args.join(", ")})`;
}
