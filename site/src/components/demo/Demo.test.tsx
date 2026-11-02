import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import type { DemoData } from "../../lib/types";
import { DemoView } from "./Demo";

const demo = JSON.parse(readFileSync("public/data/demo.json", "utf8")) as DemoData;
afterEach(cleanup);

describe("DemoView", () => {
  it("steps through the recorded evidence", () => {
    render(<DemoView demo={demo} />);
    expect(screen.getByText("Step 1 of 3")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Previous" })).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByRole("button", { name: "Next step" }));
    fireEvent.click(screen.getByRole("button", { name: "Next step" }));
    expect(screen.getByText("Step 3 of 3")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Next step" })).toHaveProperty("disabled", true);
    const table = screen.getByRole("table", { name: /Differences reported at this step/ });
    expect(within(table).getByText("status_changed")).toBeTruthy();
    expect(screen.getAllByText(/^HTTP/).length).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "Restart" }));
    expect(screen.getByText("Step 1 of 3")).toBeTruthy();
  });

  it("selects a step from the list and marks it current", () => {
    render(<DemoView demo={demo} />);
    const steps = screen.getAllByRole("button", { name: /cancel_booking/ });
    fireEvent.click(steps[1]);
    expect(steps[1].getAttribute("aria-current")).toBe("step");
    expect(screen.getByText("Step 3 of 3")).toBeTruthy();
  });

  it("reveals the original sequence and switches tabs with the keyboard", () => {
    render(<DemoView demo={demo} />);
    const toggle = screen.getByRole("button", { name: /original 13-operation sequence/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const tabs = screen.getAllByRole("tablist")[1];
    const first = within(tabs).getAllByRole("tab")[0];
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowRight" });
    expect(within(tabs).getAllByRole("tab")[1].getAttribute("aria-selected")).toBe("true");
  });
});
