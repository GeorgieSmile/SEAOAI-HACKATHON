export interface ListingOption {
  option_id: string;
  name_original: string;
  name_en: string | null;
  image_source_id: string | null;
}

export interface Source {
  source_id: string;
  kind: "listing_text" | "listing_image" | "official_guidance";
  label: string;
  option_ids: string[];
  availability: "available" | "missing" | "unreadable";
  original_text: string | null;
  text_en: string | null;
  image_file: string | null;
  url: string | null;
}

export interface ListingSnapshot {
  snapshot_id: string;
  title: string;
  options: ListingOption[];
  sources: Source[];
  limitations: string[];
}

interface ApiFailure {
  status: "failed";
  code: string;
  message: string;
}

type IntakeResult = { status: "ready"; snapshot: ListingSnapshot } | ApiFailure;

export interface Selection {
  snapshot_id: string;
  selected_option_ids: string[];
  buyer_context: string;
}

export interface CaseVersion {
  case_id: string;
  revision: number;
}

export interface StartQuestions extends Selection {
  operation_id: string;
  previous_case: CaseVersion | null;
}

export interface Question {
  question_id: string;
  prompt: string;
  why_ask: string;
  option_ids: string[];
  source_ids: string[];
  choices: string[];
}

export interface Answer {
  question_id: string;
  answer_text: string | null;
}

export interface Submission {
  round_id: string;
  answers: Answer[];
}

export interface QuestionRound {
  round_id: string;
  round_number: number;
  reason: string;
  questions: Question[];
  answers: Answer[] | null;
}

export interface ReviewRequirement {
  requirement_id: string;
  text: string;
  input_ref: string;
  quote: string;
}

export type ReviewClaim = {
  claim_id: string;
  option_ids: string[];
  source_id: string;
  display_text: string;
} & (
  | { kind: "text_excerpt"; quote: string }
  | { kind: "image_measurement"; value: number; unit: "ml" | "L" | "fl oz" | "mm" | "cm" | "in" | "g" | "kg" | "oz" | "lb" | "°C" | "°F" }
  | {
    kind: "image_detail";
    detail: "letters_visible" | "illustration_visible" | "straw_visible" | "lid_visible" | "handle_visible"
      | "retail_box_visible" | "protective_insert_visible" | "markings_visible";
  }
);

export interface ReviewFinding {
  finding_id: string;
  title: string;
  option_ids: string[];
  kind: "listing_conflict" | "requirement_unconfirmed" | "clarification_needed" | "evidence_gap";
  action_type: "decide_preference" | "ask_supplier" | "request_evidence";
  requirement_ids: string[];
  claim_ids: string[];
  interpretation: string;
  impact: string;
  priority_reason: string;
  unknowns: {
    text: string;
    reason: "not_found" | "not_reviewed" | "unreadable";
    source_ids: string[];
  }[];
  requested_evidence: string;
  evidence_limits: string;
  supplier_question: string | null;
}

export interface PurchaseReview extends CaseVersion {
  contract_version: 2;
  snapshot_id: string;
  selected_option_ids: string[];
  claims: ReviewClaim[];
  buyer_requirements: ReviewRequirement[];
  open_preferences: {
    preference_id: string;
    text: string;
    input_refs: string[];
    finding_ids: string[];
  }[];
  comparisons: {
    label: string;
    shared_claim_ids: string[];
    values: { option_id: string; summary: string; claim_ids: string[] }[];
  }[];
  findings: ReviewFinding[];
  coverage: {
    group: "product_match" | "intended_use" | "supporting_evidence";
    status: "reviewed" | "limited" | "not_assessed";
    finding_ids: string[];
    note: string;
  }[];
  limitations: string[];
  supplier_draft: string;
}

export type CaseStage = "start" | "continue" | "review";

interface CaseOperation {
  operation_id: string;
  stage: CaseStage;
  status: "running" | "ready" | "failed";
  code: string | null;
  message: string | null;
}

export interface CaseView extends Selection, CaseVersion {
  rounds: QuestionRound[];
  phase: "preparing_questions" | "awaiting_answers" | "ready_for_review" | "generating_review" | "reviewed" | "failed";
  reason: string | null;
  report: PurchaseReview | null;
  last_operation: CaseOperation | null;
}

interface SavedCase {
  snapshot: ListingSnapshot | null;
  case: CaseView | null;
}

type CaseResult = { status: "ready"; case: CaseView } | ApiFailure;

