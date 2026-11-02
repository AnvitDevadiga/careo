import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const WIDTHS = [320, 390, 768, 1024, 1440];

function trackErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("requestfailed", (r) => errors.push(`request failed: ${r.url()}`));
  page.on("response", (r) => r.status() >= 400 && errors.push(`HTTP ${r.status()}: ${r.url()}`));
  return errors;
}

async function ready(page: Page) {
  await page.goto("./");
  await expect(page.locator(".step-list")).toBeVisible();
  await expect(page.locator("table.matrix")).toBeVisible();
}

for (const width of WIDTHS) {
  test(`layout at ${width}px: no overflow, no errors, all sections`, async ({ page }) => {
    const errors = trackErrors(page);
    await page.setViewportSize({ width, height: 900 });
    await ready(page);
    for (const id of ["how", "demo", "benchmarks", "quick-start", "architecture", "limitations"]) {
      await expect(page.locator(`#${id}`)).toBeAttached();
    }
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    expect(errors).toEqual([]);
  });
}

test("200% zoom (1280px at 2x = 640 CSS px) has no horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 640, height: 720 });
  await ready(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
});

for (const scheme of ["light", "dark"] as const) {
  test(`axe: no serious or critical violations (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await ready(page);
    await page.getByRole("button", { name: "Next step" }).click();
    await page.getByRole("button", { name: "Next step" }).click();
    const results = await new AxeBuilder({ page }).analyze();
    const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id}: ${v.nodes.length} (${v.nodes[0]?.target})`)).toEqual([]);
  });
}

test("theme: follows OS by default, toggles, and persists across reloads", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await ready(page);
  const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(await bg()).toBe("rgb(11, 11, 11)");
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  expect(await bg()).toBe("rgb(255, 255, 255)");
  await page.reload();
  await expect(page.locator(".step-list")).toBeVisible();
  expect(await bg()).toBe("rgb(255, 255, 255)");
  expect(await page.evaluate(() => document.documentElement.dataset.theme)).toBe("light");
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await page.reload();
  expect(await bg()).toBe("rgb(11, 11, 11)");
});

test("keyboard: skip link first, visible focus, tabs operable", async ({ page }) => {
  await ready(page);
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to content" });
  await expect(skip).toBeFocused();
  expect(await skip.evaluate((el) => el.getBoundingClientRect().top)).toBeGreaterThanOrEqual(0);
  await page.keyboard.press("Enter");
  await page.keyboard.press("Tab");
  const outline = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement;
    return getComputedStyle(el).outlineStyle;
  });
  expect(outline).not.toBe("none");
  const tab = page.getByRole("tab", { name: "Rewrite: handle_cancel()" });
  await page.getByRole("tab", { name: "Original: cancel()" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(tab).toBeFocused();
  await expect(tab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText("SEEDED BUG", { exact: false })).toBeVisible();
});

test("reduced motion disables smooth scrolling", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await ready(page);
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).scrollBehavior)).toBe("auto");
});

test("demo: step controls, counterexample, regression tabs", async ({ page }) => {
  await ready(page);
  const demo = page.locator("#demo");
  await expect(demo.getByText("Step 1 of 3")).toBeVisible();
  await demo.getByRole("button", { name: "Next step" }).click();
  await demo.getByRole("button", { name: "Next step" }).click();
  await expect(demo.getByText("Step 3 of 3")).toBeVisible();
  await expect(demo.getByRole("table", { name: /Differences reported/ })).toContainText("status_changed");
  await expect(demo.locator(".row-diff")).toHaveCount(1);
  await demo.getByRole("button", { name: "Previous" }).click();
  await expect(demo.getByText("Step 2 of 3")).toBeVisible();
  await expect(demo.getByText(/Responses and database state match/)).toBeVisible();
  await demo.getByRole("button", { name: "Restart" }).click();
  await expect(demo.getByText("Step 1 of 3")).toBeVisible();
  const toggle = demo.getByRole("button", { name: /original 13-operation sequence/ });
  await toggle.click();
  await expect(demo.locator("#original-seq li")).toHaveCount(13);
  await demo.getByRole("tab", { name: "regression.json" }).click();
  await expect(demo.getByText('"schema": "careo.scenario"')).toBeVisible();
});

test("downloads serve the published evidence", async ({ page, request }) => {
  await ready(page);
  const links = page.locator('a[download]');
  const hrefs = await links.evaluateAll((els) => els.map((e) => (e as HTMLAnchorElement).href));
  expect(hrefs.length).toBe(5);
  for (const href of hrefs) {
    const r = await request.get(href);
    expect(r.status(), href).toBe(200);
    expect((await r.body()).length).toBeGreaterThan(100);
  }
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "Generated pytest regression test" }).click();
  expect((await download).suggestedFilename()).toBe("test_careo_regression.py");
});

test("copy button copies the command", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await ready(page);
  const block = page.locator("#quick-start .code").first();
  await block.getByRole("button", { name: "Copy" }).click();
  await expect(block.getByRole("button", { name: "Copied" })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("pip install");
});

test("benchmarks: tables, chart, filters, provenance", async ({ page }) => {
  await ready(page);
  const b = page.locator("#benchmarks");
  await expect(b.getByRole("img", { name: /Seeded regression detection rate/ })).toBeVisible();
  await expect(b.getByText("Showing 128 of 128 runs")).toBeVisible();
  await b.getByLabel("Method", { exact: true }).selectOption("A_unit_tests");
  await expect(b.getByText("Showing 8 of 128 runs")).toBeVisible();
  await expect(b.getByRole("link", { name: /^[0-9a-f]{12}$/ })).toHaveAttribute("href", /\/commit\/[0-9a-f]{40}$/);
});

test("navigation anchors, external links and deployment paths", async ({ page }) => {
  const requested: string[] = [];
  page.on("request", (r) => requested.push(new URL(r.url()).pathname));
  await ready(page);
  const anchors = await page.locator('a[href^="#"]').evaluateAll((els) => els.map((e) => e.getAttribute("href")!));
  for (const a of anchors) {
    expect(await page.locator(a).count(), a).toBe(1);
  }
  await page.getByRole("link", { name: "Benchmarks" }).first().click();
  await expect(page).toHaveURL(/#benchmarks$/);
  const github = await page.locator('a[href^="https://github.com"]').evaluateAll((els) => els.map((e) => (e as HTMLAnchorElement).href));
  expect(github.length).toBeGreaterThan(3);
  for (const g of github) expect(g).toMatch(/^https:\/\/github\.com\/AnvitDevadiga\/careo(\/|$)/);
  for (const p of requested.filter((p) => !p.startsWith("/@"))) expect(p.startsWith("/careo/"), p).toBe(true);
});
