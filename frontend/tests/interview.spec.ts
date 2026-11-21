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
  stage: "start" | "continue" | "review", status: "ready" | "failed" | "running", id = operationId,
) => ({
  stage, status, operation_id: id,
  code: status === "failed" ? "question_failed" : null,
  message: status === "failed" ? "Questions could not be prepared." : null,
});

function view(overrides: Partial<CaseView> = {}): CaseView {
  return {
    case_id: "test-case", revision: 0, snapshot_id: snapshot.snapshot_id,
    selected_option_ids: ["v0", "v1", "v6"], buyer_context: "Resale in Thailand.",
    phase: "awaiting_answers", rounds: [firstRound], reason: null,
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
  await page.route("**/api/reviews", (route) => route.fulfill({ json: response(saved) }));
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
  const unchanged = page.waitForRequest("**/api/reviews");
  await page.getByRole("button", { name: "View purchase review" }).click();
  expect((await unchanged).postDataJSON()).toMatchObject({
    revision: 1, submission: { round_id: "round-1", answers: adaptiveAnswers },
  });
  await expect(page.getByText(/earlier answers are unchanged/i)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Round 2 of 3" })).toBeVisible();
  await expect(page.getByRole("radio", { name: "Ready-made options only." })).toBeChecked();

  await page.getByRole("button", { name: "Edit round 1" }).click();
  await page.getByRole("radio", { name: firstRound.questions[0]!.choices[0]! }).check();
  const edit = page.waitForRequest("**/api/questions/continue");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
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

test("goes straight to review with partial answers, skipped values and exact nonblank free text", async ({ page }) => {
  const writes = trackWrites(page);
  const round = { ...skipped.first_round, answers: null, questions: skipped.first_round.questions.map(
    (question) => question.question_id === "q-use" ? { ...question, choices: [] } : question,
  ) };
  const saved = view({ rounds: [round] });
  await page.route("**/api/reviews", (route) => {
    const { submission, operation_id } = route.request().postDataJSON();
    return route.fulfill({ json: response(view({
      ...saved, phase: "ready_for_review", revision: 1, rounds: [{ ...round, answers: submission.answers }],
      last_operation: operation("review", "ready", operation_id),
    })) });
  });
  await mockSaved(page, () => saved);

  const freeText = page.getByRole("textbox", { name: "Your answer" });
  await expect(freeText).toHaveValue("");
  await page.getByRole("group", { name: round.questions[0]!.prompt }).getByRole("radio", { name: "Not sure / Skip" }).check();
  await freeText.fill("🍵".repeat(1001));
  await expect(page.getByRole("button", { name: "View purchase review" })).toBeDisabled();
  await freeText.fill("  Cold drinks only.  ");
  expect(writes).toEqual([]);
  const review = page.waitForRequest("**/api/reviews");
  await page.getByRole("button", { name: "View purchase review" }).click();
  expect((await review).postDataJSON()).toEqual({
    case_id: "test-case", revision: 0, operation_id: expect.any(String),
    submission: { round_id: "round-1", answers: [
      { question_id: "q-finish", answer_text: null },
      { question_id: "q-capacity", answer_text: null },
      { question_id: "q-use", answer_text: "  Cold drinks only.  " },
    ] },
  });
  await expect(page.getByRole("heading", { name: "Review inputs ready" })).toBeVisible();
  expect(writes).toEqual(["/api/reviews"]);
});

test("automatically hands off zero questions but reloads an existing ready case read-only", async ({ page }) => {
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
  const handoff = page.waitForRequest("**/api/reviews");
  await page.getByRole("button", { name: "Confirm options" }).click();
  expect((await handoff).postDataJSON()).toEqual({
    case_id: "test-case", revision: 0, operation_id: expect.any(String), submission: null,
  });
  await expect(page.getByRole("heading", { name: "Review inputs ready" })).toBeVisible();
  await expect(page.locator(".interview-question")).toHaveCount(0);
  expect(writes).toEqual(["/api/questions", "/api/reviews"]);

  await page.reload();
  await expect(page.getByRole("button", { name: "View purchase review" })).toBeVisible();
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
  expect((await review).postDataJSON()).toMatchObject({ revision: 3, submission: null });
  await expect(page.getByRole("heading", { name: "Review inputs ready" })).toBeVisible();
  await expect(page.locator(".interview-question")).toHaveCount(0);
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
  await expect(page.getByRole("button", { name: "View purchase review" })).toBeEnabled();
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
    saved = view({
      phase: "preparing_questions", revision: 1, rounds: [{ ...firstRound, answers: adaptiveAnswers }],
      last_operation: operation("continue", "running"),
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

  await page.getByRole("button", { name: "Start over" }).click();
  await expect(page.getByRole("group", { name: "Clear the saved listing and answers?" })).toBeVisible();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Round 1 of 3" })).toBeVisible();
  expect(writes).toEqual([]);

  await page.getByRole("button", { name: "Start over" }).click();
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

  await page.getByRole("button", { name: "Start over" }).click();
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
  await page.getByRole("button", { name: "Start over" }).click();
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
