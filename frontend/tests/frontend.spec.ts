import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

const markdown = "# Example Domain\n\n> An example website for documentation.\n\n## Documentation\n\n- [Getting started](https://example.com/start): Start here.\n";
const created = { job_id: "job-1", website_id: "site-1", status: "pending" };
const job = {
  ...created, status: "completed", job_type: "initial", created_at: "2026-10-15T12:00:00Z",
  started_at: "2026-10-15T12:00:01Z", completed_at: "2026-10-15T12:00:04Z",
  pages_crawled: 42, pages_selected: 25, error: null, warnings: [], output_changed: true,
};
const website = {
  website_id: "site-1", original_url: "https://example.com", normalized_url: "https://example.com/",
  title: "Example Domain", status: "completed", created_at: job.created_at,
  last_crawl_at: job.completed_at, last_successful_generation_at: job.completed_at, next_check_at: null,
  latest_generation: { generation_id: "gen-1", content_hash: "abc", generated_at: job.completed_at, is_latest: true },
};

async function mockApi(page: Page, options: { statuses?: string[]; empty?: boolean; warnings?: string[]; url?: string } = {}) {
  const calls = { generate: 0, polls: 0, refresh: 0, downloads: 0 };
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/generate") {
      calls.generate++;
      expect(route.request().postDataJSON()).toEqual({ url: options.url ?? "https://example.com" });
      return route.fulfill({ status: 202, json: created });
    }
    if (path.endsWith("/refresh")) {
      calls.refresh++;
      return route.fulfill({ status: 202, json: { ...created, job_id: `job-${calls.refresh + 1}` } });
    }
    if (path.startsWith("/api/jobs/")) {
      const statuses = options.statuses ?? ["completed"];
      const status = statuses[Math.min(calls.polls++, statuses.length - 1)];
      return route.fulfill({ json: { ...job, job_id: path.split("/").pop(), status, warnings: options.warnings ?? [], error: status === "failed" ? "Crawl failed. Internal details deliberately hidden." : null, output_changed: calls.refresh ? false : true } });
    }
    if (path.endsWith("/llms.txt") || path.endsWith("/download")) {
      if (path.endsWith("/download")) calls.downloads++;
      return route.fulfill({ contentType: "text/plain", body: options.empty ? "  \n" : markdown });
    }
    if (path === "/api/websites/site-1") return route.fulfill({ json: website });
    return route.fulfill({ status: 404, json: { error: { code: "not_found" } } });
  });
  return calls;
}

async function submit(page: Page) {
  await page.getByLabel("Your website URL").fill("https://example.com");
  await page.getByRole("button", { name: "Generate llms.txt", exact: true }).click();
}

test("homepage renders responsively and provides an accessible form", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Make your website easier for AI to understand.");
  await expect(page.getByRole("button", { name: "Generate llms.txt", exact: true })).toBeEnabled();
  await expect(page.getByRole("heading", { name: "Start with your URL" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: `test-results/mock/home-${testInfo.project.name}.png`, fullPage: true });
  expect(errors).toEqual([]);
});

test("invalid and unsupported URLs never create jobs", async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto("/");
  const input = page.getByLabel("Your website URL");
  const button = page.getByRole("button", { name: "Generate llms.txt", exact: true });
  for (const value of ["", "example.com", "ftp://example.com", "https://user:password@example.com", "https://example.com:8080"]) {
    await input.fill(value);
    await button.click();
    await expect(page.getByRole("main").getByRole("alert")).toBeVisible();
    await expect(input).toHaveAttribute("aria-invalid", "true");
  }
  expect(calls.generate).toBe(0);
});

test("submission polls real status values, blocks duplicates, and displays output", async ({ page }, testInfo) => {
  const calls = await mockApi(page, { statuses: ["pending", "running", "completed"] });
  await page.goto("/");
  await submit(page);
  await expect(page.getByText("Website queued. Waiting for an available worker.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Generating…", exact: true })).toBeDisabled();
  await expect(page.getByLabel("Your website URL")).toBeDisabled();
  await expect(page.getByText("Your website is being crawled and analyzed.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "llms.txt generated successfully!" })).toBeVisible();
  await expect(page.getByLabel("llms.txt Markdown preview")).toHaveText(markdown);
  await expect(page.getByText("42", { exact: true })).toBeVisible();
  await expect(page.getByText("25", { exact: true })).toBeVisible();
  await expect(page.locator("time")).toHaveAttribute("datetime", job.completed_at);
  expect(calls.generate).toBe(1);
  expect(calls.polls).toBe(3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `test-results/mock/result-${testInfo.project.name}.png`, fullPage: true });
});

