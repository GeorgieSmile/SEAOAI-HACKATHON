import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { Answer, CaseView, QuestionRound } from "../src/api";
import flow from "../src/fixtures/integration-flow.json" with { type: "json" };

const snapshot = flow.snapshot;
const traces = flow.cases.find((candidate) => candidate.id === "adaptive-screen")!;
const skipped = flow.cases.find((candidate) => candidate.id === "prepare-skipped")!;
const firstRound: QuestionRound = { ...traces.first_round, answers: null };
const followUp = traces.steps.find((step) => step.expected_next_round)?.expected_next_round;
if (!followUp) throw new Error("The linked fixture must contain a second round.");
const secondRound: QuestionRound = { ...followUp, answers: null };
const adaptiveAnswers: Answer[] = [
  { question_id: "q-finish", answer_text: "Screen printing is required." },
  { question_id: "q-use", answer_text: null },
];
const operationId = "b75ee57b-a4a5-4d9a-ab4f-20a66f71356f";
const operation = (
  stage: "start" | "continue" | "brief" | "review", status: "ready" | "failed" | "running", id = operationId,
) => ({
  stage, status, operation_id: id,
  code: status === "failed" ? "question_failed" : null,
  message: status === "failed" ? "Questions could not be prepared." : null,
});

function view(overrides: Partial<CaseView> = {}): CaseView {
  return {
    case_id: "test-case", revision: 0, snapshot_id: snapshot.snapshot_id,
    selected_option_ids: ["v0", "v1", "v6"], buyer_context: "Resale in Thailand.",
    phase: "awaiting_answers", rounds: [firstRound], reason: null, report: null,
    last_operation: operation("start", "ready"), ...overrides,
  };
}

const response = (saved: CaseView) => ({ status: "ready", case: { ...saved, report: null } });

async function mockSaved(page: Page, saved: () => CaseView | null, hasSnapshot = () => true) {
  await page.route("**/api/case", (route) => route.fulfill({
    json: { snapshot: hasSnapshot() ? snapshot : null, case: saved() },
  }));
  await page.route("**/api/snapshots/*/images/*", (route) => route.fulfill({ status: 404 }));
  await page.goto("/");
}

function trackWrites(page: Page) {
  const writes: string[] = [];
  page.on("request", (request) => {
    const { pathname } = new URL(request.url());
    if (request.method() !== "GET" && pathname.startsWith("/api/")) writes.push(pathname);
  });
  return writes;
}

