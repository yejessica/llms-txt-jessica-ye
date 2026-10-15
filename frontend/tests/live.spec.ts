import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("real Next.js proxy → FastAPI → SQLite → crawler worker → output and refresh", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  await page.getByLabel("Your website URL").fill("https://example.com");
  const submission = page.waitForResponse(response => response.url().endsWith("/api/generate"));
  await page.getByRole("button", { name: "Generate llms.txt", exact: true }).click();
  const response = await submission;
  expect(response.status()).toBe(202);
  const created = await response.json();
  expect(created.job_id).toBeTruthy();
  await expect(page.getByRole("heading", { name: "llms.txt generated successfully!" })).toBeVisible();
  const preview = page.getByLabel("llms.txt Markdown preview");
  await expect(preview).toContainText("# Example Domain");
  const content = await preview.textContent();
  await page.getByRole("button", { name: "Copy", exact: true }).click();
  await expect(page.getByRole("button", { name: "Copied!", exact: true })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(content);
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download", exact: true }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe("llms.txt");
  expect(await readFile((await download.path())!, "utf8")).toBe(content);
  const refresh = page.waitForResponse(response => response.url().endsWith("/refresh"));
  await page.getByRole("button", { name: "Regenerate", exact: true }).click();
  const refreshed = await (await refresh).json();
  expect(refreshed.job_id).not.toBe(created.job_id);
  await expect(page.getByText(/Your file is already up to date/)).toBeVisible();
  await expect(preview).toHaveText(content!);
  await page.screenshot({ path: "test-results/live/example-com-result.png", fullPage: true });

  // This assertion hits the real backend validation through the production proxy.
  const rejected = await page.request.post("/api/generate", { data: { url: "http://127.0.0.1" } });
  expect(rejected.status()).toBe(422);
});
