import { useEffect, useRef, useState } from "react";
import { AnswerClarificationRequired, currentPurchaseReview, pendingReview, previewAnswerEdit, sameAnswer } from "./api";
import type { Answer, BriefEdit, BriefImpact, CaseView, ListingSnapshot, OrderClarification, Question, QuestionRound, Submission } from "./api";
import { BriefChanges, BriefSummary, BuyingBrief, SavedBriefChanges } from "./BuyingBrief";
import { OptionImage } from "./Listing";
import { PurchaseReview } from "./PurchaseReview";

const MAX_ANSWER_LENGTH = 1_000;

interface Draft {
  answer_text: string | null;
  custom?: true;
}

function savedAnswer(round: QuestionRound, id: string) {
  return round.answers?.find((answer) => answer.question_id === id);
}

function submissionAnswer(question: Question, draft: Draft | undefined, saved: Answer | undefined): Answer {
  const answerText = draft?.answer_text;
  const answer: Answer = { question_id: question.question_id, answer_text: answerText?.trim() ? answerText : null };
  if (!answer.answer_text || question.choices.includes(answer.answer_text)) return answer;
  if (saved?.answer_text === answer.answer_text) {
    if (saved.requirement_effect) answer.requirement_effect = saved.requirement_effect;
    if (saved.answer_mode) answer.answer_mode = saved.answer_mode;
  } else answer.answer_mode = "natural";
  return answer;
}

interface QuestionFieldProps {
  question: Question;
  value: Draft | undefined;
  disabled: boolean;
  optionLabels: string[];
  showScope: boolean;
  clarification?: string;
  onChange: (answer: Draft) => void;
}

