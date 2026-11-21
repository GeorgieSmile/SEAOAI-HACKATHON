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

test("confirms one or three ordered options, preserves context and caps the selection", async ({ page }) => {
  await page.route("**/api/imports", (route) => route.fulfill({
    json: { status: "ready", snapshot: { ...snapshot, options: [...snapshot.options, fourthOption] } },
  }));
  await page.route("**/api/snapshots/*/images/*", (route) => route.fulfill({
    contentType: "image/png", body: tinyPng,
  }));
  await page.route("**/api/selections", (route) => {
    const { snapshot_id, selected_option_ids, buyer_context } = route.request().postDataJSON();
    return route.fulfill({ json: {
      status: "confirmed", selection: { snapshot_id, selected_option_ids, buyer_context },
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
  await expect(page.locator(".seller-details")).not.toHaveAttribute("open");
  await page.locator(".seller-details summary").click();
  await expect(page.locator(".seller-details .translation").getByText("暮色316 · applied-picture design · pink/blue")).toBeVisible();

  await options.nth(2).check();
  const firstRequest = page.waitForRequest("**/api/selections");
  await confirm.click();
  const firstPayload = (await firstRequest).postDataJSON();
  expect(firstPayload).toEqual({
    operation_id: expect.any(String),
    previous_case: null,
    snapshot_id: "example-snapshot-1",
    selected_option_ids: ["v6"],
    buyer_context: "",
  });
  await expect(page.getByText("1 option confirmed.")).toBeVisible();

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
  const secondRequest = page.waitForRequest("**/api/selections");
  await confirm.click();
  const secondPayload = (await secondRequest).postDataJSON();
  expect(secondPayload).toEqual({
    operation_id: expect.any(String),
    previous_case: null,
    snapshot_id: "example-snapshot-1",
    selected_option_ids: ["v6", "v0", "v1"],
    buyer_context: "Resale in Thailand.",
  });
  expect(secondPayload.operation_id).not.toBe(firstPayload.operation_id);
  await expect(page.getByText("3 options confirmed.")).toBeVisible();
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
  await expect(page.getByText("Seller image missing from ZIP")).toHaveCount(2);
  await expect(page.getByText("Seller image unreadable")).toHaveCount(1);
  await expect(page.getByText("No SKU-specific image")).toHaveCount(1);
  expect(imageRequests).toEqual([]);
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
