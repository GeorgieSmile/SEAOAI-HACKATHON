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
  linked_option_id?: string | null;
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

export type RequirementAction = "keep" | "replace" | "waive" | "add";

export interface RequirementEffect {
  action: RequirementAction;
  requirement_id: string | null;
}

interface RequirementTarget {
  requirement_id: string;
  statement: string;
  option_ids: string[];
}

export interface Question {
  question_id: string;
  buyer_facet?: "order_type" | "decoration_method" | "surface_finish" | "capacity" | "delivery_deadline" | "other" | null;
  prompt: string;
  why_ask: string;
  option_ids: string[];
  source_ids: string[];
  choices: string[];
  requirement_ids?: string[];
  requirement_targets?: RequirementTarget[];
  choice_effects?: (RequirementEffect & { choice: string; statement: string | null })[];
}

export interface Answer {
  question_id: string;
  answer_text: string | null;
  answer_mode?: "natural";
  requirement_effect?: RequirementEffect | null;
}

export function sameAnswer(saved: Answer | undefined, submitted: Answer) {
  return saved?.answer_text === submitted.answer_text
    && saved.answer_mode === submitted.answer_mode
    && saved.requirement_effect?.action === submitted.requirement_effect?.action
    && saved.requirement_effect?.requirement_id === submitted.requirement_effect?.requirement_id;
}

export interface Submission {
  round_id: string;
  answers: Answer[];
}

export interface OrderClarification {
  requirement_id: string;
  option_id: string;
  choice: "ready_made_only" | "custom_orders_acceptable";
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
  option_ids: string[];
  question_text: string | null;
}

export interface ResolvedRequirement extends RequirementTarget {
  status: "active" | "waived" | "unresolved";
  topic?: string;
  buyer_facet?: Question["buyer_facet"];
  supplier_target?: { kind: string; day?: number; month?: number; year?: number | null } | null;
  delivery_condition?: { follows_deadline: boolean; latest_arrival: ArrivalDate | null };
  order_conflict?: true;
  provenance: {
    input_ref: string;
    quote: string | null;
    question_text: string | null;
    action: RequirementAction | "unresolved";
    option_ids: string[];
  }[];
}

export interface ArrivalDate {
  kind: "delivery_deadline";
  day: number;
  month: number;
  year: number | null;
}

export interface BriefProposal {
  kind: "ready_made" | "custom" | "custom_by_deadline" | "deadline" | "unclear";
  option_ids: string[];
  date: ArrivalDate | null;
}

export interface BriefCorrection extends CaseVersion {
  text: string;
  option_id: string | null;
  resolves_change_id?: string;
}

export interface BriefImpact {
  changed_requirements: ResolvedRequirement[];
  removed_requirements: ResolvedRequirement[];
  discarded_rounds: QuestionRound[];
}

export interface BriefPreview extends BriefImpact {
  proposal: BriefProposal | null;
  question: string | null;
}