test("continues one whole round, restores its history and replaces later questions only after an edit", async ({ page }) => {
  let saved = view();
  const writes = trackWrites(page);
  await page.route("**/api/questions/continue", (route) => {
    const request = route.request().postDataJSON();
    const newAnswers = request.submission.answers as Answer[];
    const initialSubmission = request.revision === 0;
    saved = view({
      revision: initialSubmission ? 1 : 2,
      rounds: [
        { ...firstRound, answers: newAnswers },
        initialSubmission ? secondRound : {
          ...secondRound,
          round_id: "replacement-round",
          questions: secondRound.questions.map((question) => ({
            ...question, question_id: "q-updated", prompt: "Which applied finish would you consider?",
          })),
        },
      ],
      last_operation: operation("continue", "ready", request.operation_id),
    });
    return route.fulfill({ json: response(saved) });
  });
  await page.route("**/api/brief", (route) => route.fulfill({ json: response(saved) }));
  await page.route("**/api/brief/preview-answers", (route) => route.fulfill({
    json: { changed_requirements: [], removed_requirements: [], discarded_rounds: [] },
  }));
  await mockSaved(page, () => saved);

  await expect(page.getByRole("heading", { name: "Round 1 of 3" })).toBeVisible();
  await expect(page.getByText(firstRound.reason)).toBeVisible();
  await expect(page.getByText(firstRound.questions[0]!.why_ask)).toBeVisible();
  await expect(page.getByRole("group", { name: firstRound.questions[0]!.prompt })).toBeVisible();
  await expect(page.getByRole("group", { name: firstRound.questions[1]!.prompt })).toBeVisible();
  await expect(page.locator(".interview-question input:checked")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Confirm what you’re buying" })).toHaveCount(0);
  expect(writes).toEqual([]);

  await page.getByRole("radio", { name: "Screen printing is required." }).check();
  expect(writes).toEqual([]);
  const continued = page.waitForRequest("**/api/questions/continue");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  expect((await continued).postDataJSON()).toEqual({
    case_id: "test-case", revision: 0, operation_id: expect.any(String),
    submission: { round_id: "round-1", answers: adaptiveAnswers },
  });
  await expect(page.getByRole("heading", { name: "Round 2 of 3" })).toBeVisible();
  expect(writes).toEqual(["/api/questions/continue"]);

  await page.reload();
  await expect(page.getByRole("heading", { name: "Round 2 of 3" })).toBeVisible();
  await page.getByText("Round 1 answers", { exact: true }).click();
  await expect(page.getByText("Screen printing is required.", { exact: true })).toBeVisible();
  await expect(page.getByText("Skipped / unknown", { exact: true })).toBeVisible();
  expect(writes).toEqual(["/api/questions/continue"]);

  await page.getByRole("radio", { name: "Ready-made options only." }).check();
  await page.getByRole("button", { name: "Edit round 1" }).click();
  await expect(page.getByRole("radio", { name: "Screen printing is required." })).toBeChecked();
  const unchanged = page.waitForRequest("**/api/brief");
  await page.getByRole("button", { name: "Preview and review brief" }).click();
  await page.getByRole("button", { name: "Save answer changes" }).click();
  expect((await unchanged).postDataJSON()).toMatchObject({
    revision: 1, submission: { round_id: "round-1", answers: adaptiveAnswers },
  });
  await expect(page.getByText(/earlier answers are unchanged/i)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Round 2 of 3" })).toBeVisible();
  await expect(page.getByRole("radio", { name: "Ready-made options only." })).toBeChecked();

  await page.getByRole("button", { name: "Edit round 1" }).click();
  await page.getByRole("radio", { name: firstRound.questions[0]!.choices[0]! }).check();
  const edit = page.waitForRequest("**/api/questions/continue");
  await page.getByRole("button", { name: "Preview answer changes" }).click();
  await page.getByRole("button", { name: "Save answer changes" }).click();
  expect((await edit).postDataJSON()).toMatchObject({
    revision: 1, submission: {
      round_id: "round-1",
      answers: [
        { question_id: "q-finish", answer_text: firstRound.questions[0]!.choices[0] },
        { question_id: "q-use", answer_text: null },
      ],
    },
  });
  await expect(page.getByRole("group", { name: "Which applied finish would you consider?" })).toBeVisible();
  await expect(page.locator(".interview-question input:checked")).toHaveCount(0);
});

test("brings a rejected answer into view on mobile and retains the answer for correction", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 850 });
  const error = "The options named in your answer must match the options shown for this question.";
  const question = {
    ...firstRound.questions[1]!, prompt: "What order type applies to all selected options?",
    option_ids: view().selected_option_ids, choices: [], requirement_ids: [], choice_effects: [],
  };
  const saved = view({ rounds: [{ ...firstRound, questions: [firstRound.questions[0]!, question] }] });
  await page.route("**/api/questions/continue", (route) => route.fulfill({
    status: 422, json: { status: "failed", code: "invalid_input", message: error },
  }));
  await mockSaved(page, () => saved);

  const field = page.getByRole("group", { name: question.prompt });
  const answer = field.getByRole("textbox", { name: "Your answer" });
  const text = "For Option A, custom orders are acceptable.";
  await answer.fill(text);
  await field.getByRole("combobox").selectOption({ label: "Add my answer as a requirement" });
  const submit = page.getByRole("button", { name: "Continue", exact: true });
  await submit.click();

  const alert = page.getByRole("alert");
  await expect(alert).toHaveText(error);
  await expect(alert).toBeFocused();
  await expect(alert).toBeInViewport();
  await expect(answer).toHaveValue(text);
  await expect(field.getByRole("combobox")).toHaveValue("add:");
  await expect(submit).toBeEnabled();
});

