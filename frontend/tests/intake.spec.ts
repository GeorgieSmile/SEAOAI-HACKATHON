import { expect, test } from "@playwright/test";
import type { Page, Route } from "@playwright/test";
import flow from "../src/fixtures/integration-flow.json" with { type: "json" };

const snapshot = flow.snapshot;
const fourthOption = {
  option_id: "fourth",
  name_en: null,
  name_original: "第四色",
  image_source_id: null,
};
const archive = (name: string) => ({
  name,
  mimeType: "application/zip",
  buffer: Buffer.from("Deterministic upload response supplied by Playwright"),
});
const tinyPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8u8AAAAASUVORK5CYII=",
  "base64",
);

async function startWithoutListing(page: Page) {
  await page.route("**/api/case", (route) => route.fulfill({ json: { snapshot: null, case: null } }));
  await page.goto("/");
}

async function dropArchive(page: Page, filename: string) {
  const dataTransfer = await page.evaluateHandle((name) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["Deterministic archive response"], name, { type: "application/zip" }));
    return transfer;
  }, filename);
  try {
    await page.getByRole("region", { name: "Import a listing" }).dispatchEvent("drop", { dataTransfer });
  } finally {
    await dataTransfer.dispose();
  }
}

test("confirms one or three ordered options, preserves context and caps the selection", async ({ page }) => {
  await page.route("**/api/imports", (route) => route.fulfill({
    json: { status: "ready", snapshot: { ...snapshot, options: [...snapshot.options, fourthOption] } },
  }));
  await page.route("**/api/snapshots/*/images/*", (route) => route.fulfill({
    contentType: "image/png", body: tinyPng,
  }));
  let confirmations = 0;
  await page.route("**/api/questions", (route) => {
    const { snapshot_id, selected_option_ids, buyer_context } = route.request().postDataJSON();
    return route.fulfill({ json: {
      status: "ready",
      case: {
        case_id: `case-${++confirmations}`, revision: 0, snapshot_id,
        selected_option_ids, buyer_context, reason: null, phase: "awaiting_answers",
        report: null, last_operation: null, rounds: [{ ...flow.cases[0].first_round, answers: null }],
      },
    } });
  });
  await startWithoutListing(page);
  await page.locator("input[type=file]").setInputFiles(archive("four-options.zip"));

  const options = page.getByRole("checkbox");
  const confirm = page.getByRole("button", { name: "Confirm options" });
  await expect(options).toHaveCount(4);
  await expect(page.getByRole("status", { name: "" })).toContainText("0 of 3 selected");
  await expect(confirm).toBeDisabled();
  await expect(page.locator(".shared-values")).toContainText("720");
  await expect(page.locator("#listing-title")).toContainText("Premium-style, large-capacity stainless steel");
  await expect(options.nth(0)).toHaveAccessibleName(/applied-picture design · pink\/blue.*暮色316-贴画粉蓝/);
  const toggle = page.locator("summary").filter({ hasText: "What the seller claims" });
  const claim = page.locator(".seller-details .translation").getByText("暮色316 · applied-picture design · pink/blue");
  await expect(toggle).toBeVisible();
  await expect(claim).toBeHidden();
  await toggle.click();
  await expect(claim).toBeVisible();
  await toggle.press("Enter");
  await expect(claim).toBeHidden();

  await options.nth(2).check();
  const firstRequest = page.waitForRequest("**/api/questions");
  await confirm.click();
  const firstPayload = (await firstRequest).postDataJSON();
  expect(firstPayload).toEqual({
    operation_id: expect.any(String),
    previous_case: null,
    snapshot_id: "example-snapshot-1",
    selected_option_ids: ["v6"],
    buyer_context: "",
  });
  await expect(page.getByRole("heading", { name: "Round 1 of 3" })).toBeVisible();
  await page.getByRole("button", { name: "Change options or context" }).click();

  await options.nth(0).check();
  await options.nth(1).check();
  await options.nth(3).click();
  await expect(page.getByText("Choose up to 3 options. Remove one before adding another.")).toBeVisible();
  await expect(options.nth(3)).not.toBeChecked();
  const summary = page.getByRole("list", { name: "Selected options in order" });
  await expect(summary.locator("img")).toHaveCount(3);
  await expect(summary.locator("img").nth(0)).toHaveAttribute("src", /\/images\/image-v6$/);
  await expect(summary.locator("img").nth(1)).toHaveAttribute("src", /\/images\/image-v0$/);
  await expect(summary.locator("img").nth(2)).toHaveAttribute("src", /\/images\/image-v1$/);
  await expect(page.locator(".option img[src$='/image-v0']")).toHaveJSProperty("naturalWidth", 1);
  expect((await page.locator(".option img[src$='/image-v0']").boundingBox())?.width).toBe(1);

  const context = page.getByRole("textbox", { name: /What are you buying for/ });
  await context.fill("x".repeat(2001));
  await expect(confirm).toBeDisabled();
  await expect(page.getByText(/Use 2,000 characters or fewer/)).toBeVisible();
  await context.fill("🍵".repeat(2000));
  await expect(confirm).toBeEnabled();
  await context.fill("Resale in Thailand.");
  const secondRequest = page.waitForRequest("**/api/questions");
  await confirm.click();
  const secondPayload = (await secondRequest).postDataJSON();
  expect(secondPayload).toEqual({
    operation_id: expect.any(String),
    previous_case: { case_id: "case-1", revision: 0 },
    snapshot_id: "example-snapshot-1",
    selected_option_ids: ["v6", "v0", "v1"],
    buyer_context: "Resale in Thailand.",
  });
  expect(secondPayload.operation_id).not.toBe(firstPayload.operation_id);
  await expect(page.getByRole("heading", { name: "Round 1 of 3" })).toBeVisible();
});

