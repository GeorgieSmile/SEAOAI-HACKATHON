import { useCallback, useRef, useState } from "react";
import {
  caseVersion, continueQuestions, getCurrentCase, requestReview, retryCase, startQuestions,
} from "./api";
import type { CaseView, Selection, Submission } from "./api";

type Work = "start" | "continue" | "review" | "retry" | "refresh";
type CaseCommand = (operationId: string, isCurrent: () => boolean) => Promise<CaseView | null>;

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Could not save your progress. Try again.";
}

async function savedInterview(snapshotId: string) {
  const saved = await getCurrentCase();
  if (saved.snapshot?.snapshot_id !== snapshotId) {
    throw new Error("The active listing changed. Reload the page to open the current listing.");
  }
  return saved.case;
}

export function useInterviewSession() {
  const [view, setView] = useState<CaseView | null>(null);
  const [work, setWork] = useState<Work | null>(null);
  const [error, setError] = useState("");
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const activeRequest = useRef(0);
  const pending = useRef(false);
  const unconfirmedOperation = useRef<{ key: string; id: string } | null>(null);

  const restore = useCallback((restored: CaseView | null) => {
    ++activeRequest.current;
    pending.current = false;
    unconfirmedOperation.current = null;
    setWork(null);
    setError("");
    setNeedsRefresh(false);
    setView(restored);
  }, []);

  function suspend() {
    if (!pending.current) return;
    ++activeRequest.current;
    pending.current = false;
    setWork(null);
    setNeedsRefresh(true);
  }

  function operationId(key: string) {
    if (unconfirmedOperation.current?.key === key) return unconfirmedOperation.current.id;
    const id = crypto.randomUUID();
    unconfirmedOperation.current = { key, id };
    return id;
  }

  async function run(
    stage: Exclude<Work, "refresh">, snapshotId: string, key: string, execute: CaseCommand,
    autoReview: boolean, retryId?: string,
  ) {
    const request = ++activeRequest.current;
    const isCurrent = () => activeRequest.current === request;
    let id = retryId ?? operationId(key);
    pending.current = true;
    setWork(stage);
    setError("");
    setNeedsRefresh(false);
    try {
      let result = await execute(id, isCurrent);
      if (!isCurrent() || !result) return null;
      setView(result);
      unconfirmedOperation.current = null;
      if (autoReview && result.phase === "ready_for_review"
        && result.last_operation?.stage !== "review") {
        setWork("review");
        id = operationId(JSON.stringify(["review", caseVersion(result), null]));
        result = await requestReview(result, null, id);
        if (!isCurrent()) return null;
        setView(result);
        unconfirmedOperation.current = null;
      }
      return result;
    } catch (actionError) {
      if (!isCurrent()) return null;
      // A lost response can still leave committed answers or a running operation.
      try {
        const saved = await savedInterview(snapshotId);
        if (!isCurrent()) return null;
        setView(saved);
        if (saved?.last_operation?.operation_id === id) {
          if (saved.last_operation.status === "ready") {
            unconfirmedOperation.current = null;
            return saved;
          }
          if (saved.last_operation.status === "running") return saved;
          setError(saved.last_operation.message || errorMessage(actionError));
        } else {
          setError(errorMessage(actionError));
        }
        return saved;
      } catch (recoveryError) {
        if (isCurrent()) {
          setNeedsRefresh(true);
          setError(errorMessage(recoveryError));
        }
        return null;
      }
    } finally {
      if (isCurrent()) {
        pending.current = false;
        setWork(null);
      }
    }
  }

  function start(selection: Selection) {
    const refreshBeforeStart = pending.current || needsRefresh;
    return run("start", selection.snapshot_id, JSON.stringify(["start", selection, caseVersion(view)]), async (id, isCurrent) => {
      const previous = refreshBeforeStart ? await savedInterview(selection.snapshot_id) : view;
      if (!isCurrent()) return null;
      return startQuestions({ ...selection, previous_case: caseVersion(previous), operation_id: id });
    }, true);
  }

  function submit(current: CaseView, submission: Submission | null, stage: "continue" | "review") {
    if (pending.current || needsRefresh || (stage === "continue" && !submission)) return Promise.resolve(null);
    return run(stage, current.snapshot_id, JSON.stringify([stage, caseVersion(current), submission]), (id) => (
      stage === "continue" && submission
        ? continueQuestions(current, submission, id)
        : requestReview(current, submission, id)
    ), stage === "continue");
  }

  function retry(current: CaseView) {
    const operation = current.last_operation;
    if (pending.current || needsRefresh || operation?.status !== "failed") return;
    void run("retry", current.snapshot_id, "", (id) => retryCase(current, id),
      operation.stage !== "review", operation.operation_id);
  }

  async function refresh(snapshotId: string) {
    if (pending.current) return;
    const request = ++activeRequest.current;
    pending.current = true;
    setWork("refresh");
    setError("");
    try {
      const saved = await savedInterview(snapshotId);
      if (activeRequest.current === request) {
        setView(saved);
        setNeedsRefresh(false);
      }
    } catch (refreshError) {
      if (activeRequest.current === request) {
        setNeedsRefresh(true);
        setError(errorMessage(refreshError));
      }
    } finally {
      if (activeRequest.current === request) {
        pending.current = false;
        setWork(null);
      }
    }
  }

  return { view, work, error, needsRefresh, restore, start, submit, retry, refresh, suspend };
}
