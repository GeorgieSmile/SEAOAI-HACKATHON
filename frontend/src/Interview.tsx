import { useEffect, useRef, useState } from "react";
import { currentPurchaseReview } from "./api";
import type { CaseView, ListingSnapshot, Question, QuestionRound, Submission } from "./api";
import { OptionImage } from "./Listing";
import { PurchaseReview } from "./PurchaseReview";

const MAX_ANSWER_LENGTH = 1_000;

type Draft = string | null | undefined;

function savedAnswer(round: QuestionRound, id: string) {
  return round.answers?.find((answer) => answer.question_id === id)?.answer_text;
}

function normalize(answer: Draft) {
  return answer?.trim() ? answer : null;
}

interface QuestionFieldProps {
  question: Question;
  value: Draft;
  disabled: boolean;
  optionLabels: string[];
  showScope: boolean;
  onChange: (answer: Draft) => void;
}

function QuestionField({ question, value, disabled, optionLabels, showScope, onChange }: QuestionFieldProps) {
  const noteId = `${question.question_id}-why`;
  const length = [...(value ?? "")].length;
  return (
    <fieldset className="interview-question" aria-describedby={noteId} disabled={disabled}>
      <legend>{question.prompt}</legend>
      <p id={noteId} className="question-why"><strong>Why we ask · </strong>{question.why_ask}</p>
      {showScope && <p className="question-scope">Applies to {optionLabels.join(" · ")}</p>}
      {question.choices.length ? (
        <div className="question-choices">
          {question.choices.map((choice) => (
            <label key={choice} className="question-choice">
              <input
                type="radio" name={question.question_id} checked={value === choice}
                onChange={() => onChange(choice)}
              />
              {choice}
            </label>
          ))}
          <label className="question-choice skip-choice">
            <input type="radio" name={question.question_id} checked={value === null} onChange={() => onChange(null)} />
            Not sure / Skip
          </label>
        </div>
      ) : (
        <div className="free-answer">
          <label htmlFor={`${question.question_id}-answer`}>Your answer</label>
          <textarea
            id={`${question.question_id}-answer`}
            value={value ?? ""}
            rows={3}
            placeholder="Type what you know"
            aria-invalid={length > MAX_ANSWER_LENGTH || undefined}
            aria-describedby={`${question.question_id}-length`}
            onChange={(event) => onChange(event.currentTarget.value)}
          />
          <div className="free-answer-meta">
            <p id={`${question.question_id}-length`} className={length > MAX_ANSWER_LENGTH ? "error" : "muted"}>
              {length > MAX_ANSWER_LENGTH ? "Use 1,000 characters or fewer. " : ""}
              {length.toLocaleString("en-US")} / 1,000
            </p>
            <button className="button button-secondary" type="button" aria-pressed={value === null}
              onClick={() => onChange(value === null ? "" : null)}>
              Not sure / Skip
            </button>
          </div>
        </div>
      )}
    </fieldset>
  );
}

function RoundHistory({ round, disabled, onEdit }: {
  round: QuestionRound;
  disabled: boolean;
  onEdit: () => void;
}) {
  return (
    <div className="round-history">
      <details>
        <summary>Round {round.round_number} answers</summary>
        <dl>
          {round.questions.map((question) => (
            <div key={question.question_id}>
              <dt>{question.prompt}</dt>
              <dd>{savedAnswer(round, question.question_id) ?? "Skipped / unknown"}</dd>
            </div>
          ))}
        </dl>
      </details>
      <button className="button button-secondary" type="button" disabled={disabled}
        aria-label={`Edit round ${round.round_number}`} onClick={onEdit}>
        Edit
      </button>
    </div>
  );
}

function InterviewSelection({ snapshot, selected, context }: {
  snapshot: ListingSnapshot;
  selected: string[];
  context: string;
}) {
  return (
    <aside className="interview-selection" aria-labelledby="interview-selection-title">
      <h3 id="interview-selection-title">Selected options</h3>
      <ol className="selected-options">
        {selected.map((id, index) => {
          const option = snapshot.options.find((candidate) => candidate.option_id === id);
          if (!option) return null;
          const source = snapshot.sources.find((source) => source.source_id === option.image_source_id);
          return (
            <li key={id}>
              <span className="summary-index" aria-hidden="true">{index + 1}</span>
              <div className="summary-image">
                <OptionImage option={option} imageSource={source} snapshotId={snapshot.snapshot_id} />
              </div>
              <div>
                {option.name_en && <strong>{option.name_en}</strong>}
                <span className="original" lang="zh-Hans">{option.name_original}</span>
              </div>
            </li>
          );
        })}
      </ol>
      {context.trim() && <div className="interview-context"><h4>Buyer context</h4><p>{context}</p></div>}
    </aside>
  );
}