function QuestionField({ question, value, disabled, optionLabels, showScope, clarification, onChange }: QuestionFieldProps) {
  const id = question.question_id;
  const text = value?.answer_text;
  const clarificationAlert = useRef<HTMLParagraphElement>(null);
  const chosen = value?.custom ? undefined : question.choices.find((choice) => choice === text);
  const custom = !question.choices.length || Boolean(value?.custom || (text && chosen === undefined));
  const length = [...(text ?? "")].length;
  useEffect(() => {
    if (!disabled && clarification) clarificationAlert.current?.focus();
  }, [clarification, disabled]);

  return (
    <fieldset className="interview-question" aria-describedby={`${id}-why`} disabled={disabled}>
      <legend>{question.prompt}</legend>
      <p id={`${id}-why`} className="question-why"><strong>Why we ask · </strong>{question.why_ask}</p>
      {showScope && <p className="question-scope">Applies to {optionLabels.join(" · ")}</p>}
      {question.choices.length > 0 && (
        <div className="question-choices">
          {question.choices.map((choice) => (
            <label key={choice} className="question-choice">
              <input type="radio" name={id} checked={chosen === choice} onChange={() => onChange({ answer_text: choice })} />
              <span>{choice}</span>
            </label>
          ))}
          <label className="question-choice">
            <input type="radio" name={id} checked={custom}
              onChange={() => onChange({ answer_text: "", custom: true })} />
            Write my own answer
          </label>
          <label className="question-choice skip-choice">
            <input type="radio" name={id} checked={text === null} onChange={() => onChange({ answer_text: null })} />
            Not sure / Skip
          </label>
        </div>
      )}
      {custom && (
        <div className="free-answer">
          <label htmlFor={`${id}-answer`}>Your answer</label>
          <textarea
            id={`${id}-answer`}
            value={text ?? ""}
            rows={3}
            placeholder="Type what you know"
            aria-invalid={length > MAX_ANSWER_LENGTH || undefined}
            aria-describedby={clarification ? `${id}-length ${id}-clarification` : `${id}-length`}
            onChange={(event) => onChange({ answer_text: event.currentTarget.value, custom: true })}
          />
          <div className="free-answer-meta">
            <p id={`${id}-length`} className={length > MAX_ANSWER_LENGTH ? "error" : "muted"}>
              {length > MAX_ANSWER_LENGTH ? "Use 1,000 characters or fewer. " : ""}
              {length.toLocaleString("en-US")} / 1,000
            </p>
            {question.choices.length === 0 && !clarification && <button className="button button-secondary" type="button" aria-pressed={text === null}
              onClick={() => onChange({ answer_text: text === null ? "" : null })}>
              Not sure / Skip
            </button>}
          </div>
        </div>
      )}
      {clarification && (
        <div className="answer-clarification">
          <p id={`${id}-clarification`} ref={clarificationAlert} role="alert" tabIndex={-1}>
            {clarification}
          </p>
          <button className="button button-secondary" type="button" onClick={() => onChange({ answer_text: null })}>
            Leave this answer open
          </button>
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
              <dd>{savedAnswer(round, question.question_id)?.answer_text ?? "Skipped / unknown"}</dd>
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

export type InterviewScreen = "questions" | "brief" | "preparing-report" | "report";

interface Props {
  hidden: boolean;
  onScreenChange: (screen: InterviewScreen) => void;
  snapshot: ListingSnapshot;
  selected: string[];
  context: string;
  view: CaseView | null;
  busy: boolean;
  blocked: boolean;
  workingMessage: string;
  error: string;
  needsRefresh: boolean;
  reportConnectionLost: boolean;
  onChangeSelection?: () => void;
  onStartAgain: () => void;
  onRetry: () => void;
  onRefresh: () => void;
  onSubmit: (submission: Submission | null, action: "continue" | "brief") => Promise<CaseView | null>;
  onEditBrief: (edit: BriefEdit) => Promise<CaseView | null>;
  onConfirmBrief: () => void;
  onResumeQuestions: () => void;
  onConfirmOrder: (clarification: OrderClarification) => void;
}

export function Interview({
  hidden, onScreenChange, snapshot, selected, context, view, busy, blocked, workingMessage, error, needsRefresh, reportConnectionLost,
  onChangeSelection, onStartAgain, onRetry, onRefresh, onSubmit, onConfirmOrder, onEditBrief,
  onConfirmBrief, onResumeQuestions,
}: Props) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [editingRound, setEditingRound] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [answerClarification, setAnswerClarification] = useState<AnswerClarificationRequired | null>(null);
  const [reopenBrief, setReopenBrief] = useState(false);
  const [expandBrief, setExpandBrief] = useState(false);
  const [answerPreview, setAnswerPreview] = useState<{
    impact: BriefImpact; submission: Submission; action: "continue" | "brief"; revision: number;
  } | null>(null);
  const [previewingAnswers, setPreviewingAnswers] = useState(false);
  const [draftEdit, setDraftEdit] = useState<{ revision: number; text: string } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const errorAlert = useRef<HTMLParagraphElement>(null);
  const openRound = view?.rounds.find((round) => round.answers === null);
  const edited = view?.rounds.find((round) => round.round_id === editingRound);
  const activeRound = edited ?? openRound;
  const reviewStatus = pendingReview(view);
  const running = view?.last_operation?.status === "running";
  const locked = busy || blocked || running || Boolean(reviewStatus) || needsRefresh || previewingAnswers;
  const failed = view?.last_operation?.status === "failed";
  const report = currentPurchaseReview(view);
  const reportInvalid = view?.phase === "reviewed" && !report;
  const showReport = Boolean(report && !hidden && !activeRound && !locked && !reopenBrief);
  const reviewPrepared = view?.phase === "ready_for_review";
  const reviewFailed = failed && view?.last_operation?.stage === "review";
  const needsRegeneration = reviewFailed && view.confirmed_revision !== view.revision;
  const reportWaiting = Boolean(reviewStatus || (reviewFailed && !needsRegeneration));
  const briefScreen = Boolean((reviewPrepared || reopenBrief) && !edited && !reportWaiting);
  const screen = hidden ? "questions" : showReport ? "report" : reportWaiting ? "preparing-report" : briefScreen ? "brief" : "questions";

  useEffect(() => {
    onScreenChange(screen);
  }, [onScreenChange, screen]);

  useEffect(() => {
    if (!hidden) heading.current?.focus();
  }, [hidden, activeRound?.round_id, screen]);

  useEffect(() => {
    if (!hidden && error) errorAlert.current?.focus();
  }, [hidden, error]);

  function answerValue(round: QuestionRound, id: string) {
    return Object.hasOwn(drafts, id) ? drafts[id] : savedAnswer(round, id);
  }

  function setAnswer(id: string, answer: Draft) {
    setAnswerPreview(null);
    setAnswerClarification((current) => current?.questionId === id ? null : current);
    setDrafts((existing) => ({ ...existing, [id]: answer }));
  }

  const invalidAnswer = activeRound?.questions.some(
    (question) => [...(answerValue(activeRound, question.question_id)?.answer_text ?? "")].length > MAX_ANSWER_LENGTH,
  );

  const answeredCount = activeRound?.questions.filter((question) => (
    answerValue(activeRound, question.question_id)?.answer_text?.trim()
  )).length;

  async function submitAnswers(action: "continue" | "brief") {
    if (locked || invalidAnswer) return;
    setNotice("");
    setAnswerClarification(null);
    const submission = activeRound ? {
      round_id: activeRound.round_id,
      answers: activeRound.questions.map((question) => submissionAnswer(
        question, answerValue(activeRound, question.question_id), savedAnswer(activeRound, question.question_id),
      )),
    } : null;
    if (edited && submission && view) {
      setPreviewingAnswers(true);
      try {
        const impact = await previewAnswerEdit(view, submission);
        setAnswerPreview({ impact, submission, action, revision: view.revision });
      } catch (failure) {
        if (failure instanceof AnswerClarificationRequired) setAnswerClarification(failure);
        else setNotice(failure instanceof Error ? failure.message : "Could not preview the answer changes.");
      } finally {
        setPreviewingAnswers(false);
      }
      return;
    }
    await saveAnswers(submission, action);
  }

  async function saveAnswers(submission: Submission | null, action: "continue" | "brief") {
    setAnswerPreview(null);
    setReopenBrief(false);
    setExpandBrief(false);
    let result: CaseView | null;
    try {
      result = await onSubmit(submission, action);
    } catch (failure) {
      if (failure instanceof AnswerClarificationRequired) setAnswerClarification(failure);
      else setNotice(failure instanceof Error ? failure.message : "Could not save your answers.");
      return;
    }
    if (submission && result && result.case_id === view?.case_id && result.rounds.some((round) => (
      round.round_id === submission.round_id && round.answers !== null
      && submission.answers.every((answer) => sameAnswer(savedAnswer(round, answer.question_id), answer))
    ))) {
      if (edited && action === "brief" && result.rounds.some((round) => round.answers === null)) {
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
    setAnswerPreview(null);
    setAnswerClarification(null);
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
            setAnswerPreview(null);
            setAnswerClarification(null);
            setEditingRound(round.round_id);
          }} />)}
    </section>
  );

  return (
    <section className="interview-shell" aria-labelledby="interview-title" hidden={hidden}>
      <div className="interview-header">
        <div><span className="eyebrow">Step {showReport || reportWaiting ? "3 of 3 · Purchase review" : briefScreen ? "2 of 3 · Buying brief" : "2 of 3 · Buyer questions"}</span>
        <h1 id="interview-title" tabIndex={-1} ref={heading}>
          {showReport ? "Before you order" : reportWaiting ? reviewFailed ? "Purchase review paused" : "Preparing your purchase review"
            : briefScreen ? "Review buying brief" : "Clarify your purchase"}
        </h1>
        <p className="muted">{showReport ? "Check each finding against its sources before contacting the seller."
          : reportWaiting ? "Your buying brief is confirmed and your answers are saved."
            : briefScreen ? "Check the requirements and open decisions before generating your review."
              : "A few questions about what you need. Answer what you know, skip the rest."}</p></div>
        <div className="interview-actions">
          {showReport && history && <a className="button button-secondary" href="#history-title"
            onClick={() => document.getElementById("history-title")?.focus()}>Edit answers</a>}
          {showReport && <button className="button button-secondary" type="button"
            onClick={() => setReopenBrief(true)}>Review buying brief</button>}
          {reopenBrief && report && <button className="button button-secondary" type="button"
            onClick={() => setReopenBrief(false)}>Back to purchase review</button>}
          <button className="button button-secondary" type="button" disabled={!onChangeSelection} onClick={onChangeSelection}>
            Change options or context
          </button>
        </div>
      </div>

      {showReport && report && view ? (
        <>
          {error && <p className="interview-error" role="alert" tabIndex={-1} ref={errorAlert}>{error}</p>}
          <PurchaseReview key={report.revision} report={report} snapshot={snapshot}
            requirements={view.requirements}
            confirmed={view.confirmed_revision === report.revision} onReviewBrief={() => setReopenBrief(true)}
            briefChanges={<SavedBriefChanges view={view} snapshot={snapshot} />}
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
              <p className="interview-error" role="alert" tabIndex={-1} ref={errorAlert}>
                {error || view?.last_operation?.message || "Could not prepare the next step."}
              </p>
            )}
            {reportInvalid && !busy && !error && (
              <p className="interview-error" role="alert">The purchase review could not be matched to your saved answers.</p>
            )}
            {reviewStatus && !needsRefresh && !busy && (
              <div className="interview-status report-progress" role="status">
                <span className="report-spinner" aria-hidden="true" />
                <div>
                  <h2>{reviewStatus === "queued" ? "In the report queue" : "Generating your evidence report"}</h2>
                  <p>{reviewStatus === "queued" ? "Your review will start as soon as a slot opens."
                    : "We’re checking the seller’s evidence against your buying brief."}</p>
                  <p className="muted">{reportConnectionLost ? "Could not check progress. Reconnecting…"
                    : "This page updates automatically. You can refresh and pick up where you left off."}</p>
                </div>
              </div>
            )}
            {((running && !reviewStatus) || needsRefresh || reportInvalid) && !busy && (
              <div className="interview-status">
                <p>{running && !reviewStatus ? "Your last request is still running." : "Reload your saved progress before submitting again."}</p>
                <button className="button button-secondary" type="button" disabled={blocked} onClick={onRefresh}>
                  Reload saved progress
                </button>
              </div>
            )}
            {history}
            {view && activeRound && !edited && view.requirements != null && (
              <BriefSummary view={view} snapshot={snapshot} expanded={expandBrief}
                onToggle={() => setExpandBrief((expanded) => !expanded)} />
            )}
            {view && !edited && (briefScreen || expandBrief) && (
              <BuyingBrief id={activeRound ? "expanded-buying-brief" : undefined} view={view} snapshot={snapshot} disabled={locked}
                onConfirm={reviewPrepared && !activeRound ? onConfirmBrief : undefined}
                onResume={view.can_resume_questions ? onResumeQuestions : undefined}
                onEdit={async (edit) => {
                const result = await onEditBrief(edit);
                if (result) setReopenBrief(false);
                return result;
              }} onConfirmOrder={(order) => {
                setReopenBrief(false);
                onConfirmOrder(order);
              }} />
            )}
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
                      clarification={answerClarification?.questionId === question.question_id ? answerClarification.message : undefined}
                      showScope={question.requirement_ids !== undefined || question.option_ids.length < selected.length}
                      optionLabels={question.option_ids.map((id) => {
                        const option = snapshot.options.find((candidate) => candidate.option_id === id);
                        return option?.name_en ?? option?.name_original ?? id;
                      })}
                      onChange={(answer) => setAnswer(question.question_id, answer)}
                    />
                  ))}
                </div>
                <div className="interview-actions">
                  <button type="submit" className="button button-primary" disabled={locked || invalidAnswer}>
                    {edited ? "Preview answer changes" : "Continue"}
                  </button>
                  <button className="button button-secondary" type="button" disabled={locked || invalidAnswer}
                    onClick={() => void submitAnswers("brief")}>
                    {edited ? "Preview and review brief" : "Review buying brief"}
                  </button>
                  <small className="answered">{answeredCount} of {activeRound.questions.length} answered</small>
                  {edited && <button className="button button-secondary" type="button"
                    disabled={locked} onClick={cancelEdit}>Cancel edit</button>}
                </div>
              </form>
            )}
            {previewingAnswers && <p className="interview-status" role="status">Previewing changes to your buying brief…</p>}
            {answerPreview?.revision === view?.revision && answerPreview && (
              <section className="review-panel brief-preview" aria-label="Preview of answer changes">
                <h3>Preview answer changes</h3>
                <BriefChanges impact={answerPreview.impact} snapshot={snapshot} selected={selected} />
                <button type="button" className="button button-primary" disabled={locked}
                  onClick={() => void saveAnswers(answerPreview.submission, answerPreview.action)}>Save answer changes</button>
              </section>
            )}
            {!activeRound && !busy && !reportWaiting && view?.reason && <p className="interview-status">{view.reason}</p>}
            {!activeRound && !locked && view && failed && (!reviewFailed || needsRegeneration) && (
              <div className="interview-actions">
                <button className="button button-primary" type="button"
                  onClick={() => void onSubmit(null, "brief")}>Review buying brief</button>
              </div>
            )}
            {failed && !locked && !needsRegeneration && (
              <div className="interview-actions">
                <button className={`button ${reviewFailed ? "button-primary" : "button-secondary"}`} type="button" onClick={onRetry}>
                  {reviewFailed ? "Retry purchase review" : "Retry last request"}
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
