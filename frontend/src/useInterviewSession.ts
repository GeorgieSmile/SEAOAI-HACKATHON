import { useCallback, useRef, useState } from "react";
import {
  caseVersion, continueQuestions, getCurrentCase, requestReview, retryCase, startQuestions,
} from "./api";
import type { CaseStage, CaseVersion, CaseView, Selection, Submission } from "./api";

type Work = "start" | "continue" | "review" | "retry" | "refresh";
type BindOperation = (payload: unknown, base: CaseVersion | null, stage: CaseStage) => string;
type CaseCommand = (bindOperation: BindOperation, isCurrent: () => boolean) => Promise<CaseView | null>;

interface SentOperation {
  id: string;
  stage: CaseStage;
  base: CaseVersion | null;
}

function sameCaseVersion(first: CaseVersion | null, second: CaseVersion | null) {
  return first?.case_id === second?.case_id && first?.revision === second?.revision;
}

function canReconcile(saved: CaseView | null, expected: CaseVersion | null, sent: SentOperation | null) {
  if (sameCaseVersion(caseVersion(saved), expected)) return true;
  // An unseen version can only be the direct result of this tab's pending command.
  if (!saved || !sent || !sameCaseVersion(sent.base, expected)
    || saved.last_operation?.operation_id !== sent.id || saved.last_operation.stage !== sent.stage) return false;
  if (sent.stage === "start") return saved.revision === 0;
  return expected !== null && saved.case_id === expected.case_id && saved.revision === expected.revision + 1;
}

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
  const [requiresReload, setRequiresReload] = useState(false);
  const activeRequest = useRef(0);
  const pending = useRef(false);
  const unconfirmedOperation = useRef<{ key: string; id: string } | null>(null);
  const lastSent = useRef<SentOperation | null>(null);

  const restore = useCallback((restored: CaseView | null) => {
    ++activeRequest.current;
    pending.current = false;
    unconfirmedOperation.current = null;
    lastSent.current = null;
    setWork(null);
    setError("");
    setNeedsRefresh(false);
    setRequiresReload(false);
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

  function requireReload() {
    setRequiresReload(true);
    setNeedsRefresh(true);
    setError("The saved interview changed. Reload saved progress before confirming again.");
  }

  async function run(
    stage: Exclude<Work, "refresh">, snapshotId: string, execute: CaseCommand,
    autoReview: boolean, retryId?: string,
  ) {
    const request = ++activeRequest.current;
    const isCurrent = () => activeRequest.current === request;
    let id = retryId;
    let expected = caseVersion(view);
    const bind: BindOperation = (payload, base, wireStage) => {
      id = retryId ?? operationId(JSON.stringify([stage, payload]));
      expected = base;
      lastSent.current = { id, stage: wireStage, base };
      return id;
    };
    pending.current = true;
    setWork(stage);
    setError("");
    setNeedsRefresh(false);
    try {
      let result = await execute(bind, isCurrent);
      if (!isCurrent() || !result) return null;
      setView(result);
      unconfirmedOperation.current = null;
      lastSent.current = null;
      if (autoReview && result.phase === "ready_for_review"
        && result.last_operation?.stage !== "review") {
        setWork("review");
        expected = caseVersion(result);
        id = operationId(JSON.stringify(["review", [expected, null]]));
        lastSent.current = { id, stage: "review", base: expected };
        result = await requestReview(result, null, id);
        if (!isCurrent()) return null;
        setView(result);
        unconfirmedOperation.current = null;
        lastSent.current = null;
      }
      return result;
    } catch (actionError) {
      if (!isCurrent()) return null;
      // A lost response can still leave committed answers or a running operation.
      try {
        const saved = await savedInterview(snapshotId);
        if (!isCurrent()) return null;
        if (!canReconcile(saved, expected, lastSent.current)) {
          requireReload();
          return null;
        }
        setView(saved);
        if (id && saved?.last_operation?.operation_id === id) {
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
    if (requiresReload) return Promise.resolve(null);
    const refreshBeforeStart = pending.current || needsRefresh;
    return run("start", selection.snapshot_id, async (bind, isCurrent) => {
      const previous = refreshBeforeStart ? await savedInterview(selection.snapshot_id) : view;
      if (!isCurrent()) return null;
      if (!canReconcile(previous, caseVersion(view), lastSent.current)) {
        requireReload();
        return null;
      }
      const payload = { ...selection, previous_case: caseVersion(previous) };
      return startQuestions({ ...payload, operation_id: bind(payload, payload.previous_case, "start") });
    }, true);
  }

  function submit(current: CaseView, submission: Submission | null, stage: "continue" | "review") {
    if (pending.current || needsRefresh || requiresReload || (stage === "continue" && !submission)) return Promise.resolve(null);
    return run(stage, current.snapshot_id, (bind) => {
      const version = caseVersion(current);
      const id = bind([version, submission], version, stage);
      return stage === "continue" && submission
        ? continueQuestions(current, submission, id)
        : requestReview(current, submission, id);
    }, stage === "continue");
  }

  function retry(current: CaseView) {
    const operation = current.last_operation;
    if (pending.current || needsRefresh || requiresReload || operation?.status !== "failed") return;
    void run("retry", current.snapshot_id, (bind) => retryCase(current, bind(null, caseVersion(current), operation.stage)),
      operation.stage !== "review", operation.operation_id);
  }

  async function refresh(snapshotId: string) {
    if (pending.current || requiresReload) return;
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

  return { view, work, error, needsRefresh, requiresReload, restore, start, submit, retry, refresh, suspend };
}
