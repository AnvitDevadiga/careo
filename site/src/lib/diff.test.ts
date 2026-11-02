import { describe, expect, it } from "vitest";
import { alignRows, groupDifferences, tablesOf } from "./diff";

describe("diff helpers", () => {
  it("aligns rows by position and flags extra rows", () => {
    const state = { reference: { t: [{ a: 1 }] }, candidate: { t: [{ a: 1 }, { a: 2 }] } };
    const rows = alignRows(state, "t");
    expect(rows.map((r) => r.differs)).toEqual([false, true]);
    expect(rows[1].reference).toBeUndefined();
    expect(tablesOf(state)).toEqual(["t"]);
  });
  it("groups differences by area", () => {
    const g = groupDifferences([
      { path: "<status>", kind: "status_changed" },
      { path: "<body>/id", kind: "value_changed" },
      { path: "<db>/events/3", kind: "extra_in_candidate" },
    ]);
    expect(Object.keys(g).sort()).toEqual(["body", "db", "status"]);
  });
});
