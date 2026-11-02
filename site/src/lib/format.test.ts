import { describe, expect, it } from "vitest";
import { describeStep, num, percent, ratio, seconds } from "./format";

describe("format", () => {
  it("formats ratios and percentages from numerator/denominator", () => {
    expect(ratio({ numerator: 5, denominator: 30, rate: 0.167 })).toBe("5/30");
    expect(percent({ numerator: 5, denominator: 30, rate: 0.167 })).toBe("17%");
    expect(percent({ numerator: 0, denominator: 0, rate: null })).toBe("n/a");
  });
  it("formats numbers and durations", () => {
    expect(num(null)).toBe("n/a");
    expect(num(11.5)).toBe("11.5");
    expect(num(3)).toBe("3");
    expect(seconds(0.2041)).toBe("204 ms");
    expect(seconds(2.5)).toBe("2.50 s");
  });
  it("describes steps with logical refs", () => {
    expect(describeStep({ op: "cancel_booking", params: { id: { $ref: "booking#1" } }, query: {} })).toBe(
      "cancel_booking(id=booking#1)",
    );
    expect(describeStep({ op: "create_booking", body: { slot: "09:00" } })).toBe('create_booking(slot="09:00")');
  });
});