interface Props {
  hidden: boolean;
  onReportVisibilityChange: (visible: boolean) => void;
  snapshot: ListingSnapshot;
  selected: string[];
  context: string;
  view: CaseView | null;
  busy: boolean;
  blocked: boolean;
  workingMessage: string;
  error: string;
  needsRefresh: boolean;
  onChangeSelection?: () => void;
  onStartAgain: () => void;
  onRetry: () => void;
  onRefresh: () => void;
  onSubmit: (submission: Submission | null, action: "continue" | "review") => Promise<CaseView | null>;
}

export function Interview({
  hidden, onReportVisibilityChange, snapshot, selected, context, view, busy, blocked, workingMessage, error, needsRefresh,
  onChangeSelection, onStartAgain, onRetry, onRefresh, onSubmit,
}: Props) {
  const [drafts, setDrafts] = useState<Record<string, string | null>>({});
  const [editingRound, setEditingRound] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [draftEdit, setDraftEdit] = useState<{ revision: number; text: string } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const openRound = view?.rounds.find((round) => round.answers === null);
  const edited = view?.rounds.find((round) => round.round_id === editingRound);
  const activeRound = edited ?? openRound;
  const running = view?.last_operation?.status === "running";
  const locked = busy || blocked || running || needsRefresh;
  const failed = view?.last_operation?.status === "failed";
  const report = currentPurchaseReview(view);
  const reportInvalid = view?.phase === "reviewed" && !report;
  const showReport = Boolean(report && !hidden && !activeRound && !locked);
  const reviewPrepared = view?.phase === "ready_for_review"
    && view.last_operation?.stage === "review" && view.last_operation.status === "ready";
  const reviewFailed = failed && view?.last_operation?.stage === "review";
  const needsRegeneration = reviewFailed && view?.last_operation?.code === "review_regeneration_required";

  useEffect(() => {
    onReportVisibilityChange(showReport);
  }, [onReportVisibilityChange, showReport]);

  useEffect(() => {
    if (!hidden) heading.current?.focus();
  }, [hidden, activeRound?.round_id, showReport]);

  function answerValue(round: QuestionRound, id: string): Draft {
    return Object.hasOwn(drafts, id) ? drafts[id] : savedAnswer(round, id);
  }

  function setAnswer(id: string, answer: Draft) {
    setDrafts((existing) => {
      const next = { ...existing };
      if (answer === undefined) delete next[id];
      else next[id] = answer;
      return next;
    });
  }

  const invalidAnswer = activeRound?.questions.some(
    (question) => [...(answerValue(activeRound, question.question_id) ?? "")].length > MAX_ANSWER_LENGTH,
  );

  async function submitAnswers(action: "continue" | "review") {
    if (locked || invalidAnswer) return;
    setNotice("");
    const submission = activeRound ? {
      round_id: activeRound.round_id,
      answers: activeRound.questions.map((question) => ({
        question_id: question.question_id,
        answer_text: normalize(answerValue(activeRound, question.question_id)),
      })),
    } : null;
    const result = await onSubmit(submission, action);
    if (submission && result && result.case_id === view?.case_id && result.rounds.some((round) => (
      round.round_id === submission.round_id && round.answers !== null
      && submission.answers.every((answer) => savedAnswer(round, answer.question_id) === answer.answer_text)
    ))) {
      if (edited && action === "review" && result.rounds.some((round) => round.answers === null)) {
        setNotice("Your earlier answers are unchanged. Answer or skip the open round to proceed to the purchase review.");
      }
      setEditingRound(null);
      setDrafts((existing) => {
        const next = { ...existing };
        for (const answer of submission.answers) delete next[answer.question_id];
        return next;
      });
    }
  }

  function cancelEdit() {
    setEditingRound(null);
    if (edited) {
      setDrafts((existing) => {
        const next = { ...existing };
        for (const question of edited.questions) delete next[question.question_id];
        return next;
      });
    }
  }

  const history = view && view.rounds.some((round) => round.answers !== null && round.round_id !== edited?.round_id) && (
    <section aria-labelledby="history-title" className="interview-history">
      <h2 id="history-title" tabIndex={-1}>Saved answers</h2>
      {view.rounds
        .filter((round) => round.answers !== null && round.round_id !== edited?.round_id)
        .map((round) => <RoundHistory round={round} key={round.round_id} disabled={locked}
          onEdit={() => {
            setNotice("");
            setEditingRound(round.round_id);
          }} />)}
    </section>
  );

  return (
    <section className="interview-shell" aria-labelledby="interview-title" hidden={hidden}>
      <div className="interview-header">
        <div><span className="eyebrow">Step {showReport ? "3 of 3 · Purchase review" : "2 of 3"}</span>
        <h1 id="interview-title" tabIndex={-1} ref={heading}>
          {showReport && report ? `${report.findings.length} ${report.findings.length === 1 ? "thing" : "things"} to resolve before you order` : reviewPrepared && !edited ? "Review inputs ready" : "Clarify your purchase"}
        </h1>
        <p className="muted">{showReport ? "Check each finding against its sources before contacting the seller." : "A few questions about what you need. Answer what you know, skip the rest."}</p></div>
        <div className="interview-actions">
          {showReport && history && <a className="button button-secondary" href="#history-title"
            onClick={() => document.getElementById("history-title")?.focus()}>Edit answers</a>}
          <button className="button button-secondary" type="button" disabled={!onChangeSelection} onClick={onChangeSelection}>
            Change options or context
          </button>
        </div>
      </div>

      {showReport && report && view ? (
        <>
          <PurchaseReview key={report.revision} report={report} snapshot={snapshot}
            supplierDraft={draftEdit?.revision === report.revision ? draftEdit.text : report.supplier_draft}
            onChangeDraft={(text) => setDraftEdit({ revision: report.revision, text })} />
          {history}
        </>
      ) : (
        <div className="interview-layout">
          <div className="interview-main">
            {busy && <p className="interview-status" role="status">{workingMessage}</p>}
            {notice && !busy && <p className="interview-status" role="status">{notice}</p>}
            {(error || (failed && !busy)) && (
              <p className="interview-error" role="alert">
                {error || view?.last_operation?.message || "Could not prepare the next step."}
              </p>
            )}
            {reportInvalid && !busy && !error && (
              <p className="interview-error" role="alert">The purchase review could not be matched to your saved answers.</p>
            )}
            {(running || needsRefresh || reportInvalid) && !busy && (
              <div className="interview-status">
                <p>{running ? "Your last request is still running." : "Reload your saved progress before submitting again."}</p>
                <button className="button button-secondary" type="button" disabled={blocked} onClick={onRefresh}>
                  Reload saved progress
                </button>
              </div>
            )}
            {history}
            {activeRound && (
              <form className="interview-round" onSubmit={(event) => {
                event.preventDefault();
                void submitAnswers("continue");
              }}>
                <h3>{edited ? "Edit round" : "Round"} {activeRound.round_number} of 3</h3>
                <div className="segments" aria-hidden="true">{[1, 2, 3].map(n => <i key={n} className={n <= activeRound.round_number ? "filled" : ""} />)}</div>
                <p className="round-reason">{activeRound.reason}</p>
                {edited && <p className="muted">{report
                  ? "Changing these answers replaces the purchase review and supplier draft. Later rounds may change."
                  : "Changing these answers can replace later rounds."}</p>}
                {!edited && <p className="muted">Answer what you know. You can leave any question blank.</p>}
                <div className="interview-questions">
                  {activeRound.questions.map((question) => (
                    <QuestionField
                      key={question.question_id}
                      question={question}
                      value={answerValue(activeRound, question.question_id)}
                      disabled={locked}
                      showScope={question.option_ids.length < selected.length}
                      optionLabels={question.option_ids.map((id) => {
                        const option = snapshot.options.find((candidate) => candidate.option_id === id);
                        return option?.name_en ?? option?.name_original ?? id;
                      })}
                      onChange={(answer) => setAnswer(question.question_id, answer)}
                    />
                  ))}
                </div>
                <div className="interview-actions">
                  <button type="submit" className="button button-primary" disabled={locked || invalidAnswer}>Continue</button>
                  <button className="button button-secondary" type="button" disabled={locked || invalidAnswer}
                    onClick={() => void submitAnswers("review")}>
                    View purchase review
                  </button>
                  <small className="answered">{activeRound.questions.filter(q => normalize(answerValue(activeRound, q.question_id)) !== null).length} of {activeRound.questions.length} answered</small>
                  {edited && <button className="button button-secondary" type="button"
                    disabled={locked} onClick={cancelEdit}>Cancel edit</button>}
                </div>
              </form>
            )}
            {!activeRound && !busy && (view?.reason || reviewPrepared) && (
              <p className="interview-status" role={reviewPrepared ? "status" : undefined}>
                {view?.reason ?? "Your selected options and saved answers are ready for the purchase review."}
              </p>
            )}
            {!activeRound && !locked && view && ((failed && !reviewFailed) || view.phase === "ready_for_review") && (
              <div className="interview-actions">
                <button className="button button-primary" type="button"
                  onClick={() => void onSubmit(null, "review")}>View purchase review</button>
              </div>
            )}
            {failed && !locked && (
              <div className="interview-actions">
                <button className={`button ${reviewFailed ? "button-primary" : "button-secondary"}`} type="button" onClick={onRetry}>
                  {needsRegeneration ? "Regenerate report" : reviewFailed ? "Retry purchase review" : "Retry last request"}
                </button>
              </div>
            )}
            {!view && !locked && error && (
              <div className="interview-actions">
                <button className="button button-primary" type="button" onClick={onStartAgain}>Try again</button>
              </div>
            )}
          </div>
          <InterviewSelection snapshot={snapshot} selected={selected} context={context} />
        </div>
      )}
    </section>
  );
}