test("opens the buying brief with partial answers, skipped values and exact nonblank free text", async ({ page }) => {
  const writes = trackWrites(page);
  const round = { ...skipped.first_round, answers: null, questions: skipped.first_round.questions.map(
    (question) => question.question_id === "q-use" ? { ...question, choices: [] } : question,
  ) };
  const saved = view({ rounds: [round] });
  await page.route("**/api/brief", (route) => {
    const { submission, operation_id } = route.request().postDataJSON();
    return route.fulfill({ json: response(view({
      ...saved, phase: "ready_for_review", revision: 1, rounds: [{ ...round, answers: submission.answers }],
      last_operation: operation("brief", "ready", operation_id),
    })) });
  });
  await mockSaved(page, () => saved);

  const freeText = page.getByRole("textbox", { name: "Your answer" });
  await expect(freeText).toHaveValue("");
  await page.getByRole("group", { name: round.questions[0]!.prompt }).getByRole("radio", { name: "Not sure / Skip" }).check();
  await freeText.fill("🍵".repeat(1001));
  await expect(page.getByRole("button", { name: "Review buying brief" })).toBeDisabled();
  await freeText.fill("  Cold drinks only.  ");
  expect(writes).toEqual([]);
  const review = page.waitForRequest("**/api/brief");
  await page.getByRole("button", { name: "Review buying brief" }).click();
  expect((await review).postDataJSON()).toEqual({
    case_id: "test-case", revision: 0, operation_id: expect.any(String),
    submission: { round_id: "round-1", answers: [
      { question_id: "q-finish", answer_text: null },
      { question_id: "q-capacity", answer_text: null },
      { question_id: "q-use", answer_text: "  Cold drinks only.  " },
    ] },
  });
  await expect(page.getByRole("heading", { name: "Review buying brief" })).toBeVisible();
  expect(writes).toEqual(["/api/brief"]);
});