test("shows Chinese fallback and separate image availability states without loading remote images", async ({ page }) => {
  const imageRequests: string[] = [];
  page.on("request", (request) => {
    if (request.resourceType() === "image") imageRequests.push(request.url());
  });
  await page.route("**/api/imports", (route) => route.fulfill({ json: {
    status: "ready",
    snapshot: {
      ...snapshot,
      sources: snapshot.sources.map((source) => {
        if (source.label === "Product title") return { ...source, text_en: null };
        if (source.source_id === "image-v0") return { ...source, availability: "missing", url: "https://invalid.example/missing.jpg" };
        if (source.source_id === "image-v1") return { ...source, availability: "unreadable" };
        if (source.source_id === "image-v6") return { ...source, availability: "missing" };
        return source;
      }),
      options: [...snapshot.options, fourthOption],
    },
  } }));
  await startWithoutListing(page);
  await page.locator("input[type=file]").setInputFiles(archive("partial.zip"));
  await expect(page.locator("#listing-title")).toContainText(snapshot.title);
  await expect(page.locator("#listing-title")).toContainText("English translation unavailable");
  await expect(page.getByRole("checkbox").nth(3)).toHaveAccessibleName(/第四色.*English translation unavailable/);
  await expect(page.getByText("Seller image unavailable")).toHaveCount(2);
  await expect(page.getByText("Seller image unreadable")).toHaveCount(1);
  await expect(page.getByText("No SKU-specific image")).toHaveCount(1);
  const brandImages = ["/dilidoo-icon.svg", "/dilidoo-detective.jpg"].map(path => new URL(path, page.url()).href);
  expect(imageRequests.filter(url => !brandImages.includes(url))).toEqual([]);
});

test("clears the old selection on upload and ignores the older response", async ({ page }) => {
  let pending: Route | undefined;
  await page.route("**/api/imports", async (route) => {
    const multipartBody = route.request().postData() ?? "";
    if (multipartBody.includes("pending.zip")) {
      pending = route;
      return;
    }
    await route.fulfill({ json: { status: "ready", snapshot } });
  });
  await page.route("**/api/snapshots/*/images/*", (route) => route.fulfill({
    contentType: "image/png", body: tinyPng,
  }));
  await startWithoutListing(page);
  const fileInput = page.locator("input[type=file]");
  await fileInput.setInputFiles(archive("first.zip"));
  await page.getByRole("checkbox").nth(0).check();
  await page.getByRole("textbox", { name: /What are you buying for/ }).fill("Previous purchase");

  await fileInput.setInputFiles(archive("pending.zip"));
  await expect(page.getByText("Reading listing and preparing translations…")).toBeVisible();
  await expect(page.getByRole("checkbox")).toHaveCount(0);
  await expect.poll(() => Boolean(pending)).toBeTruthy();

  await fileInput.setInputFiles(archive("latest.zip"));
  await expect(page.getByText("0 of 3 selected")).toBeVisible();
  await expect(page.getByRole("textbox", { name: /What are you buying for/ })).toHaveValue("");
  const oldResponse = page.waitForResponse((response) => response.request() === pending!.request());
  await pending!.fulfill({ json: {
    status: "ready",
    snapshot: { ...snapshot, snapshot_id: "older-snapshot", title: "Earlier listing" },
  } });
  await oldResponse;
  await expect(page.getByText("latest.zip")).toBeVisible();
  await expect(page.getByText("Earlier listing")).toBeHidden();
  await expect(page.getByRole("checkbox")).toHaveCount(3);
});

