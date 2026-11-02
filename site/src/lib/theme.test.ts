import { beforeEach, describe, expect, it } from "vitest";
import { applyTheme, currentTheme, storedTheme } from "./theme";

describe("theme", () => {
  beforeEach(() => {
    localStorage.clear();
    delete document.documentElement.dataset.theme;
  });
  it("defaults to light without a preference", () => {
    expect(storedTheme()).toBeNull();
    expect(currentTheme()).toBe("light");
  });
  it("persists an explicit choice", () => {
    applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(storedTheme()).toBe("dark");
    expect(currentTheme()).toBe("dark");
  });
  it("ignores garbage in storage", () => {
    localStorage.setItem("careo-theme", "neon");
    expect(storedTheme()).toBeNull();
  });
});