test("shows zero-question brief without starting a report and reloads it read-only", async ({ page }) => {
  const writes = trackWrites(page);
  let saved: CaseView | null = null;
  await page.route("**/api/questions", (route) => {
    const request = route.request().postDataJSON();
    saved = view({
      phase: "ready_for_review", selected_option_ids: request.selected_option_ids,
      buyer_context: request.buyer_context, rounds: [], reason: "The buyer supplied enough detail.",
      last_operation: operation("start", "ready", request.operation_id),
    });
    return route.fulfill({ json: response(saved) });
  });
  await page.route("**/api/reviews", (route) => route.fulfill({
    json: response(view({ ...saved!, last_operation: operation("review", "ready") })),
  }));
  await mockSaved(page, () => saved);

  await page.getByRole("checkbox").nth(2).check();
  await page.getByRole("button", { name: "Confirm options" }).click();
  await expect(page.getByRole("heading", { name: "Review buying brief" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Case progress" }).locator("[aria-current=step]")).toContainText("Buying brief");
  await expect(page).toHaveTitle("Review buying brief · DiliDoo!");
  await expect(page.locator(".interview-question")).toHaveCount(0);
  expect(writes).toEqual(["/api/questions"]);

  await page.reload();
  const confirm = page.getByRole("button", { name: "Confirm brief and generate review" });
  await expect(confirm).toBeVisible();
  expect(writes).toEqual(["/api/questions"]);
  const handoff = page.waitForRequest("**/api/reviews");
  await confirm.evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
  expect((await handoff).postDataJSON()).toEqual({
    case_id: "test-case", revision: 0, operation_id: expect.any(String), confirm: true,
  });
  expect(writes).toEqual(["/api/questions", "/api/reviews"]);
});

test("saves a fully skipped final round and then moves to review without a fourth round", async ({ page }) => {
  const writes = trackWrites(page);
  const thirdRound: QuestionRound = {
    ...secondRound, round_id: "round-3", round_number: 3,
    questions: [{ ...secondRound.questions[0]!, question_id: "q-final" }],
  };
  const saved = view({ revision: 2, rounds: [
    { ...firstRound, answers: adaptiveAnswers },
    { ...secondRound, answers: [{ question_id: "q-custom", answer_text: null }] },
    thirdRound,
  ] });
  let finished = saved;
  await page.route("**/api/questions/continue", (route) => {
    const { submission, operation_id } = route.request().postDataJSON();
    finished = view({
      ...saved, revision: 3, phase: "ready_for_review",
      rounds: [...saved.rounds.slice(0, 2), { ...thirdRound, answers: submission.answers }],
      last_operation: operation("continue", "ready", operation_id),
    });
    return route.fulfill({ json: response(finished) });
  });
  await page.route("**/api/reviews", (route) => route.fulfill({
    json: response(view({ ...finished, last_operation: operation("review", "ready") })),
  }));
  await mockSaved(page, () => saved);

  await expect(page.getByRole("heading", { name: "Round 3 of 3" })).toBeVisible();
  const submission = page.waitForRequest("**/api/questions/continue");
  const review = page.waitForRequest("**/api/reviews");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  expect((await submission).postDataJSON()).toMatchObject({
    revision: 2, submission: { round_id: "round-3", answers: [{ question_id: "q-final", answer_text: null }] },
  });
  await expect(page.getByRole("heading", { name: "Review buying brief" })).toBeVisible();
  await expect(page.locator(".interview-question")).toHaveCount(0);
  expect(writes).toEqual(["/api/questions/continue"]);
  await page.getByRole("button", { name: "Confirm brief and generate review" }).click();
  expect((await review).postDataJSON()).toMatchObject({ revision: 3, confirm: true });
  expect(writes).toEqual(["/api/questions/continue", "/api/reviews"]);
});

test("reconciles a failed continuation, shows saved answers and retries the original operation", async ({ page }) => {
  let saved = view();
  await page.route("**/api/questions/continue", (route) => {
    const request = route.request().postDataJSON();
    saved = view({
      phase: "failed", revision: 1, rounds: [{ ...firstRound, answers: request.submission.answers }],
      last_operation: operation("continue", "failed", request.operation_id),
    });
    return route.fulfill({ json: {
      status: "failed", case_id: saved.case_id, revision: 1, code: "question_failed",
      message: "Questions could not be prepared.",
    } });
  });
  await page.route("**/api/case/retry", (route) => route.fulfill({ json: response(view({
    ...saved, phase: "awaiting_answers", rounds: [...saved.rounds, secondRound],
    last_operation: operation("continue", "ready", saved.last_operation!.operation_id),
  })) }));
  await mockSaved(page, () => saved);

  await page.getByRole("radio", { name: "Screen printing is required." }).check();
  const continueRequest = page.waitForRequest("**/api/questions/continue");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  const original = (await continueRequest).postDataJSON();
  await expect(page.getByRole("alert")).toHaveText("Questions could not be prepared.");
  await expect(page.getByRole("button", { name: "Review buying brief" })).toBeEnabled();
  await page.getByText("Round 1 answers", { exact: true }).click();
  await expect(page.getByText("Screen printing is required.", { exact: true })).toBeVisible();

  const retry = page.waitForRequest("**/api/case/retry");
  await page.getByRole("button", { name: "Retry last request" }).click();
  expect((await retry).postDataJSON()).toEqual({
    case_id: "test-case", revision: 1, operation_id: original.operation_id,
  });
  await expect(page.getByRole("heading", { name: "Round 2 of 3" })).toBeVisible();
});

test("uses the server revision when changing SKUs during an old request", async ({ page }) => {
  let saved = view();
  let release!: () => void;
  const hold = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/questions/continue", async (route) => {
    const { operation_id } = route.request().postDataJSON();
    saved = view({
      phase: "preparing_questions", revision: 1, rounds: [{ ...firstRound, answers: adaptiveAnswers }],
      last_operation: operation("continue", "running", operation_id),
    });
    await hold;
    await route.fulfill({ json: response(view({
      ...saved, case_id: "test-case", phase: "awaiting_answers", selected_option_ids: ["v0", "v1", "v6"],
      rounds: [{ ...firstRound, answers: adaptiveAnswers }, secondRound],
    })) });
  });
  await page.route("**/api/questions", (route) => {
    const request = route.request().postDataJSON();
    saved = view({
      case_id: "replacement-case", selected_option_ids: request.selected_option_ids,
      rounds: [{ ...firstRound, round_id: "new-round", reason: "Only the new selection is active." }],
    });
    return route.fulfill({ json: response(saved) });
  });
  await mockSaved(page, () => saved);

  const delayed = page.waitForRequest("**/api/questions/continue");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await delayed;
  await expect(page.getByRole("status")).toContainText("Saving answers");
  await page.getByRole("button", { name: "Change options or context" }).click();
  await page.getByRole("checkbox").nth(1).uncheck();
  const replacement = page.waitForRequest("**/api/questions");
  await page.getByRole("button", { name: "Confirm options" }).click();
  expect((await replacement).postDataJSON()).toMatchObject({
    previous_case: { case_id: "test-case", revision: 1 }, selected_option_ids: ["v0", "v6"],
  });
  await expect(page.getByText("Only the new selection is active.")).toBeVisible();
  const stale = page.waitForResponse("**/api/questions/continue");
  release();
  await (await stale).finished();
  await page.waitForLoadState("networkidle");
  await expect(page.getByText("Only the new selection is active.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Round 2 of 3" })).toHaveCount(0);
});