test("shows the active import failure and allows replacing the ZIP", async ({ page }) => {
  await page.route("**/api/imports", (route) => {
    if (route.request().postData()?.includes("broken.zip")) {
      return route.fulfill({ json: { status: "failed", code: "invalid_archive", message: "The ZIP is invalid." } });
    }
    return route.fulfill({ json: { status: "ready", snapshot } });
  });
  await page.route("**/api/snapshots/*/images/*", (route) => route.fulfill({
    contentType: "image/png", body: tinyPng,
  }));
  await startWithoutListing(page);
  const fileInput = page.locator("input[type=file]");
  await fileInput.setInputFiles(archive("broken.zip"));
  await expect(page.getByRole("alert")).toHaveText("The ZIP is invalid.");
  await expect(page.getByRole("checkbox")).toHaveCount(0);
  await fileInput.setInputFiles(archive("replacement.zip"));
  await expect(page.getByRole("checkbox")).toHaveCount(3);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("a saved listing lookup failure offers a read-only retry and blocks ordinary upload", async ({ page }) => {
  let restoreSucceeds = false;
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.method() !== "GET" && new URL(request.url()).pathname.startsWith("/api/")) writes.push(request.url());
  });
  await page.route("**/api/case", (route) => restoreSucceeds
    ? route.fulfill({ json: { snapshot, case: null } })
    : route.fulfill({ status: 503, json: { status: "failed", message: "Database temporarily unavailable." } }));
  await page.route("**/api/imports", (route) => route.fulfill({ json: { status: "ready", snapshot } }));
  await page.route("**/api/snapshots/*/images/*", (route) => route.fulfill({
    contentType: "image/png", body: tinyPng,
  }));
  await page.goto("/");

  await expect(page.getByRole("alert")).toHaveText(/could not check for a saved listing/i);
  await expect(page.getByRole("button", { name: "Choose ZIP", exact: true })).toHaveCount(0);
  await dropArchive(page, "accidental.zip");
  await expect(page.getByText("accidental.zip")).toHaveCount(0);
  await expect(page.getByRole("checkbox")).toHaveCount(0);

  restoreSucceeds = true;
  const retry = page.waitForRequest("**/api/case");
  await page.getByRole("button", { name: "Retry saved listing" }).click();
  expect((await retry).method()).toEqual("GET");
  await expect(page.getByText("Current listing restored")).toBeVisible();
  await expect(page.getByRole("checkbox")).toHaveCount(3);
  await page.getByRole("button", { name: "Replace", exact: true }).click();
  await expect(page.getByRole("button", { name: "Choose another ZIP" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Import link" })).toHaveAccessibleDescription(/clears any saved listing, photos, and answers/i);
  expect(writes).toEqual([]);

  const newImport = page.waitForRequest("**/api/imports");
  await dropArchive(page, "later.zip");
  expect((await newImport).method()).toEqual("POST");
  await expect(page.getByText("later.zip")).toBeVisible();
});

test("a deliberate replacement cannot be overwritten by a slow saved listing response", async ({ page }) => {
  // A transport that delivers a response even after the original caller has requested cancellation.
  await page.addInitScript(() => {
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, init) => originalFetch(input, input === "/api/case" ? { ...init, signal: undefined } : init);
  });
  let releaseRecovery!: () => void;
  const delay = new Promise<void>((resolve) => { releaseRecovery = resolve; });
  await page.route("**/api/case", async (route) => {
    await delay;
    await route.fulfill({ json: { snapshot: { ...snapshot, title: "Old saved listing" }, case: null } });
  });
  await page.route("**/api/imports", (route) => route.fulfill({ json: {
    status: "ready",
    snapshot: { ...snapshot, snapshot_id: "fresh-import", title: "Newly imported listing" },
  } }));
  await page.route("**/api/snapshots/*/images/*", (route) => route.fulfill({
    contentType: "image/png", body: tinyPng,
  }));
  const firstLookup = page.waitForRequest("**/api/case");
  await page.goto("/");
  await firstLookup;

  await expect(page.getByRole("status")).toHaveText(/checking for a saved listing/i);
  await dropArchive(page, "too-early.zip");
  await expect(page.getByText("too-early.zip")).toHaveCount(0);
  await page.getByRole("button", { name: "Start a new listing" }).click();
  await expect(page.getByText(/clears any saved listing, photos, and answers/i)).toBeVisible();
  await expect(page.getByRole("button", { name: "Choose replacement ZIP" })).toBeEnabled();

  const intentionalImport = page.waitForRequest("**/api/imports");
  await dropArchive(page, "intentional.zip");
  expect((await intentionalImport).postData()).toContain("intentional.zip");
  await expect(page.getByText("Newly imported listing")).toBeVisible();
  const oldResponse = page.waitForResponse("**/api/case");
  releaseRecovery();
  await (await oldResponse).finished();
  await page.waitForLoadState("networkidle");
  await expect(page.getByText("Old saved listing")).toHaveCount(0);
  await expect(page.getByText("Newly imported listing")).toBeVisible();
});

test("offers ZIP recovery when a TTP Cargo link fails", async ({ page }) => {
  const link = "https://taobao.ttpcargo.com/product?provider=1688&itemId=1035537244639";
  await page.route("**/api/snapshots/*/images/*", (route) => route.fulfill({ contentType: "image/png", body: tinyPng }));
  await page.route("**/api/imports", (route) => {
    if (route.request().headers()["content-type"] === "application/json") {
      expect(route.request().postDataJSON()).toEqual({ url: link });
      return route.fulfill({ json: { status: "failed", code: "intake_failed", message: "TTP Cargo could not load this listing." } });
    }
    return route.fulfill({ json: { status: "ready", snapshot } });
  });
  await startWithoutListing(page);
  await expect(page.getByLabel("TTP Cargo product link")).toHaveValue(link);
  await page.getByRole("button", { name: "Import link" }).click();
  await expect(page.getByRole("alert")).toContainText("could not load");
  await page.locator("input[type=file]").setInputFiles(archive("saved.zip"));
  await expect(page.getByRole("checkbox")).toHaveCount(snapshot.options.length);
  await expect(page.getByRole("alert")).toHaveCount(0);
});

const linkedProducts = [
  ["1688", "itemId=1035537244639&provider=1688&skuId=6048851312288"],
  ["Taobao", "skuId=6156598687347&provider=taobao&itemId=1003065116802"],
  ["Tmall", "provider=tmall&itemId=988441343788&skuId=5985509084995"],
];

for (const [provider, query] of linkedProducts) {
  test(`shows the ${provider} option from the link and lets the buyer confirm it`, async ({ page }) => {
    const url = "https://taobao.ttpcargo.com/product?" + query;
    const target = snapshot.options[1]!;
    await page.route("**/api/snapshots/*/images/*", (route) => route.fulfill({ contentType: "image/png", body: tinyPng }));
    await page.route("**/api/imports", (route) => {
      expect(route.request().postDataJSON()).toEqual({ url });
      return route.fulfill({ json: { status: "ready", snapshot: { ...snapshot, linked_option_id: target.option_id } } });
    });
    await startWithoutListing(page);
    await page.getByLabel("TTP Cargo product link").fill(url);
    await page.getByRole("button", { name: "Import link" }).click();
    const linked = page.getByRole("checkbox", { name: /From your link/i });
    await expect(linked).toHaveValue(target.option_id);
    await expect(page.getByRole("checkbox", { checked: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Confirm options" })).toBeDisabled();
    await linked.check();
    await expect(page.getByRole("button", { name: "Confirm options" })).toBeEnabled();
  });
}