test("copy writes exact Markdown, download uses the backend, and regeneration updates output", async ({ page, context }) => {
  const calls = await mockApi(page);
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  await submit(page);
  await page.getByRole("button", { name: "Copy", exact: true }).click();
  await expect(page.getByRole("button", { name: "Copied!", exact: true })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(markdown);
  const downloading = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download", exact: true }).click();
  const download = await downloading;
  expect(download.suggestedFilename()).toBe("llms.txt");
  expect(await readFile((await download.path())!, "utf8")).toBe(markdown);
  expect(calls.downloads).toBe(1);
  await page.getByRole("button", { name: "Regenerate", exact: true }).click();
  await expect(page.getByText(/Your file is already up to date/)).toBeVisible();
  expect(calls.refresh).toBe(1);
});

test("failed crawls and empty files show useful errors without server details", async ({ page }) => {
  await mockApi(page, { statuses: ["failed"] });
  await page.goto("/");
  await submit(page);
  await expect(page.getByRole("main").getByRole("alert")).toContainText("We couldn’t crawl this website");
  await expect(page.getByRole("main").getByRole("alert")).not.toContainText("Internal details");
  await expect(page.getByRole("button", { name: "Generate llms.txt", exact: true })).toBeEnabled();
  await page.unroute("**/api/**");
  await mockApi(page, { empty: true });
  await submit(page);
  await expect(page.getByRole("main").getByRole("alert")).toContainText("The generated file is empty");
  await expect(page.getByLabel("llms.txt Markdown preview")).not.toBeVisible();
});

test("submission errors handle unavailable service, timeout, and backend URL rejection", async ({ page }) => {
  await page.goto("/");
  for (const status of [502, 504, 422, 429]) {
    await page.route("**/api/generate", route => route.fulfill({ status, body: "private stack trace must not appear" }));
    await submit(page);
    await expect(page.getByRole("main").getByRole("alert")).toContainText(status === 504 ? "timed out" : status === 422 ? "public HTTP(S)" : status === 429 ? "demo is busy" : "generation service");
    await expect(page.getByRole("main").getByRole("alert")).not.toContainText("stack trace");
    await expect(page.getByRole("button", { name: "Generate llms.txt", exact: true })).toBeEnabled();
    await page.unroute("**/api/generate");
  }
});

test("refresh and download failures preserve the last successful preview", async ({ page }) => {
  await mockApi(page);
  await page.goto("/");
  await submit(page);
  await expect(page.getByLabel("llms.txt Markdown preview")).toHaveText(markdown);
  await page.route("**/api/websites/site-1/refresh", route => route.fulfill({ status: 503 }));
  await page.getByRole("button", { name: "Regenerate", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("generation service");
  await expect(page.getByLabel("llms.txt Markdown preview")).toHaveText(markdown);
  await expect(page.getByText("Your last successful file is still available below.")).toBeVisible();
  await page.route("**/api/websites/site-1/download", route => route.fulfill({ status: 502 }));
  await page.getByRole("button", { name: "Download", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert").last()).toContainText("generation service");
  await expect(page.getByLabel("llms.txt Markdown preview")).toHaveText(markdown);
});

test("active jobs resume after reload without another submission", async ({ page }) => {
  const calls = await mockApi(page, { statuses: ["pending", "completed"] });
  await page.goto("/");
  await submit(page);
  await expect(page.getByText("Website queued. Waiting for an available worker.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "llms.txt generated successfully!" })).toBeVisible();
  expect(calls.generate).toBe(1);
});

test("interrupted polling retries and can resume the same job", async ({ page }) => {
  const calls = await mockApi(page);
  await page.route("**/api/jobs/*", route => route.fulfill({ status: 503 }));
  await page.goto("/");
  await submit(page);
  await expect(page.getByText("Connection interrupted. Reconnecting automatically…")).toBeVisible();
  const retry = page.getByRole("button", { name: "Check job again" });
  await expect(retry).toBeVisible();
  await page.unroute("**/api/jobs/*");
  await retry.click();
  await expect(page.getByRole("heading", { name: "llms.txt generated successfully!" })).toBeVisible();
  expect(calls.generate).toBe(1);
});

test("crawl warnings are visible and Markdown is rendered as safe text", async ({ page }) => {
  await mockApi(page, { warnings: ["A page could not be fetched: https://example.com/missing"] });
  await page.goto("/");
  await submit(page);
  await page.getByText("Completed with 1 crawl warning").click();
  await expect(page.getByText("A page could not be fetched:", { exact: false })).toBeVisible();
  await expect(page.getByLabel("llms.txt Markdown preview").locator("a")).toHaveCount(0);
});

test("regeneration keeps the previous file until changed output arrives, and survives a failed refresh job", async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto("/");
  await submit(page);
  const preview = page.getByLabel("llms.txt Markdown preview");
  await expect(preview).toHaveText(markdown);
  let refreshPolls = 0;
  await page.route("**/api/jobs/job-2", route => route.fulfill({ json: {
    ...job, job_id: "job-2", status: refreshPolls++ === 0 ? "running" : "completed", pages_crawled: 43,
  } }));
  const updated = "# Example Domain\n\nUpdated website overview.\n";
  await page.route("**/api/websites/site-1/llms.txt", route => route.fulfill({ body: updated, contentType: "text/plain" }));
  await page.getByRole("button", { name: "Regenerate", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Refreshing your llms.txt…" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Regenerate", exact: true })).toBeDisabled();
  await expect(preview).toHaveText(markdown);
  await expect(preview).toHaveText(updated);
  await expect(page.getByText("43", { exact: true })).toBeVisible();
  await page.route("**/api/jobs/job-3", route => route.fulfill({ json: { ...job, job_id: "job-3", status: "failed", error: "No accessible HTML pages were extracted." } }));
  await page.getByRole("button", { name: "Regenerate", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("We couldn’t crawl this website");
  await expect(preview).toHaveText(updated);
  expect(calls.refresh).toBe(2);
});

test("clipboard denial selects the Markdown and explains manual copying", async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", { value: { writeText: () => Promise.reject(new DOMException("Denied", "NotAllowedError")) } });
  });
  await page.goto("/");
  await submit(page);
  await page.getByRole("button", { name: "Copy", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Clipboard access is unavailable");
  // DOM selections omit the terminal line break; the clipboard API and download
  // tests above separately verify exact file bytes, including that line break.
  expect(await page.evaluate(() => window.getSelection()?.toString())).toBe(markdown.trimEnd());
});

test("network failure during submission is recoverable", async ({ page }) => {
  await page.route("**/api/generate", route => route.abort("failed"));
  await page.goto("/");
  await submit(page);
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Check your connection");
  await expect(page.getByRole("button", { name: "Generate llms.txt", exact: true })).toBeEnabled();
});

for (const [shortcut, target] of [
  ["/example.com", "https://example.com"],
  ["/https://example.com", "https://example.com"],
  ["/http://example.com/docs/", "http://example.com/docs/"],
  ["/example.com/docs/a%2Fb%3Fc%25?x=a%2Fb&url=another&job=target-job", "https://example.com/docs/a%2Fb%3Fc%25?x=a%2Fb&url=another&job=target-job"],
]) {
  test(`URL shortcut ${shortcut} starts generation automatically`, async ({ page }) => {
    const calls = await mockApi(page, { url: target });
    await page.goto(shortcut);
    await expect(page.getByLabel("llms.txt Markdown preview")).toHaveText(markdown);
    await expect(page.getByLabel("Generate for another website")).toHaveValue(target);
    const location = new URL(page.url());
    expect(location.pathname).toBe("/");
    expect(location.searchParams.get("url")).toBe(target);
    expect(location.searchParams.get("job")).toBe("job-1");
    expect(calls.generate).toBe(1);
  });
}

test("shortcut jobs resume while running and reopen after completion without submitting again", async ({ page }) => {
  const calls = await mockApi(page, { statuses: ["pending", "completed"] });
  await page.goto("/example.com");
  await expect(page.getByText("Website queued. Waiting for an available worker.")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("llms.txt Markdown preview")).toHaveText(markdown);
  // The job URL also works after completion and without session storage.
  await page.evaluate(() => sessionStorage.clear());
  await page.reload();
  await expect(page.getByLabel("llms.txt Markdown preview")).toHaveText(markdown);
  expect(calls.generate).toBe(1);
});

test("explicit shortcut target takes precedence over a different saved job", async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem("llms-active-job", JSON.stringify({
    job_id: "job-other", website_id: "site-other", url: "https://other.example.com", status: "running",
  })));
  const calls = await mockApi(page);
  await page.goto("/example.com");
  await expect(page.getByLabel("llms.txt Markdown preview")).toHaveText(markdown);
  expect(calls.generate).toBe(1);
  expect(calls.polls).toBe(1);
});

test("invalid shortcut targets and job links do not create jobs", async ({ page }) => {
  const calls = await mockApi(page);
  for (const shortcut of [
    "/ftp://example.com", "/https://user:password@example.com", "/example.com:8080",
    "/?url=javascript%3Aalert(1)", "/?url=https%3A%2F%2Fexample.com&url=https%3A%2F%2Fother.com",
    "/?url=https%3A%2F%2Fexample.com&job=invalid%2Fjob",
    `/?url=${encodeURIComponent("https://example.com/" + "a".repeat(2048))}`,
  ]) {
    await page.goto(shortcut);
    await expect(page.getByRole("main").getByRole("alert")).toBeVisible();
    await expect(page.getByRole("button", { name: "Generate llms.txt", exact: true })).toBeEnabled();
  }
  expect(calls.generate).toBe(0);
});

test("a missing linked job shows recovery controls without automatically recrawling", async ({ page }) => {
  const calls = await mockApi(page);
  await page.route("**/api/jobs/job-missing", route => route.fulfill({ status: 404 }));
  await page.goto("/?url=https%3A%2F%2Fexample.com&job=job-missing");
  await expect(page.getByRole("main").getByRole("alert")).toContainText("no longer available");
  expect(calls.generate).toBe(0);
  await page.getByRole("button", { name: "Generate llms.txt", exact: true }).click();
  await expect(page.getByLabel("llms.txt Markdown preview")).toHaveText(markdown);
  expect(calls.generate).toBe(1);
});

test("shortcut catch-all preserves unknown route and API 404s and static assets", async ({ request }) => {
  expect((await request.get("/not-a-website")).status()).toBe(404);
  expect((await request.get("/api/not-a-route")).status()).toBe(404);
  expect((await request.get("/icon.svg")).status()).toBe(200);
});
