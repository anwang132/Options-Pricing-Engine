import { describe, expect, it } from "vitest";
import { isDecimalText, percentToDecimal, sig } from "./decimal";

describe("percentToDecimal", () => {
  it.each([
    ["5", "0.05"],
    ["5.25", "0.0525"],
    ["10", "0.10"],
    ["0.5", "0.005"],
    ["-0.5", "-0.005"],
    ["100", "1.00"],
    ["123.4", "1.234"],
    ["0", "0.00"],
    [".5", "0.005"],
  ])("%s%% -> %s", (input, expected) => {
    expect(percentToDecimal(input)).toBe(expected);
    expect(Number(percentToDecimal(input))).toBeCloseTo(Number(input) / 100, 15);
  });
  it("rejects non-numbers", () => {
    expect(() => percentToDecimal("abc")).toThrow();
    expect(isDecimalText("1e5")).toBe(false);
  });
});

describe("sig", () => {
  it("formats for display only", () => {
    expect(sig(4.759422392871535, 6)).toBe("4.75942");
    expect(sig(null)).toBe("—");
    expect(sig(1.23e-9, 3)).toBe("1.23e-9");
  });
});
