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

export interface ConfirmSelection extends Selection {
  operation_id: string;
  previous_case: null;
}

type SelectionResult = { status: "confirmed"; selection: Selection } | ApiFailure;

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

export async function getCurrentSnapshot(signal: AbortSignal) {
  const result = await request<{ snapshot: ListingSnapshot | null }>(
    "/api/case", "Could not load the current listing.", { signal },
  );
  if (result.snapshot === undefined) {
    throw new Error("Could not load the current listing.");
  }
  return result.snapshot;
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

export async function confirmSelection(payload: ConfirmSelection) {
  const result = await request<SelectionResult>(
    "/api/selections", "Could not confirm these options.",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  if (result.status !== "confirmed" || !result.selection) {
    throw new Error("Could not confirm these options.");
  }
  return result.selection;
}

export function sourceImageUrl(snapshotId: string, sourceId: string) {
  return `/api/snapshots/${encodeURIComponent(snapshotId)}/images/${encodeURIComponent(sourceId)}`;
}
