import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { fileURLToPath } from "node:url";
import type { CaseView, ListingSnapshot, PurchaseReview, QuestionRound, ResolvedRequirement } from "../src/api";
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

const confirmBrief = (page: Page) => page.getByRole("button", { name: "Confirm brief and generate review", exact: true });

function prepared(saved: CaseView, operation_id: string, changes: Partial<CaseView> = {}): CaseView {
  return {
    ...saved, ...changes, phase: "ready_for_review", report: null, confirmed_revision: undefined,
    last_operation: { stage: "brief", status: "ready", operation_id, code: null, message: null },
  };
}

async function mockAnswerImpact(page: Page) {
  await page.route("**/api/brief/preview-answers", (route) => route.fulfill({
    json: { changed_requirements: [], removed_requirements: [], discarded_rounds: [] },
  }));
}

for (const entry of ["TTP Cargo link", "ZIP after a link failure"] as const) {
  test(`opens and resumes the purchase review from ${entry}`, async ({ page }) => {
    const usingLink = entry === "TTP Cargo link";
    const url = "https://taobao.ttpcargo.com/product?provider=1688&itemId=1035537244639&skuId=6048851312288";
    let saved: CaseView | null = null;
    let imported: ListingSnapshot | null = null;
    await page.route("**/api/case", (route) => route.fulfill({ json: { snapshot: imported, case: saved } }));
    await page.route("**/api/snapshots/*/images/*", (route) => route.fulfill({ status: 404 }));
    await page.route("**/api/imports", (route) => {
      const isLink = route.request().headers()["content-type"] === "application/json";
      if (isLink) {
        expect(route.request().postDataJSON()).toEqual({ url });
        if (!usingLink) {
          return route.fulfill({ json: { status: "failed", code: "link_unavailable", message: "TTP Cargo could not load this listing." } });
        }
      }
      imported = { ...snapshot, linked_option_id: isLink ? skipReport.selected_option_ids.at(-1)! : null };
      return route.fulfill({ json: { status: "ready", snapshot: imported } });
    });
    await page.route("**/api/questions", (route) => {
      const request = route.request().postDataJSON();
      expect(request.selected_option_ids).toEqual(skipReport.selected_option_ids);
      saved = reviewed(skipReport, {
        revision: 0, phase: "awaiting_answers", report: null, buyer_context: request.buyer_context,
        rounds: [{ ...skip.first_round, answers: null }],
        last_operation: { stage: "start", status: "ready", operation_id: request.operation_id, code: null, message: null },
      });
      return route.fulfill({ json: ready(saved) });
    });
    await page.route("**/api/brief", (route) => {
      const { operation_id, submission } = route.request().postDataJSON();
      expect(submission.answers).toEqual(asSubmission(linked.input.answers));
      saved = prepared(saved!, operation_id, { revision: skipReport.revision,
        rounds: [{ ...skip.first_round, answers: submission.answers }],
      });
      return route.fulfill({ json: ready(saved) });
    });
    await page.route("**/api/reviews", (route) => {
      const request = route.request().postDataJSON();
      expect(request).toMatchObject({ confirm: true, revision: saved!.revision });
      saved = reviewed(skipReport, {
        buyer_context: "",
        last_operation: { stage: "review", status: "ready", operation_id: request.operation_id, code: null, message: null },
      });
      return route.fulfill({ json: ready(saved) });
    });

    await page.goto("/");
    await page.getByLabel("TTP Cargo product link").fill(url);
    await page.getByRole("button", { name: "Import link" }).click();
    if (!usingLink) {
      await expect(page.getByRole("alert")).toContainText("TTP Cargo could not load");
      const chooser = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: "Choose another ZIP" }).click();
      await (await chooser).setFiles({ name: "listing.zip", mimeType: "application/zip", buffer: Buffer.from("Fixture") });
    }
    await expect(page.getByText("From your link")).toHaveCount(usingLink ? 1 : 0);
    const options = page.getByRole("group", { name: "Product options" });
    const confirm = page.getByRole("button", { name: "Confirm options" });
    await expect(confirm).toBeDisabled();
    for (const id of skipReport.selected_option_ids) await options.locator(`input[value="${id}"]`).check();
    await confirm.click();
    await page.getByRole("button", { name: "Review buying brief", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Review buying brief" })).toBeVisible();
    await confirmBrief(page).click();
    await expect(page.getByRole("heading", { name: "Before you order", level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Message to supplier" })).toBeVisible();
    await expect(page.getByRole("list", { name: "Case progress" }).locator("[aria-current=step]")).toContainText("Evidence report");
    await page.reload();
    await page.getByRole("button", { name: "Change options or context" }).click();
    await expect(page.getByText("From your link")).toHaveCount(usingLink ? 1 : 0);
    await page.getByRole("button", { name: "Resume purchase review" }).click();
    await expect(page.getByRole("heading", { name: "Before you order", level: 1 })).toBeVisible();
  });
}

test("shows scoped requirement decisions and saves an effect-only change to an earlier answer", async ({ page }) => {
  const finish = "Screen printing is required.";
  const answerText = "  Matte would work for this one.  ";
  const initial: ResolvedRequirement = {
    requirement_id: "finish", statement: finish, status: "active", option_ids: ["v0", "v1"],
    provenance: [{ input_ref: "context", quote: finish, question_text: null, action: "add", option_ids: ["v0", "v1"] }],
  };
  const round: QuestionRound = {
    round_id: "revisit", round_number: 1, reason: "Revisit the finish by option.", questions: [
      {
        question_id: "revisit-a", prompt: "What finish works for this option?", why_ask: "This only changes Option A.",
        option_ids: ["v0"], source_ids: [], choices: ["The original finish still works.", "Any finish works."],
        requirement_ids: ["finish"], requirement_targets: [{ ...initial, option_ids: ["v0"] }],
        choice_effects: [
          { choice: "The original finish still works.", action: "keep", requirement_id: "finish", statement: null },
          { choice: "Any finish works.", action: "waive", requirement_id: "finish", statement: null },
        ],
      },
      {
        question_id: "revisit-b", prompt: "Would you change the finish for Option B?", why_ask: "This only changes Option B.",
        option_ids: ["v1"], source_ids: [], choices: [], requirement_ids: ["finish"],
        requirement_targets: [{ ...initial, option_ids: ["v1"] }], choice_effects: [],
      },
      {
        question_id: "texture-c", prompt: "What texture works for Option C?", why_ask: "This only changes Option C.",
        option_ids: ["v6"], source_ids: [], choices: [], requirement_ids: [], choice_effects: [],
      },
    ],
    answers: [
      { question_id: "revisit-a", answer_text: answerText, requirement_effect: { action: "keep", requirement_id: "finish" } },
      { question_id: "revisit-b", answer_text: null },
      { question_id: "texture-c", answer_text: "Maybe matte." },
    ],
  };
  const confirmed: ResolvedRequirement = {
    ...initial, requirement_id: "finish-a", option_ids: ["v0"],
    provenance: [...initial.provenance, {
      input_ref: "answer:revisit-a", quote: answerText, question_text: round.questions[0]!.prompt,
      action: "keep", option_ids: ["v0"],
    }],
  };
  const waived: ResolvedRequirement = {
    ...initial, requirement_id: "order-c", statement: "Any order type is acceptable.", status: "waived", option_ids: ["v6"],
    provenance: [{ input_ref: "answer:order-c", quote: "Any order type is acceptable.",
      question_text: "Do you need ready-made stock?", action: "waive", option_ids: ["v6"] }],
  };
  const unresolved: ResolvedRequirement = {
    ...initial, requirement_id: "texture-c", statement: "Maybe matte.", status: "unresolved", option_ids: ["v6"],
    provenance: [{ input_ref: "answer:texture-c", quote: "Maybe matte.",
      question_text: round.questions[2]!.prompt, action: "unresolved", option_ids: ["v6"] }],
  };
  let saved = reviewed(skipReport, {
    rounds: [round], requirements: [{ ...initial, option_ids: ["v1"] }, confirmed, waived, unresolved],
  });
  const originalRevision = saved.revision;

  await mockAnswerImpact(page);
  await page.route("**/api/brief", (route) => {
    const { operation_id, submission } = route.request().postDataJSON();
    saved = prepared(saved, operation_id, {
      revision: originalRevision + 1,
      rounds: [{ ...round, answers: submission.answers }],
      requirements: saved.requirements!.map((requirement) => requirement.requirement_id === confirmed.requirement_id
        ? { ...confirmed, statement: answerText, provenance: confirmed.provenance.map((entry) =>
          entry.action === "keep" ? { ...entry, action: "replace" } : entry) } : requirement),
    });
    return route.fulfill({ json: ready(saved) });
  });
  await page.route("**/api/reviews", (route) => {
    const { operation_id, revision } = route.request().postDataJSON();
    const nextReport = { ...skipReport, revision, supplier_draft: "Could you confirm the finish for Option A?" };
    saved = { ...saved, phase: "reviewed", report: nextReport,
      last_operation: { ...saved.last_operation!, stage: "review", operation_id },
    };
    return route.fulfill({ json: ready(saved) });
  });
  await openWorkspace(page, () => saved);

  const decisions = page.getByRole("region", { name: "Your requirements by option" });
  const optionA = decisions.getByRole("region", { name: "Option A", exact: true });
  const optionB = decisions.getByRole("region", { name: "Option B", exact: true });
  const optionC = decisions.getByRole("region", { name: "Option C", exact: true });
  await expect(optionA.locator("summary").filter({ hasText: finish })).toBeVisible();
  await expect(optionA.getByText("Question skipped", { exact: false })).toHaveCount(0);
  await expect(optionB.locator("summary").filter({ hasText: "Required" })).toHaveText(`Required${finish}`);
  await expect(optionB.getByText(round.questions[1]!.prompt)).toHaveCount(0);
  await expect(optionC.locator("summary").filter({ hasText: "Waived" })).toBeVisible();
  await expect(optionC.locator("summary").filter({ hasText: "Decision open · Answer left open" })).toBeVisible();
  await optionA.locator("summary").filter({ hasText: finish }).click();
  await expect(optionA.getByRole("list", { name: "Decision history" }).getByText("Added · Your purchase context")).toBeVisible();
  await expect(optionA.locator("blockquote").last()).toHaveText(answerText);

  await page.getByRole("link", { name: "Edit answers" }).click();
  const history = page.getByRole("region", { name: "Saved answers" });
  await history.getByText("Round 1 answers", { exact: true }).click();
  await expect(history.getByText(round.questions[1]!.prompt)).toBeVisible();
  await expect(history.getByText("Skipped / unknown", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Edit round 1" }).click();
  const targetQuestion = page.getByRole("group", { name: round.questions[0]!.prompt });
  await expect(targetQuestion.getByRole("radio", { name: `Keep “${finish}”` })).toBeVisible();
  await expect(targetQuestion.getByRole("radio", { name: `Remove “${finish}”` })).toBeVisible();
  await expect(targetQuestion.getByRole("textbox", { name: "Your answer" })).toHaveValue(answerText);
  const effect = targetQuestion.getByRole("combobox", { name: "How should this answer affect your requirements?" });
  await expect(effect).toHaveValue("keep:finish");
  await effect.selectOption({ label: `Replace “${finish}” with my answer` });
  const request = page.waitForRequest("**/api/brief");
  await page.getByRole("button", { name: "Preview and review brief" }).click();
  await page.getByRole("button", { name: "Save answer changes" }).click();
  expect((await request).postDataJSON()).toMatchObject({
    revision: originalRevision, submission: { answers: [
      { question_id: "revisit-a", answer_text: answerText, requirement_effect: { action: "replace", requirement_id: "finish" } },
      { question_id: "revisit-b", answer_text: null },
      { question_id: "texture-c", answer_text: "Maybe matte." },
    ] },
  });
  await expect(optionA.locator("summary").filter({ hasText: answerText.trim() })).toBeVisible();
  await expect(optionB.locator("summary").filter({ hasText: finish })).toBeVisible();
  await confirmBrief(page).click();
  await expect(page.getByRole("textbox", { name: "Supplier message draft" })).toHaveValue(saved.report!.supplier_draft);
});

test("settles one option's conflicting order instructions from a report with no interview rounds", async ({ page }) => {
  const stock = "Ready-made stock only.";
  const custom = "Custom orders are acceptable.";
  const deadline = "The order must be delivered by 1 December.";
  const stockSource: ResolvedRequirement["provenance"][number] = { input_ref: "context", quote: stock, question_text: null,
    action: "add", option_ids: ["v0", "v1", "v6"] };
  const customSource: ResolvedRequirement["provenance"][number] = {
    ...stockSource, quote: custom, option_ids: ["v0"],
  };
  const conflict: ResolvedRequirement = {
    requirement_id: "order-conflict-a", order_conflict: true, status: "unresolved",
    statement: "Clarify the order type", option_ids: ["v0"],
    provenance: [stockSource, customSource],
  };
  const unaffected: ResolvedRequirement[] = [
    { requirement_id: "stock-bc", statement: stock, status: "active", option_ids: ["v1", "v6"],
      provenance: [stockSource] },
    { requirement_id: "deadline", statement: deadline, status: "active", option_ids: ["v0", "v1", "v6"],
      provenance: [{ ...stockSource, quote: deadline }] },
  ];
  const before = "[Options B, C] Do you have ready-made stock?\n[Options A, B, C] Can you deliver by 1 December?";
  const after = `${before}\n[Option A] If a custom order is needed, what are the lead time and minimum order quantity?`;
  let saved = reviewed({ ...skipReport, revision: 0, findings: [], supplier_draft: before,
    open_preferences: [{ preference_id: `requirement:${conflict.requirement_id}`, text: conflict.statement,
      option_ids: conflict.option_ids, input_refs: ["context"], finding_ids: [], questions: [] }],
  }, { rounds: [], requirements: [conflict, ...unaffected] });
  const requests: unknown[] = [];
  await page.route("**/api/brief", async (route) => {
    const request = route.request().postDataJSON();
    requests.push(request);
    saved = prepared(saved, request.operation_id, { revision: 1,
      rounds: [], requirements: [...unaffected, { requirement_id: "custom-a", statement: custom,
        status: "active", option_ids: ["v0"], provenance: [customSource] }],
    });
    await route.fulfill({ json: ready(saved) });
  });
  await page.route("**/api/reviews", (route) => {
    const { operation_id, revision, confirm } = route.request().postDataJSON();
    expect(confirm).toBe(true);
    saved = { ...saved, phase: "reviewed", report: { ...skipReport, revision, open_preferences: [], supplier_draft: after },
      last_operation: { ...saved.last_operation!, stage: "review", operation_id } };
    return route.fulfill({ json: ready(saved) });
  });
  await openWorkspace(page, () => saved);

  await expect(page.getByRole("textbox", { name: "Supplier message draft" })).toHaveValue(before);
  await page.getByRole("button", { name: "Review buying brief", exact: true }).click();
  const clarification = page.getByRole("region", { name: "Clarify order type for Option A" });
  await expect(clarification.getByText(stock, { exact: true })).toBeVisible();
  await expect(clarification.getByText(custom, { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: /^Clarify order type for Option/ })).toHaveCount(1);
  await expect(page.getByText(conflict.statement, { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Saved answers" })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Supplier message draft" })).toHaveCount(0);
  const save = clarification.getByRole("button", { name: "Save order choice" });
  await expect(save).toBeDisabled();
  await clarification.getByRole("radio", { name: "Custom orders are acceptable" }).check();
  await save.click();
  await expect(clarification).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Supplier message draft" })).toHaveCount(0);
  await confirmBrief(page).click();
  await expect(page.getByRole("textbox", { name: "Supplier message draft" })).toHaveValue(after);
  expect(requests).toEqual([{
    case_id: skipReport.case_id, revision: 0, operation_id: expect.any(String), submission: null,
    order_clarification: { requirement_id: conflict.requirement_id, option_id: "v0", choice: "custom_orders_acceptable" },
  }]);
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Supplier message draft" })).toHaveValue(after);
  expect(requests).toHaveLength(1);
});

test("opens scoped source evidence and compares ordered options while draft edits stay separate", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const [shared, capacity, use] = richReport.findings;
  const claimFor = (sourceId: string) => richReport.claims.find((claim) => claim.source_id === sourceId)!;
  const nameClaim = claimFor("name-v6");
  const imageClaim = claimFor("image-v6");
  const report: PurchaseReview = {
    ...richReport,
    findings: [
      shared!,
      {
        ...capacity!, option_ids: ["v6"], unknowns: [],
        claim_ids: [imageClaim.claim_id],
      },
      use!,
    ],
    comparisons: [
      ...richReport.comparisons.map((comparison) => ({ ...comparison, values: [...comparison.values].reverse() })),
      {
        label: "Saved SKU details",
        shared_claim_ids: [],
        values: richReport.selected_option_ids.map((id) => ({
          option_id: id,
          claim_ids: id === "v6" ? [nameClaim.claim_id, imageClaim.claim_id] : [],
          summary: id === "v6" ? "" : "No SKU-specific evidence cited for this topic.",
          limitations: [],
        })),
      },
    ],
  };
  await openWorkspace(page, () => reviewed(report, {
    buyer_context: contract.input.buyer_context,
    rounds: [{ ...skip.first_round, answers: asSubmission(contract.input.answers) }],
  }));

  await expect(page.getByRole("heading", { name: "Before you order", level: 1 })).toBeFocused();
  const findings = page.getByRole("article");
  await expect(findings.getByRole("heading", { level: 3 })).toHaveText(report.findings.map((finding) => finding.title));
  await expect(findings.first().locator(".review-finding-content")).toBeHidden();
  const firstFindingLink = page.getByRole("link", { name: report.findings[0]!.title, exact: true }).first();
  await firstFindingLink.click();
  await expect(findings.first().locator(".review-finding-content")).toBeVisible();
  for (const row of [findings.nth(1), findings.nth(2)]) {
    await row.getByText("See the evidence", { exact: true }).click();
  }
  await expect(findings.nth(0).locator(".review-scope span")).toHaveText(["Requirement unconfirmed", "Option A", "Option B", "Option C"]);
  await expect(findings.nth(0).locator("blockquote").getByText("Screen printing is required.", { exact: true })).toBeVisible();
  await expect(findings.nth(0).getByText(report.findings[0]!.priority_reason, { exact: false })).toBeVisible();

  const sharedEvidence = findings.nth(0).getByRole("region", { name: "Source evidence" });
  await expect(sharedEvidence.locator("summary").filter({ hasText: / · Option [ABC]$/ })).toHaveText([
    `${claimFor("name-v0").display_text} · Option A`,
    `${claimFor("name-v1").display_text} · Option B`,
    `${nameClaim.display_text} · Option C`,
  ]);
  const methods = sharedEvidence.locator(`[data-claim-id="${claimFor("customization-menu").claim_id}"]`);
  await methods.locator("summary").click();
  const original = snapshot.sources.find((source) => source.source_id === "customization-menu")!;
  await expect(methods.getByText(original.original_text!, { exact: true })).toBeVisible();
  await expect(methods.getByText(original.text_en!, { exact: true })).toBeVisible();
  await expect(methods.getByText("Seller listing claim · listing level", { exact: true })).toBeVisible();
  await expect(findings.nth(1).locator(".review-scope span")).toHaveText(["Listing conflict", "Option C"]);
  await findings.nth(1).getByText(`${imageClaim.display_text} · Option C`, { exact: true }).click();
  const image = findings.nth(1).getByRole("img", { name: "Seller image for option v6" });
  await expect(image).toHaveAttribute("src", `/api/snapshots/${snapshot.snapshot_id}/images/image-v6`);
  await expect.poll(() => image.evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await expect(findings.nth(1).getByRole("img")).toHaveCount(1);
  await findings.nth(2).getByText(claimFor("dmsc-food-contact").display_text, { exact: true }).click();
  await expect(findings.nth(2).getByText("Official guidance · curated reference", { exact: true })).toBeVisible();

  const comparison = page.getByRole("table", { name: "Compare selected options" });
  const capacityRow = comparison.getByRole("row").filter({ has: page.getByRole("rowheader", { name: /^Capacity/ }) });
  const selector = capacityRow.getByRole("rowheader").locator("details");
  await expect(selector.locator("summary")).toHaveText(claimFor("capacity-selector").display_text);
  await selector.locator("summary").click();
  await expect(selector.getByText("720ml", { exact: true })).toBeVisible();
  await expect(comparison.getByRole("columnheader")).toHaveText([
    "Listing detail",
    ...snapshot.options.map((option, index) => new RegExp(`Option ${"ABC"[index]}.*${option.name_original}`)),
  ]);
  const first = report.comparisons[0]!;
  expect(first.values[0]!.option_id).not.toBe("v0");
  await expect(comparison.getByRole("row").nth(1).getByRole("cell").nth(0)).toContainText(claimFor("name-v0").display_text);

  const exactSkuCell = comparison.getByRole("row").filter({
    has: page.getByRole("rowheader", { name: "Saved SKU details" }),
  }).getByRole("cell").nth(2);
  await expect(exactSkuCell.locator("summary")).toHaveText([nameClaim.display_text, imageClaim.display_text]);
  const nameEvidence = exactSkuCell.locator(`details[data-claim-id="${nameClaim.claim_id}"]`);
  const imageEvidence = exactSkuCell.locator(`details[data-claim-id="${imageClaim.claim_id}"]`);
  await nameEvidence.locator("summary").click();
  await expect(nameEvidence.getByText("暮色316-字母粉蓝", { exact: true })).toBeVisible();
  await expect(imageEvidence).not.toHaveAttribute("open");
  await expect(imageEvidence.getByRole("img")).toBeHidden();
  await imageEvidence.locator("summary").click();
  const comparisonImage = imageEvidence.getByRole("img", { name: "Seller image for option v6" });
  await expect(comparisonImage).toHaveAttribute("src", `/api/snapshots/${snapshot.snapshot_id}/images/image-v6`);
  await expect.poll(() => comparisonImage.evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);

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
  await page.getByRole("button", { name: "Message copied" }).click();
  await expect(page.getByRole("alert")).toHaveText("Could not copy the message. Select and copy the text manually.");
  await expect(message).toBeFocused();
  await message.fill(" ");
  await expect(page.getByRole("button", { name: "Copy message" })).toHaveCount(0);
});

test("shows an option's thumbnail limitation beside its available comparison evidence", async ({ page }) => {
  const limitation = "The saved thumbnail for Option A is missing. Its capacity label could not be checked.";
  const base = richReport.comparisons.find((comparison) => comparison.label === "Capacity")!;
  const nameClaim = richReport.claims.find((claim) => claim.source_id === "name-v0")!;
  const report = {
    ...richReport,
    comparisons: [{
      ...base,
      values: base.values.map((value) => value.option_id === "v0"
        ? { ...value, summary: nameClaim.display_text, claim_ids: [nameClaim.claim_id], limitations: [limitation] }
        : value),
    }],
  };
  await page.setViewportSize({ width: 390, height: 844 });
  await openWorkspace(page, () => reviewed(report));
  const comparison = page.getByRole("table", { name: "Compare selected options" });
  const cells = comparison.getByRole("cell");
  const affected = cells.nth(0);
  await expect(affected.getByText("Option A", { exact: true })).toBeVisible();
  await expect(affected.locator("summary")).toHaveText(nameClaim.display_text);
  await expect(affected.getByText(limitation, { exact: true })).toBeVisible();
  await expect(affected.getByText("Thumbnail label: 750 ml", { exact: true })).toHaveCount(0);
  await expect(cells.nth(1).getByText(limitation, { exact: true })).toHaveCount(0);
  await expect(cells.nth(2).getByText(limitation, { exact: true })).toHaveCount(0);
});

test("shows report buyer inputs with the issued questions and their SKU scopes", async ({ page }) => {
  const report: PurchaseReview = {
    ...skipReport,
    buyer_requirements: [{
      requirement_id: "scoped-finish", text: "Screen printing is required.", quote: "Screen printing is required.",
      input_ref: "answer:q-finish", option_ids: ["v1"],
      question_text: "Does the black and white option need screen printing?",
    }],
    open_preferences: [{
      preference_id: "open-use", text: "Your requirements for drink use remain open.",
      input_refs: ["context", "answer:q-use"], finding_ids: [], option_ids: ["v0", "v6"],
      questions: [{
        question_id: "q-use", question_text: "Will you use the first option for hot drinks?", option_ids: ["v0"],
      }],
    }],
  };
  await openWorkspace(page, () => reviewed(report));
  const inputs = page.getByRole("region", { name: "Buyer inputs" });
  await expect(inputs.getByText("Your answer to “Does the black and white option need screen printing?” · Option B")).toBeVisible();
  await expect(inputs.getByText("Your requirements for drink use remain open. · Option A, Option C")).toBeVisible();
  await expect(inputs.getByText("Your purchase context", { exact: true })).toBeVisible();
  await expect(inputs.getByText("Question “Will you use the first option for hot drinks?” · Option A")).toBeVisible();
});

test("guides the buyer to open preferences when there are no findings", async ({ page }) => {
  const preference = {
    preference_id: "open-order", text: "Your preference for stock and customization is still open.",
    input_refs: [], finding_ids: [], option_ids: skipReport.selected_option_ids, questions: [],
  };
  const report = {
    ...skipReport, findings: [], open_preferences: [preference], supplier_draft: "",
    coverage: skipReport.coverage.map((group) => ({ ...group, finding_ids: [] })),
  };
  await openWorkspace(page, () => reviewed(report));
  await expect(page.getByRole("heading", { name: "Before you order", level: 1 })).toBeFocused();
  const guidance = page.getByRole("link", { name: "Review your open preferences." });
  await guidance.click();
  await expect(page).toHaveURL(/#open-preferences-heading$/);
  await expect(page.getByRole("heading", { name: "Open preferences" })).toBeInViewport();
  await expect(page.getByText(preference.text)).toBeInViewport();
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
  await expect(coverage.locator(".review-coverage > li > p:last-child")).toHaveText(
    empty.coverage.map((group) => group.note),
  );
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
  await mockAnswerImpact(page);
  await page.route("**/api/brief", (route) => {
    const { operation_id, revision, submission } = route.request().postDataJSON();
    saved = prepared(saved, operation_id, { revision: revision + 1,
      rounds: [{ ...skip.first_round, answers: submission.answers }],
    });
    return route.fulfill({ json: ready(saved) });
  });
  await page.route("**/api/reviews", (route) => {
    const { operation_id, revision } = route.request().postDataJSON();
    ++writes;
    const operation = {
      operation_id, stage: "review" as const, status: writes === 1 ? "failed" as const : "ready" as const,
      code: writes === 1 ? "invalid_review" : null, message: writes === 1 ? "The review could not be validated." : null,
    };
    const updatedReport = {
      ...skipReport, revision, supplier_draft: "Draft for the changed answers.",
      findings: [...skipReport.findings].reverse(),
    };
    saved = reviewed(updatedReport, {
      phase: writes === 1 ? "failed" : "reviewed", report: writes === 1 ? null : updatedReport,
      rounds: saved.rounds, confirmed_revision: revision, last_operation: operation,
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
  await page.getByRole("button", { name: "Review buying brief", exact: true }).click();
  await confirmBrief(page).click();
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
  const changed = page.waitForRequest("**/api/brief");
  await page.getByRole("button", { name: "Preview and review brief" }).click();
  await page.getByRole("button", { name: "Save answer changes" }).click();
  expect((await changed).postDataJSON()).toMatchObject({
    revision: 1,
    submission: { answers: [{ question_id: "q-finish", answer_text: "Screen printing is required." },
      { question_id: "q-capacity", answer_text: null }, { question_id: "q-use", answer_text: null }] },
  });
  await confirmBrief(page).click();
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
    await confirmBrief(page).click();
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

test("offers explicit regeneration for a saved legacy report and keeps submitted answers", async ({ page }) => {
  const lastOperation = {
    operation_id: "legacy-report", stage: "review" as const, status: "failed" as const,
    code: "review_regeneration_required", message: "This saved review needs to be regenerated. Your answers are saved.",
  };
  let saved = reviewed(skipReport, { phase: "failed", report: null, last_operation: lastOperation });
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST") writes.push(new URL(request.url()).pathname);
  });
  await page.route("**/api/brief", (route) => {
    const { operation_id, revision, submission } = route.request().postDataJSON();
    expect(revision).toBe(saved.revision);
    expect(submission).toBeNull();
    saved = prepared(saved, operation_id);
    return route.fulfill({ json: ready(saved) });
  });
  await page.route("**/api/reviews", (route) => {
    const { operation_id, revision, confirm } = route.request().postDataJSON();
    expect(confirm).toBe(true);
    saved = reviewed(skipReport, { confirmed_revision: revision,
      last_operation: { ...lastOperation, operation_id, status: "ready", code: null, message: null },
    });
    return route.fulfill({ json: ready(saved) });
  });
  await openWorkspace(page, () => saved);
  await expect(page.getByRole("alert")).toHaveText(lastOperation.message);
  await expect(page.getByRole("textbox", { name: "Supplier message draft" })).toHaveCount(0);
  await page.getByText("Round 1 answers", { exact: true }).click();
  await expect(page.getByText("Skipped / unknown", { exact: true })).toHaveCount(3);
  expect(writes).toEqual([]);
  await page.getByRole("button", { name: "Review buying brief", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Review buying brief" })).toBeVisible();
  expect(writes).toEqual(["/api/brief"]);
  await confirmBrief(page).click();
  await expect(page.getByRole("heading", { name: "Before you order", level: 1 })).toBeVisible();
  await page.getByText("Round 1 answers", { exact: true }).click();
  await expect(page.getByText("Skipped / unknown", { exact: true })).toHaveCount(3);
  expect(writes).toEqual(["/api/brief", "/api/reviews"]);
});