async function request<T>(path: string, fallback: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init).catch(() => {
    throw new Error("The server could not be reached. Try again.");
  });
  const result = await response.json().catch(() => null);

  if (!response.ok || !result || result.status === "failed") {
    throw new Error(
      typeof result?.message === "string" && result.message ? result.message : fallback,
    );
  }
  return result as T;
}

export async function getCurrentCase(signal?: AbortSignal) {
  const result = await request<SavedCase>(
    "/api/case", "Could not load the current listing.", { signal },
  );
  if (result.snapshot === undefined || result.case === undefined) {
    throw new Error("Could not load the current listing.");
  }
  return result;
}

export async function uploadListing(file: File) {
  const body = new FormData();
  body.append("file", file);
  const result = await request<IntakeResult>("/api/imports", "Could not read this ZIP file.", {
    method: "POST", body,
  });
  if (result.status !== "ready" || !result.snapshot) {
    throw new Error("Could not read this ZIP file.");
  }
  return result.snapshot;
}

async function postCaseCommand(path: string, payload: object) {
  const result = await request<CaseResult>(
    path, "Could not save your progress. Try again.",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  if (result.status !== "ready" || !result.case) {
    throw new Error("Could not save your progress. Try again.");
  }
  return result.case;
}

export function caseVersion(view: CaseView | null): CaseVersion | null {
  return view ? { case_id: view.case_id, revision: view.revision } : null;
}

export function currentPurchaseReview(view: CaseView | null): PurchaseReview | null {
  const report = view?.report;
  if (view?.phase !== "reviewed" || !report || report.contract_version !== 2
    || report.case_id !== view.case_id || report.revision !== view.revision
    || report.snapshot_id !== view.snapshot_id
    || !Array.isArray(report.selected_option_ids)
    || report.selected_option_ids.length !== view.selected_option_ids.length
    || !report.selected_option_ids.every((id, index) => id === view.selected_option_ids[index])
    || !Array.isArray(report.claims) || !Array.isArray(report.findings) || !Array.isArray(report.comparisons)
    || !Array.isArray(report.buyer_requirements) || !Array.isArray(report.open_preferences)
    || !Array.isArray(report.coverage) || !Array.isArray(report.limitations)
    || typeof report.supplier_draft !== "string") return null;
  return report;
}

export function startQuestions(payload: StartQuestions) {
  return postCaseCommand("/api/questions", payload);
}

export function continueQuestions(view: CaseView, submission: Submission, operationId: string) {
  return postCaseCommand("/api/questions/continue", {
    ...caseVersion(view), operation_id: operationId, submission,
  });
}

export function requestReview(view: CaseView, submission: Submission | null, operationId: string) {
  return postCaseCommand("/api/reviews", {
    ...caseVersion(view), operation_id: operationId, submission,
  }).then((result) => {
    const saved = view.rounds.find((round) => round.round_id === submission?.round_id)?.answers;
    const changed = submission !== null && (!saved || submission.answers.some((answer) => (
      saved.find((prior) => prior.question_id === answer.question_id)?.answer_text !== answer.answer_text
    )));
    return checkReviewResponse(result, view, operationId, changed ? 1 : 0);
  });
}

export function retryCase(view: CaseView, operationId: string) {
  return postCaseCommand("/api/case/retry", {
    ...caseVersion(view), operation_id: operationId,
  }).then((result) => view.last_operation?.stage === "review"
    ? checkReviewResponse(result, view, operationId, 0) : result);
}

function checkReviewResponse(result: CaseView, base: CaseView, operationId: string, revisionChange: number) {
  const resultOperation = result.last_operation?.operation_id;
  const cached = revisionChange === 0 && currentPurchaseReview(base)
    && resultOperation === base.last_operation?.operation_id;
  if (result.phase === "reviewed" && (!currentPurchaseReview(result)
    || result.case_id !== base.case_id || result.snapshot_id !== base.snapshot_id
    || result.revision !== base.revision + revisionChange
    || (resultOperation !== operationId && !cached))) {
    throw new Error("The purchase review could not be matched to your saved answers. Reload saved progress.");
  }
  return result;
}

export async function resetCase(snapshotId: string, previous: CaseVersion | null) {
  const result = await request<SavedCase>("/api/case/reset", "Could not clear the saved listing.", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ snapshot_id: snapshotId, previous_case: previous }),
  });
  if (result.snapshot !== null || result.case !== null) throw new Error("Could not clear the saved listing.");
}

export function sourceImageUrl(snapshotId: string, sourceId: string) {
  return `/api/snapshots/${encodeURIComponent(snapshotId)}/images/${encodeURIComponent(sourceId)}`;
}
