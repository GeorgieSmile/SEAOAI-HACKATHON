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
  });
}

export function retryCase(view: CaseView, operationId: string) {
  return postCaseCommand("/api/case/retry", {
    ...caseVersion(view), operation_id: operationId,
  });
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
