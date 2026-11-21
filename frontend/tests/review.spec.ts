import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { fileURLToPath } from "node:url";
import type { CaseView, ListingSnapshot, PurchaseReview, Submission } from "../src/api";
import flow from "../src/fixtures/integration-flow.json" with { type: "json" };
import contract from "../../docs/contracts/examples/report-contract-example.json" with { type: "json" };

const snapshot = flow.snapshot as ListingSnapshot;
const linked = flow.report_example;
const skip = flow.cases.find((candidate) => candidate.id === linked.input.case_id)!;
const skipReport = linked.expected_result.report as PurchaseReview;
const richReport = contract.expected_result.report as PurchaseReview;
const ready = (saved: CaseView) => ({ status: "ready", case: saved });
const asSubmission = (answers: typeof linked.input.answers | typeof contract.input.answers) =>
  answers.map(({ question_id, answer_text }) => ({ question_id, answer_text }));

function reviewed(report: PurchaseReview, options: Partial<CaseView> = {}): CaseView {
  return {
    case_id: report.case_id, revision: report.revision, snapshot_id: report.snapshot_id,
    selected_option_ids: report.selected_option_ids, buyer_context: linked.input.buyer_context,
    phase: "reviewed", rounds: [{ ...skip.first_round, answers: asSubmission(linked.input.answers) }],
    reason: null, report,
    last_operation: { stage: "review", status: "ready", operation_id: "initial-report", code: null, message: null },
    ...options,
  };
}

async function openWorkspace(page: Page, saved: () => CaseView) {
  await page.route("**/api/case", (route) => route.fulfill({ json: { snapshot, case: saved() } }));
  await page.route("**/api/snapshots/*/images/*", (route) => {
    const [, snapshotId, id] = new URL(route.request().url()).pathname.match(/snapshots\/([^/]+)\/images\/([^/]+)/) ?? [];
    const source = snapshot.sources.find((source) => source.source_id === id && source.kind === "listing_image");
    if (snapshotId !== snapshot.snapshot_id || !source?.image_file) return route.fulfill({ status: 404 });
    return route.fulfill({
      path: fileURLToPath(new URL(`../../samples/images/${source.image_file}`, import.meta.url)),
      contentType: "image/jpeg",
    });
  });
  await page.goto("/");
}