test("Start over requires confirmation and clears the listing and case only after success", async ({ page }) => {
  const writes = trackWrites(page);
  let saved: CaseView | null = view({ revision: 1 });
  let hasListing = true;
  await page.route("**/api/case/reset", (route) => {
    saved = null;
    hasListing = false;
    return route.fulfill({ json: { snapshot: null, case: null } });
  });
  await mockSaved(page, () => saved, () => hasListing);

  await page.getByRole("button", { name: "New listing" }).click();
  await expect(page.getByRole("group", { name: "Clear the saved listing and answers?" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Round 1 of 3" })).toBeVisible();
  expect(writes).toEqual([]);

  await page.getByRole("button", { name: "New listing" }).click();
  const reset = page.waitForRequest("**/api/case/reset");
  await page.getByRole("button", { name: "Clear listing and answers" }).click();
  expect((await reset).postDataJSON()).toEqual({
    snapshot_id: snapshot.snapshot_id, previous_case: { case_id: "test-case", revision: 1 },
  });
  await expect(page.getByRole("button", { name: "Choose ZIP", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Round 1 of 3" })).toHaveCount(0);
  expect(writes).toEqual(["/api/case/reset"]);
});

test("a newer case on the same snapshot must be loaded and confirmed before reset", async ({ page }) => {
  const writes = trackWrites(page);
  let saved: CaseView | null = view();
  let hasListing = true;
  await page.route("**/api/case/reset", (route) => {
    saved = null;
    hasListing = false;
    return route.fulfill({ json: { snapshot: null, case: null } });
  });
  await mockSaved(page, () => saved, () => hasListing);

  await page.getByRole("button", { name: "New listing" }).click();
  saved = view({
    case_id: "newer-case", revision: 1, rounds: [{ ...firstRound, reason: "Newer saved progress" }],
  });
  await page.getByRole("button", { name: "Clear listing and answers" }).click();
  await expect(page.getByRole("alert")).toContainText("The saved listing or answers changed.");
  expect(writes).toEqual([]);

  await page.getByRole("button", { name: "Reload saved progress" }).click();
  await expect(page.getByText("Newer saved progress")).toBeVisible();
  expect(writes).toEqual([]);
  await expect(page.getByRole("button", { name: "Clear listing and answers" })).toHaveCount(0);
  await page.getByRole("button", { name: "New listing" }).click();
  const reset = page.waitForRequest("**/api/case/reset");
  await page.getByRole("button", { name: "Clear listing and answers" }).click();
  expect((await reset).postDataJSON()).toEqual({
    snapshot_id: snapshot.snapshot_id, previous_case: { case_id: "newer-case", revision: 1 },
  });
  await expect(page.getByRole("button", { name: "Choose ZIP", exact: true })).toBeVisible();
});

test("retains a draft and resends the identical command after a lost submission", async ({ page }) => {
  const freeTextRound = { ...firstRound, questions: firstRound.questions.map(
    (question) => question.question_id === "q-use" ? { ...question, choices: [] } : question,
  ) };
  let saved = view({ rounds: [freeTextRound] });
  let calls = 0;
  await page.route("**/api/questions/continue", (route) => {
    if (++calls === 1) return route.abort("failed");
    const payload = route.request().postDataJSON();
    saved = view({
      revision: 1, rounds: [{ ...freeTextRound, answers: payload.submission.answers }, secondRound],
      last_operation: operation("continue", "ready", payload.operation_id),
    });
    return route.fulfill({ json: response(saved) });
  });
  await mockSaved(page, () => saved);

  const answer = page.getByRole("textbox", { name: "Your answer" });
  await answer.fill("  For cold drinks only.  ");
  const first = page.waitForRequest("**/api/questions/continue");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  const originalPayload = (await first).postDataJSON();
  await expect(page.getByRole("alert")).toContainText("The server could not be reached.");
  await expect(answer).toHaveValue("  For cold drinks only.  ");

  const second = page.waitForRequest("**/api/questions/continue");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  expect((await second).postDataJSON()).toEqual(originalPayload);
  await expect(page.getByRole("heading", { name: "Round 2 of 3" })).toBeVisible();
});

const staleTabScenarios = [
  { change: "different case", action: "Confirm options" },
  { change: "newer revision", action: "Confirm options" },
  { change: "different case", action: "Resume questions" },
  { change: "newer revision", action: "Resume questions" },
] as const;

for (const { change, action } of staleTabScenarios) {
  test(`requires reload for another tab's ${change} after ${action} during a delayed request`, async ({ page: tabA, context }) => {
    const tabB = await context.newPage();
    let saved = view();
    let release!: () => void;
    const hold = new Promise<void>((resolve) => { release = resolve; });
    const tabAWrites = trackWrites(tabA);

    await context.route("**/api/case", (route) => route.fulfill({
      json: { snapshot, case: saved },
    }));
    await context.route("**/api/snapshots/*/images/*", (route) => route.fulfill({ status: 404 }));
    await context.route("**/api/questions/continue", async (route) => {
      const payload = route.request().postDataJSON();
      if (route.request().frame().page() === tabA) {
        const result = view({
          revision: 1, rounds: [{ ...firstRound, answers: payload.submission.answers }, secondRound],
          last_operation: operation("continue", "ready", payload.operation_id),
        });
        saved = result;
        await hold;
        await route.fulfill({ json: response(result) });
      } else {
        saved = view({
          revision: 2, rounds: [
            { ...firstRound, answers: adaptiveAnswers },
            { ...secondRound, answers: payload.submission.answers },
            { ...secondRound, round_id: "tab-b-round", round_number: 3, reason: "Saved by the other tab.",
              questions: secondRound.questions.map((question) => ({ ...question, question_id: "tab-b-question" })) },
          ],
          last_operation: operation("continue", "ready", payload.operation_id),
        });
        await route.fulfill({ json: response(saved) });
      }
    });
    await context.route("**/api/questions", (route) => {
      const payload = route.request().postDataJSON();
      const fromOtherTab = route.request().frame().page() === tabB;
      saved = view({
        case_id: fromOtherTab ? "tab-b-case" : "reconfirmed-case",
        selected_option_ids: payload.selected_option_ids,
        buyer_context: payload.buyer_context,
        rounds: [{ ...firstRound, reason: fromOtherTab ? "Saved by the other tab." : "Replacement confirmed." }],
        last_operation: operation("start", "ready", payload.operation_id),
      });
      return route.fulfill({ json: response(saved) });
    });

    try {
      await tabA.goto("/");
      const ownRequest = tabA.waitForRequest("**/api/questions/continue");
      await tabA.getByRole("button", { name: "Continue", exact: true }).click();
      await ownRequest;
      await tabA.getByRole("button", { name: "Change options or context" }).click();
      if (action === "Confirm options") await tabA.getByRole("checkbox").nth(1).uncheck();
      await expect.poll(() => saved.revision).toBe(1);

      await tabB.goto("/");
      await expect(tabB.getByRole("heading", { name: "Round 2 of 3" })).toBeVisible();
      if (change === "different case") {
        await tabB.getByRole("button", { name: "Change options or context" }).click();
        await tabB.getByRole("checkbox").nth(2).uncheck();
        await tabB.getByRole("button", { name: "Confirm options" }).click();
      } else {
        await tabB.getByRole("button", { name: "Continue", exact: true }).click();
      }
      await expect(tabB.getByText("Saved by the other tab.")).toBeVisible();
      const newer = saved;

      await tabA.getByRole("button", { name: action }).click();
      await expect(tabA.getByRole("button", { name: "Change options or context" })).toBeDisabled();
      await expect(tabA.getByRole("alert")).toHaveText(
        "The saved interview changed. Reload saved progress before confirming again.",
      );
      await expect(tabA.getByText("Saved by the other tab.")).toHaveCount(0);
      expect(tabAWrites).toEqual(["/api/questions/continue"]);
      await tabA.getByRole("button", { name: "Reload saved progress" }).click();
      await expect(tabA.getByText("Saved by the other tab.")).toBeVisible();
      expect(tabAWrites).toEqual(["/api/questions/continue"]);

      await tabA.getByRole("button", { name: "Change options or context" }).click();
      await tabA.getByRole("checkbox").nth(0).uncheck();
      const replacement = tabA.waitForRequest("**/api/questions");
      await tabA.getByRole("button", { name: "Confirm options" }).click();
      expect((await replacement).postDataJSON().previous_case).toEqual({
        case_id: newer.case_id, revision: newer.revision,
      });
      await expect(tabA.getByText("Replacement confirmed.")).toBeVisible();
    } finally {
      release();
    }
  });
}

test("does not adopt an unseen case when it changes between matching preflight and POST", async ({ page }) => {
  let saved = view();
  let release!: () => void;
  const hold = new Promise<void>((resolve) => { release = resolve; });
  const writes = trackWrites(page);
  await page.route("**/api/questions/continue", async (route) => {
    await hold;
    await route.fulfill({ json: response(view()) });
  });
  await page.route("**/api/questions", (route) => {
    saved = view({
      case_id: "concurrent-case", revision: 2,
      rounds: [{ ...firstRound, reason: "A different tab updated the interview." }],
    });
    return route.fulfill({ status: 409, json: {
      status: "failed", case_id: saved.case_id, revision: saved.revision, code: "stale",
      message: "That request no longer matches the active case.",
    } });
  });
  await mockSaved(page, () => saved);

  try {
    const delayed = page.waitForRequest("**/api/questions/continue");
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await delayed;
    await page.getByRole("button", { name: "Change options or context" }).click();
    await page.getByRole("checkbox").nth(1).uncheck();
    const attempt = page.waitForRequest("**/api/questions");
    await page.getByRole("button", { name: "Confirm options" }).click();
    expect((await attempt).postDataJSON().previous_case).toEqual({ case_id: "test-case", revision: 0 });
    await expect(page.getByRole("alert")).toContainText("The saved interview changed.");
    await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(0);
    expect(writes).toEqual(["/api/questions/continue", "/api/questions"]);

    await page.getByRole("button", { name: "Reload saved progress" }).click();
    await expect(page.getByText("A different tab updated the interview.")).toBeVisible();
    expect(writes).toEqual(["/api/questions/continue", "/api/questions"]);
  } finally {
    release();
  }
});

test("can replace the case created by the current tab's own delayed initial start", async ({ page }) => {
  let saved: CaseView | null = null;
  let release!: () => void;
  const hold = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/questions", async (route) => {
    const payload = route.request().postDataJSON();
    const initial = payload.previous_case === null;
    const result = view({
      case_id: initial ? "own-first-case" : "own-replacement-case",
      selected_option_ids: payload.selected_option_ids,
      buyer_context: payload.buyer_context,
      rounds: [{ ...firstRound, reason: initial ? "Original request." : "New selection in this tab." }],
      last_operation: operation("start", "ready", payload.operation_id),
    });
    saved = result;
    if (initial) await hold;
    await route.fulfill({ json: response(result) });
  });
  await mockSaved(page, () => saved);

  try {
    await page.getByRole("checkbox").nth(0).check();
    await page.getByRole("checkbox").nth(1).check();
    const first = page.waitForRequest("**/api/questions");
    await page.getByRole("button", { name: "Confirm options" }).click();
    expect((await first).postDataJSON().previous_case).toBeNull();
    await expect.poll(() => saved?.case_id).toBe("own-first-case");

    await page.getByRole("button", { name: "Change options or context" }).click();
    await page.getByRole("checkbox").nth(0).uncheck();
    const next = page.waitForRequest("**/api/questions");
    await page.getByRole("button", { name: "Confirm options" }).click();
    expect((await next).postDataJSON()).toMatchObject({
      previous_case: { case_id: "own-first-case", revision: 0 }, selected_option_ids: ["v1"],
    });
    await expect(page.getByText("New selection in this tab.")).toBeVisible();
  } finally {
    release();
  }
});

test("Resume questions loads the same tab's completed round without another write", async ({ page }) => {
  let saved = view();
  let release!: () => void;
  const hold = new Promise<void>((resolve) => { release = resolve; });
  const writes = trackWrites(page);
  await page.route("**/api/questions/continue", async (route) => {
    const request = route.request().postDataJSON();
    const result = view({
      revision: 1, rounds: [{ ...firstRound, answers: request.submission.answers }, secondRound],
      last_operation: operation("continue", "ready", request.operation_id),
    });
    saved = result;
    await hold;
    await route.fulfill({ json: response(result) });
  });
  await mockSaved(page, () => saved);

  try {
    await page.getByRole("radio", { name: "Screen printing is required." }).check();
    const submitted = page.waitForRequest("**/api/questions/continue");
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await submitted;
    await page.getByRole("button", { name: "Change options or context" }).click();
    await expect.poll(() => saved.revision).toBe(1);

    const restored = page.waitForRequest("**/api/case");
    await page.getByRole("button", { name: "Resume questions" }).click();
    expect((await restored).method()).toBe("GET");
    await expect(page.getByRole("heading", { name: "Round 2 of 3" })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await page.getByText("Round 1 answers", { exact: true }).click();
    await expect(page.getByText("Screen printing is required.", { exact: true })).toBeVisible();
    expect(writes).toEqual(["/api/questions/continue"]);
  } finally {
    release();
  }
});

test("explains replacement before importing over saved answers and retrying a failed request", async ({ page }) => {
  const writes = trackWrites(page);
  await mockSaved(page, () => view({
    revision: 1, rounds: [{ ...firstRound, answers: adaptiveAnswers }, secondRound],
  }));
  await expect(page.getByRole("heading", { name: "Round 2 of 3" })).toBeVisible();
  await page.getByRole("button", { name: "Replace listing", exact: true }).click();
  const warning = /clears any saved listing, photos, and answers, even if the new import fails/i;
  await expect(page.getByText(warning)).toBeVisible();
  await expect(page.getByRole("button", { name: "Import link" })).toHaveAccessibleDescription(warning);
  await expect(page.getByRole("button", { name: "Choose another ZIP" })).toHaveAccessibleDescription(warning);
  expect(writes).toEqual([]);

  // The backend can still hold the saved answers if the POST never reaches it.
  await page.route("**/api/imports", (route) => route.abort("connectionfailed"));
  await page.getByLabel("TTP Cargo product link").fill("https://taobao.ttpcargo.com/product?provider=1688&itemId=123");
  await page.getByRole("button", { name: "Import link" }).click();
  await expect(page.getByRole("alert")).toContainText("server could not be reached");
  await expect(page.getByText(warning)).toBeVisible();
  await expect(page.getByRole("button", { name: "Import link" })).toHaveAccessibleDescription(warning);
  await expect(page.getByRole("button", { name: "Choose another ZIP" })).toHaveAccessibleDescription(warning);
});
