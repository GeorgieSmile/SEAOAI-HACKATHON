import { useCallback, useEffect, useRef, useState } from "react";
import {
  AnswerClarificationRequired, caseVersion, continueQuestions, DemoLocked, getCurrentCase, pendingReview, prepareBrief, requestReview, resumeQuestions,
  retryCase, saveBriefEdit, startQuestions,
} from "./api";
import type { BriefEdit, CaseStage, CaseVersion, CaseView, OrderClarification, Selection, Submission } from "./api";

type Work = "start" | "continue" | "brief" | "review" | "retry" | "refresh";
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

async function savedInterview(snapshotId: string, signal?: AbortSignal) {
  const saved = await getCurrentCase(signal);
  if (saved.snapshot?.snapshot_id !== snapshotId) {
    throw new Error("The active listing changed. Reload the page to open the current listing.");
  }
  return saved.case;
}

export function useInterviewSession(pollReview: boolean) {
  const [view, setView] = useState<CaseView | null>(null);
  const [work, setWork] = useState<Work | null>(null);
  const [error, setError] = useState("");
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const [requiresReload, setRequiresReload] = useState(false);
  const [reportConnectionLost, setReportConnectionLost] = useState(false);
  const activeRequest = useRef(0);
  const pending = useRef(false);
  const unconfirmedOperation = useRef<{ key: string; id: string } | null>(null);
  const lastSent = useRef<SentOperation | null>(null);

  useEffect(() => {
    const status = pendingReview(view);
    if (!view || !status || !pollReview || work || needsRefresh || requiresReload) return;
    const controller = new AbortController();
    const request = activeRequest.current;
    const version = caseVersion(view);
    const operationId = view.last_operation?.operation_id;
    const isCurrent = () => !controller.signal.aborted && activeRequest.current === request;
    let timeout: number;

    function requireReload(message: string) {
      setReportConnectionLost(false);
      setRequiresReload(true);
      setNeedsRefresh(true);
      setError(message);
    }

    async function checkReport() {
      try {
        const { snapshot, case: saved } = await getCurrentCase(controller.signal);
        if (!isCurrent()) return;
        if (snapshot?.snapshot_id !== view?.snapshot_id || !saved || !sameCaseVersion(caseVersion(saved), version)
          || saved.last_operation?.stage !== "review" || saved.last_operation.operation_id !== operationId) {
          requireReload("The saved review changed. Reload saved progress to open it.");
          return;
        }
        setReportConnectionLost(false);
        if (pendingReview(saved) !== status) {
          setView(saved);
          unconfirmedOperation.current = null;
          lastSent.current = null;
          return;
        }
        timeout = window.setTimeout(() => void checkReport(), 1_500);
      } catch (failure) {
        if (!isCurrent()) return;
        if (failure instanceof DemoLocked) {
          requireReload("Your demo access changed. Reload saved progress to unlock the demo.");
          return;
        }
        setReportConnectionLost(true);
        timeout = window.setTimeout(() => void checkReport(), 3_000);
      }
    }

    timeout = window.setTimeout(() => void checkReport(), 1_500);
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [view, pollReview, work, needsRefresh, requiresReload]);

  const restore = useCallback((restored: CaseView | null) => {
    ++activeRequest.current;
    pending.current = false;
    unconfirmedOperation.current = null;
    lastSent.current = null;
    setWork(null);
    setError("");
    setReportConnectionLost(false);
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

  function acceptSavedVersion(saved: CaseView | null, expected: CaseVersion | null) {
    if (canReconcile(saved, expected, lastSent.current)) return true;
    setRequiresReload(true);
    setNeedsRefresh(true);
    setError("The saved interview changed. Reload saved progress before confirming again.");
    return false;
  }

  async function run(
    stage: Exclude<Work, "refresh">, snapshotId: string, execute: CaseCommand,
    retryId?: string,
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
      const result = await execute(bind, isCurrent);
      if (!isCurrent() || !result) return null;
      setView(result);
      unconfirmedOperation.current = null;
      lastSent.current = null;
      return result;
    } catch (actionError) {
      if (!isCurrent()) return null;
      if (actionError instanceof AnswerClarificationRequired) {
        unconfirmedOperation.current = null;
        lastSent.current = null;
        throw actionError;
      }
      // A lost response can still leave committed answers or a running operation.
      try {
        const saved = await savedInterview(snapshotId);
        if (!isCurrent() || !acceptSavedVersion(saved, expected)) return null;
        setView(saved);
        if (id && saved?.last_operation?.operation_id === id) {
          if (saved.last_operation.status === "ready") {
            unconfirmedOperation.current = null;
            return saved;
          }
          if (saved.last_operation.status === "queued" || saved.last_operation.status === "running") return saved;
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
      if (!isCurrent() || !acceptSavedVersion(previous, caseVersion(view))) return null;
      const payload = { ...selection, previous_case: caseVersion(previous) };
      return startQuestions({ ...payload, operation_id: bind(payload, payload.previous_case, "start") });
    });
  }

  function submit(
    current: CaseView, submission: Submission | null, stage: "continue" | "brief", clarification?: OrderClarification,
  ) {
    if (pending.current || needsRefresh || requiresReload || (stage === "continue" && !submission)) return Promise.resolve(null);
    return run(stage, current.snapshot_id, (bind) => {
      const version = caseVersion(current);
      const id = bind([version, submission, clarification], version, stage);
      return stage === "continue" && submission
        ? continueQuestions(current, submission, id)
        : prepareBrief(current, submission, id, clarification);
    });
  }

  function confirm(current: CaseView) {
    if (pending.current || needsRefresh || requiresReload) return;
    void run("review", current.snapshot_id, (bind) =>
      requestReview(current, bind(null, caseVersion(current), "review")));
  }

  function edit(current: CaseView, change: BriefEdit) {
    if (pending.current || needsRefresh || requiresReload) return Promise.resolve(null);
    return run("brief", current.snapshot_id, (bind) =>
      saveBriefEdit(current, change, bind(change, caseVersion(current), "brief")));
  }

  function resume(current: CaseView) {
    if (pending.current || needsRefresh || requiresReload) return;
    void run("continue", current.snapshot_id, (bind) =>
      resumeQuestions(current, bind(null, caseVersion(current), "continue")));
  }

  function retry(current: CaseView) {
    const operation = current.last_operation;
    if (pending.current || needsRefresh || requiresReload || operation?.status !== "failed") return;
    void run("retry", current.snapshot_id, (bind) => retryCase(current, bind(null, caseVersion(current), operation.stage)),
      operation.operation_id);
  }

  async function refresh(snapshotId: string) {
    if (pending.current || requiresReload) return;
    const request = ++activeRequest.current;
    pending.current = true;
    setWork("refresh");
    setError("");
    try {
      const saved = await savedInterview(snapshotId);
      if (activeRequest.current === request && acceptSavedVersion(saved, caseVersion(view))) {
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

  return {
    view, work, error, needsRefresh, requiresReload, reportConnectionLost,
    restore, start, submit, confirm, edit, resume, retry, refresh, suspend,
  };
}