export type BriefEdit = { correction: BriefCorrection; proposal: BriefProposal }
  | { undo_change_id: string };

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
    option_ids: string[];
    questions: { question_id: string; question_text: string; option_ids: string[] }[];
  }[];
  comparisons: {
    label: string;
    shared_claim_ids: string[];
    values: { option_id: string; summary: string; claim_ids: string[]; limitations: string[] }[];
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

export type CaseStage = "start" | "continue" | "brief" | "review";

interface CaseOperation {
  operation_id: string;
  stage: CaseStage;
  status: "queued" | "running" | "ready" | "failed";
  code: string | null;
  message: string | null;
  retry_token?: string | null;
}

export interface CaseView extends Selection, CaseVersion {
  rounds: QuestionRound[];
  requirements?: ResolvedRequirement[] | null;
  brief_impact?: BriefImpact;
  brief_changes?: { change_id: string; revision: number; text: string; question?: string }[];
  order_clarifications?: { clarification_id: string; revision?: number }[];
  confirmed_revision?: number;
  can_resume_questions?: boolean;
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

export class AnswerClarificationRequired extends Error {
  constructor(readonly questionId: string, message: string) {
    super(message);
  }
}

async function request<T>(path: string, fallback: string, init?: RequestInit): Promise<T> {
  const response = await fetchResponse(path, init);
  const result = await response.json().catch(() => null);

  if (response.status === 422 && result?.code === "answer_clarification_required"
    && typeof result.question_id === "string" && typeof result.message === "string") {
    throw new AnswerClarificationRequired(result.question_id, result.message);
  }
  if (!response.ok || !result || result.status === "failed") {
    throw new Error(
      typeof result?.message === "string" && result.message ? result.message : fallback,
    );
  }
  return result as T;
}

function fetchResponse(path: string, init?: RequestInit) {
  return fetch(path, init).catch(() => {
    throw new Error("The server could not be reached. Try again.");
  });
}

export async function getCurrentCase(signal?: AbortSignal) {
  const result = await request<SavedCase>(
    "/api/case", "Could not load the current listing.", { signal, cache: "no-store" },
  );
  if (result.snapshot === undefined || result.case === undefined) {
    throw new Error("Could not load the current listing.");
  }
  return result;
}

export async function importListing(source: File | string) {
  let init: RequestInit;
  if (typeof source === "string") {
    init = { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: source }) };
  } else {
    const body = new FormData();
    body.append("file", source);
    init = { method: "POST", body };
  }
  const message = "Could not import the listing. Try again or choose a saved ZIP.";
  const result = await request<IntakeResult>("/api/imports", message, init);
  if (result.status !== "ready" || !result.snapshot) throw new Error(message);
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

export function pendingReview(view: CaseView | null) {
  if (view?.phase !== "generating_review" || view.last_operation?.stage !== "review") return null;
  const status = view.last_operation.status;
  return status === "queued" || status === "running" ? status : null;
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

export function prepareBrief(
  view: CaseView, submission: Submission | null, operationId: string, clarification?: OrderClarification,
) {
  return postCaseCommand("/api/brief", {
    ...caseVersion(view), operation_id: operationId, submission,
    ...(clarification && { order_clarification: clarification }),
  });
}

export function requestReview(view: CaseView, operationId: string) {
  return postCaseCommand("/api/reviews", {
    ...caseVersion(view), operation_id: operationId, confirm: true,
  }).then((result) => checkReviewResponse(result, view, operationId, 0));
}

export function resumeQuestions(view: CaseView, operationId: string) {
  return postCaseCommand("/api/questions/resume", { ...caseVersion(view), operation_id: operationId });
}

function requestBriefPreview<T>(path: string, payload: object) {
  return request<T>(path, "Could not preview the change. Your buying brief is saved.", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function previewCorrection(correction: BriefCorrection) {
  return requestBriefPreview<BriefPreview>("/api/brief/preview", correction);
}

export function previewUndo(view: CaseView, undo_change_id: string) {
  return requestBriefPreview<BriefPreview>("/api/brief/preview-undo", { ...caseVersion(view), undo_change_id });
}

export function previewAnswerEdit(view: CaseView, submission: Submission) {
  return requestBriefPreview<BriefImpact>("/api/brief/preview-answers", {
    ...caseVersion(view), operation_id: crypto.randomUUID(), submission,
  });
}

export function saveBriefEdit(view: CaseView, edit: BriefEdit, operationId: string) {
  return postCaseCommand("/api/brief/edit", { ...caseVersion(view), operation_id: operationId, ...edit });
}

export function retryCase(view: CaseView, operationId: string) {
  const retryToken = view.last_operation?.retry_token;
  return postCaseCommand("/api/case/retry", {
    ...caseVersion(view), operation_id: operationId, ...(retryToken && { retry_token: retryToken }),
  }).then((result) => view.last_operation?.stage === "review"
    ? checkReviewResponse(result, view, operationId, 0) : result);
}

function checkReviewResponse(result: CaseView, base: CaseView, operationId: string, revisionChange: number) {
  const operation = result.last_operation;
  // A second tab can confirm the same revision and receive the canonical saved operation.
  const confirmedOperation = revisionChange === 0 && result.confirmed_revision === base.revision
    && operation?.stage === "review" && Boolean(operation.operation_id);
  const cached = confirmedOperation && operation.status === "ready";
  const wrongVersion = result.case_id !== base.case_id || result.snapshot_id !== base.snapshot_id
    || result.revision !== base.revision + revisionChange;
  const rejectsPending = result.phase === "generating_review" && (!pendingReview(result) || wrongVersion || !confirmedOperation
    || result.buyer_context !== base.buyer_context || result.selected_option_ids.length !== base.selected_option_ids.length
    || !result.selected_option_ids.every((id, index) => id === base.selected_option_ids[index]));
  if ((result.phase === "reviewed" && (!currentPurchaseReview(result) || wrongVersion || (operation?.operation_id !== operationId && !cached)))
    || rejectsPending) {
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
