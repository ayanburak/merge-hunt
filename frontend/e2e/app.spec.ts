/**
 * file: e2e/app.spec.ts
 * description: Walks through the pages in a real browser, with the backend faked by FakeBackend.
 */

// These cover what the unit tests cannot see: moving between pages, how many requests a page
// sends and how the page is laid out at a given width.

import { expect, test } from "@playwright/test";

import { FakeBackend, scanned, scored } from "./backend";

let backend: FakeBackend;

test.beforeEach(async ({ page }) => {
  backend = new FakeBackend();
  await backend.attach(page);
});

test("a scan starts, runs and leads to its results", async ({ page }) => {
  backend.repos = [scored("widgets", 7)];
  await page.goto("/");

  await page.getByRole("button", { name: "Start scan" }).click();
  await expect(page.getByText("of 6")).toBeVisible();

  backend.scans[0] = scanned({ id: backend.scans[0].id, done: 6, candidates: 10, skipped: 4 });
  await page.getByRole("link", { name: "See results" }).click();

  await expect(page).toHaveURL(/\/scans\/1\/results$/);
  await expect(page.getByText("acme/")).toBeVisible();
});

test("the results lead to a repo and its issues", async ({ page }) => {
  backend.scans = [scanned()];
  backend.repos = [scored("widgets", 7), scored("gears", 8, { failed_filters: ["merge_rate"] })];
  await page.goto("/scans/1/results");

  await expect(page.getByText("of 2 repos pass")).toBeVisible();
  await page.getByRole("button", { name: /widgets/ }).click();
  await page.getByRole("link", { name: "See 1 issue" }).click();

  await expect(page).toHaveURL(/\/repos\/7$/);
  await expect(page.getByRole("heading", { name: "acme/widgets" })).toBeVisible();
  await expect(page.getByText("Fix the broken link")).toBeVisible();
});

test("returning to the results asks for them once", async ({ page }) => {
  backend.scans = [scanned()];
  backend.repos = [scored("widgets", 7)];
  await page.goto("/scans/1/results");
  await expect(page.getByText("of 1 repos pass")).toBeVisible();

  await page.getByRole("link", { name: "Other scans" }).click();
  await expect(page.getByText("Earlier scans")).toBeVisible();
  const before = backend.count("POST /scans/1/results");

  await page.locator('a[href="/scans/1/results"]').last().click();
  await expect(page.getByText("of 1 repos pass")).toBeVisible();
  await page.waitForTimeout(500);

  expect(backend.count("POST /scans/1/results") - before).toBe(1);
});

test("the results follow a running scan", async ({ page }) => {
  backend.scans = [scanned({ status: "RUNNING", done: 1, finished_at: null })];
  backend.repos = [scored("widgets", 7)];
  await page.goto("/scans/1/results");
  await expect(page.getByText("of 1 repos pass")).toBeVisible();

  backend.repos = [scored("widgets", 7), scored("gears", 8)];
  backend.scans = [scanned({ status: "RUNNING", done: 2, finished_at: null })];

  await expect(page.getByText("of 2 repos pass")).toBeVisible();
});

test("an unknown repo answers at once and sends one request", async ({ page }) => {
  await page.goto("/repos/999");

  await expect(page.getByRole("heading", { name: "This repo is not known here" })).toBeVisible({ timeout: 500 });
  await page.waitForTimeout(1200);
  expect(backend.count("GET /repos/999")).toBe(1);
});

test("an address that is no id never reaches the backend", async ({ page }) => {
  await page.goto("/repos/abc");
  await expect(page.getByRole("heading", { name: "This repo is not known here" })).toBeVisible();

  await page.goto("/scans/abc/results");
  await expect(page.getByRole("heading", { name: "This scan does not exist" })).toBeVisible();

  expect(backend.requests.filter((item) => item.includes("abc") || item.includes("NaN"))).toEqual([]);
});

test("the failed repos of a finished scan can be read on its results", async ({ page }) => {
  backend.scans = [scanned({ failures: [{ repo: "acme/broken", error: "Issues could not be fetched: timed out" }] })];
  backend.repos = [scored("widgets", 7)];
  await page.goto("/scans/1/results");

  await page.getByText("1 repo could not be fetched completely").click();

  await expect(page.getByText("Issues could not be fetched: timed out")).toBeVisible();
});

test("a scan with no results opens from the history with its error", async ({ page }) => {
  backend.scans = [scanned({ status: "FAILED", done: 0, error: "Bad credentials" })];
  await page.goto("/");

  await page.getByRole("button", { name: /Failed$/ }).click();

  await expect(page.getByText("The scan could not finish")).toBeVisible();
  await expect(page.getByText("Bad credentials")).toBeVisible();
});

for (const width of [320, 375]) {
  test(`the scans page fits a width of ${width} pixels`, async ({ page }) => {
    await page.setViewportSize({ width, height: 640 });
    await page.goto("/");

    const name = await page.getByRole("link", { name: "merge-hunt" }).boundingBox();
    const scrolls = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);

    expect(name!.height).toBeLessThan(30);
    expect(scrolls).toBe(false);
  });
}