test("opens scoped source evidence and compares ordered options while draft edits stay separate", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const [shared, capacity, use] = richReport.findings;
  const report: PurchaseReview = {
    ...richReport,
    findings: [
      shared!,
      {
        ...capacity!, option_ids: ["v6"], unknowns: [],
        citations: capacity!.citations.filter((citation) => citation.source_id === "image-v6"),
      },
      use!,
    ],
    comparisons: richReport.comparisons.map((comparison) => ({ ...comparison, values: [...comparison.values].reverse() })),
  };
  await openWorkspace(page, () => reviewed(report, {
    buyer_context: contract.input.buyer_context,
    rounds: [{ ...skip.first_round, answers: asSubmission(contract.input.answers) }],
  }));

  await expect(page.getByRole("heading", { name: "Before you order", level: 1 })).toBeFocused();
  const findings = page.getByRole("article");
  await expect(findings.getByRole("heading", { level: 3 })).toHaveText(report.findings.map((finding) => finding.title));
  await expect(findings.nth(0).getByText("Requirement unconfirmed · Option A, Option B, Option C", { exact: true })).toBeVisible();
  await expect(findings.nth(0).getByText("Screen printing is required.", { exact: true })).toBeVisible();
  await expect(findings.nth(0).getByText(report.findings[0]!.priority_reason, { exact: false })).toBeVisible();

  const methods = findings.nth(0).getByRole("region", { name: "Source evidence" })
    .locator("details").filter({ has: page.getByText("Customization methods offered", { exact: true }) });
  await methods.locator("summary").click();
  const original = snapshot.sources.find((source) => source.source_id === "customization-menu")!;
  await expect(methods.getByText(original.original_text!, { exact: true })).toBeVisible();
  await expect(methods.getByText(original.text_en!, { exact: true })).toBeVisible();
  await expect(methods.getByText("Seller listing claim · listing level", { exact: true })).toBeVisible();
  await expect(findings.nth(1).getByText("Listing conflict · Option C", { exact: true })).toBeVisible();
  await findings.nth(1).getByText("Seller image for option v6", { exact: true }).click();
  const image = findings.nth(1).getByRole("img", { name: "Seller image for option v6" });
  await expect(image).toHaveAttribute("src", `/api/snapshots/${snapshot.snapshot_id}/images/image-v6`);
  await expect.poll(() => image.evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await expect(findings.nth(1).getByText(capacity!.citations.find((citation) => citation.source_id === "image-v6")!.image_observation!)).toBeVisible();
  await expect(findings.nth(1).getByRole("img")).toHaveCount(1);
  await findings.nth(2).getByText("DMSC food-contact guidance · 30 Oct 2026", { exact: true }).click();
  await expect(findings.nth(2).getByText("Official guidance · curated reference", { exact: true })).toBeVisible();

  const comparison = page.getByRole("table", { name: "Compare selected options" });
  await expect(comparison.getByRole("columnheader")).toHaveText([
    "Listing detail",
    ...snapshot.options.map((option, index) => new RegExp(`Option ${"ABC"[index]}.*${option.name_original}`)),
  ]);
  const first = report.comparisons[0]!;
  await expect(comparison.getByRole("row").nth(1).getByRole("cell").nth(0)).toContainText(
    first.values.find((value) => value.option_id === "v0")!.summary,
  );

  const message = page.getByRole("textbox", { name: "Supplier message draft" });
  await expect(message).toHaveValue(report.supplier_draft);
  await message.fill("Please confirm the agreed option A.");
  await page.getByRole("button", { name: "Copy message" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Message copied." })).toBeVisible();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe("Please confirm the agreed option A.");
  await expect(findings.getByRole("heading", { level: 3 })).toHaveText(report.findings.map((finding) => finding.title));

  await page.evaluate(() => Object.defineProperty(navigator.clipboard, "writeText", {
    value: () => Promise.reject(new DOMException("Access denied", "NotAllowedError")),
  }));
  await page.getByRole("button", { name: "Copy message" }).click();
  await expect(page.getByRole("alert")).toHaveText("Could not copy the message. Select and copy the text manually.");
  await expect(message).toBeFocused();
  await message.fill(" ");
  await expect(page.getByRole("button", { name: "Copy message" })).toHaveCount(0);
});

test("shows three coverage groups and limitations for a report with no findings or supplier draft", async ({ page }) => {
  const empty = {
    ...skipReport, findings: [], open_preferences: [], supplier_draft: "",
    coverage: skipReport.coverage.map((coverage, index) => ({
      ...coverage, finding_ids: [], status: index === 0 ? "reviewed" as const : coverage.status,
    })),
  };
  await openWorkspace(page, () => reviewed(empty));
  await expect(page.getByText("No findings were reported. See the coverage and limitations below.")).toBeVisible();
  const coverage = page.getByRole("region", { name: "Coverage and limitations" });
  await expect(coverage.getByRole("heading", { level: 3 })).toHaveText([
    "Product match", "Intended use", "Supporting evidence", "Review limitations",
  ]);
  await expect(coverage.getByText("Reviewed", { exact: true })).toBeVisible();
  await expect(coverage.getByText(/Product-detail images were not reviewed/)).toBeVisible();
  await expect(page.getByText("This review has no questions for the supplier.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Copy message" })).toHaveCount(0);
});

test("retains saved answers on review failure, retries the same operation, and replaces the draft after editing", async ({ page }) => {
  let saved = reviewed(skipReport, {
    revision: 0, phase: "awaiting_answers", report: null, last_operation: null,
    rounds: [{ ...skip.first_round, answers: null }],
  });
  let writes = 0;
  await page.route("**/api/reviews", (route) => {
    const { operation_id, revision, submission } = route.request().postDataJSON() as {
      operation_id: string; revision: number; submission: Submission;
    };
    ++writes;
    const operation = {
      operation_id, stage: "review" as const, status: writes === 1 ? "failed" as const : "ready" as const,
      code: writes === 1 ? "invalid_review" : null, message: writes === 1 ? "The review could not be validated." : null,
    };
    const updatedReport = {
      ...skipReport, revision: revision + 1, supplier_draft: "Draft for the changed answers.",
      findings: [...skipReport.findings].reverse(),
    };
    saved = reviewed(updatedReport, {
      phase: writes === 1 ? "failed" : "reviewed", report: writes === 1 ? null : updatedReport,
      rounds: [{ ...skip.first_round, answers: submission.answers }], last_operation: operation,
    });
    return route.fulfill({ json: writes === 1
      ? { status: "failed", case_id: saved.case_id, revision: saved.revision, code: operation.code, message: operation.message }
      : ready(saved) });
  });
  await page.route("**/api/case/retry", (route) => {
    saved = { ...saved, phase: "reviewed", report: skipReport,
      last_operation: { ...saved.last_operation!, status: "ready", code: null, message: null } };
    return route.fulfill({ json: ready(saved) });
  });
  await openWorkspace(page, () => saved);

  const request = page.waitForRequest("**/api/reviews");
  await page.getByRole("button", { name: "View purchase review" }).click();
  const failedOperationId = (await request).postDataJSON().operation_id;
  await expect(page.getByRole("alert")).toHaveText("The review could not be validated.");
  await expect(page.getByRole("heading", { name: "Before you order", level: 1 })).toHaveCount(0);
  await page.getByText("Round 1 answers", { exact: true }).click();
  await expect(page.getByText("Skipped / unknown", { exact: true })).toHaveCount(3);
  const retry = page.waitForRequest("**/api/case/retry");
  await page.getByRole("button", { name: "Retry purchase review" }).click();
  expect((await retry).postDataJSON()).toEqual({ case_id: saved.case_id, revision: 1, operation_id: failedOperationId });
  await expect(page.getByRole("heading", { name: "Before you order", level: 1 })).toBeVisible();

  const draft = page.getByRole("textbox", { name: "Supplier message draft" });
  await draft.fill("My local edit.");
  await page.getByRole("link", { name: "Edit answers" }).click();
  await page.getByRole("button", { name: "Edit round 1" }).click();
  await expect(draft).toHaveCount(0);
  await page.getByRole("button", { name: "Cancel edit" }).click();
  await expect(draft).toHaveValue("My local edit.");

  await page.getByRole("button", { name: "Edit round 1" }).click();
  await page.getByRole("radio", { name: "Screen printing is required." }).check();
  const changed = page.waitForRequest("**/api/reviews");
  await page.getByRole("button", { name: "View purchase review" }).click();
  expect((await changed).postDataJSON()).toMatchObject({
    revision: 1,
    submission: { answers: [{ question_id: "q-finish", answer_text: "Screen printing is required." },
      { question_id: "q-capacity", answer_text: null }, { question_id: "q-use", answer_text: null }] },
  });
  await expect(draft).toHaveValue("Draft for the changed answers.");
  await expect(page.getByRole("article").first().getByRole("heading", { level: 3 })).toHaveText(skipReport.findings.at(-1)!.title);

  await page.reload();
  await expect(draft).toHaveValue("Draft for the changed answers.");
  expect(writes).toBe(2);
});

test("discards a delayed report after a new selection starts a different case", async ({ page }) => {
  let saved = reviewed(skipReport, { phase: "ready_for_review", report: null });
  let release!: () => void;
  const hold = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/reviews", async (route) => {
    const operation_id = route.request().postDataJSON().operation_id;
    const last_operation = {
      stage: "review" as const, status: "running" as const, operation_id, code: null, message: null,
    };
    saved = { ...saved, phase: "generating_review", last_operation };
    const old = reviewed(skipReport, { last_operation: { ...last_operation, status: "ready" } });
    await hold;
    await route.fulfill({ json: ready(old) });
  });
  await page.route("**/api/questions", (route) => {
    const { selected_option_ids, buyer_context, operation_id } = route.request().postDataJSON();
    saved = { ...saved, case_id: "new-case", revision: 0, selected_option_ids, buyer_context,
      phase: "awaiting_answers", rounds: [{ ...skip.first_round, reason: "Questions for the new selection.", answers: null }],
      last_operation: { stage: "start", status: "ready", operation_id, code: null, message: null } };
    return route.fulfill({ json: ready(saved) });
  });
  await openWorkspace(page, () => saved);

  try {
    const delayed = page.waitForRequest("**/api/reviews");
    await page.getByRole("button", { name: "View purchase review" }).click();
    await delayed;
    await expect(page.getByText("Preparing your purchase review…")).toBeVisible();
    await page.getByRole("button", { name: "Change options or context" }).click();
    await page.getByRole("checkbox").nth(0).uncheck();
    await page.getByRole("button", { name: "Confirm options" }).click();
    await expect(page.getByText("Questions for the new selection.")).toBeVisible();
    const response = page.waitForResponse("**/api/reviews");
    release();
    await (await response).finished();
    await page.waitForLoadState("networkidle");
    await expect(page.getByText("Questions for the new selection.")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Before you order", level: 1 })).toHaveCount(0);
  } finally {
    release();
  }
});

test("rejects a ready review with an old revision and accepts corrected saved progress on reload", async ({ page }) => {
  let saved = reviewed(skipReport, { revision: skipReport.revision + 1 });
  await openWorkspace(page, () => saved);
  await expect(page.getByRole("alert")).toContainText("could not be matched to your saved answers");
  await expect(page.getByRole("article")).toHaveCount(0);
  saved = { ...saved, report: { ...skipReport, revision: saved.revision } };
  await page.getByRole("button", { name: "Reload saved progress" }).click();
  await expect(page.getByRole("heading", { name: "Before you order", level: 1 })).toBeVisible();
});
