import { useEffect, useRef, useState } from "react";
import { currentPurchaseReview, sameAnswer } from "./api";
import type { Answer, CaseView, ListingSnapshot, OrderClarification, Question, QuestionRound, RequirementEffect, Submission } from "./api";
import { OptionImage } from "./Listing";
import { PurchaseReview } from "./PurchaseReview";

const MAX_ANSWER_LENGTH = 1_000;

interface Draft {
  answer_text: string | null;
  requirement_effect?: RequirementEffect | null;
  custom?: true;
}

function savedAnswer(round: QuestionRound, id: string) {
  return round.answers?.find((answer) => answer.question_id === id);
}

function submissionAnswer(question: Question, draft: Draft | undefined): Answer {
  const answerText = draft?.answer_text;
  const answer: Answer = { question_id: question.question_id, answer_text: answerText?.trim() ? answerText : null };
  if (answer.answer_text && draft?.requirement_effect && !question.choices.includes(answer.answer_text)) {
    answer.requirement_effect = draft.requirement_effect;
  }
  return answer;
}

function effectLabel(question: Question, effect: RequirementEffect, statement?: string | null, optionLabels?: string[]) {
  const target = question.requirement_targets?.find((target) => target.requirement_id === effect.requirement_id);
  const scoped = target && optionLabels && target.option_ids.length < question.option_ids.length;
  const scope = scoped ? ` (${target.option_ids.map((id) => optionLabels[question.option_ids.indexOf(id)]).join(" · ")})` : "";
  const existing = target ? `“${target.statement}”${scope}` : "the existing requirement";
  switch (effect.action) {
    case "keep": return `Keep ${existing}`;
    case "replace": return `Replace ${existing} with ${statement ? `“${statement}”` : "my answer"}`;
    case "waive": return effect.requirement_id === null ? "Leave this preference unrestricted" : `Remove ${existing}`;
    case "add": return `Add ${statement ? `“${statement}”` : "my answer as a requirement"}`;
  }
}

function effectKey(effect: RequirementEffect) {
  return `${effect.action}:${effect.requirement_id ?? ""}`;
}

interface QuestionFieldProps {
  question: Question;
  value: Draft | undefined;
  disabled: boolean;
  optionLabels: string[];
  showScope: boolean;
  onChange: (answer: Draft) => void;
}

function QuestionField({ question, value, disabled, optionLabels, showScope, onChange }: QuestionFieldProps) {
  const id = question.question_id;
  const text = value?.answer_text;
  const structured = question.requirement_ids !== undefined;
  const chosen = value?.custom ? undefined : question.choices.find((choice) => choice === text);
  const custom = !question.choices.length || Boolean(value?.custom || (text && chosen === undefined));
  const length = [...(text ?? "")].length;
  const matchedChoice = custom && question.choices.some((choice) => choice === text);
  const freeTextEffects: RequirementEffect[] = (question.requirement_ids ?? []).flatMap((requirement_id) => (
    (["keep", "replace", "waive"] as const).map((action) => ({ action, requirement_id }))
  ));
  freeTextEffects.push({ action: "add", requirement_id: null });
  if (question.requirement_ids?.length === 0) freeTextEffects.push({ action: "waive", requirement_id: null });

  return (
    <fieldset className="interview-question" aria-describedby={`${id}-why`} disabled={disabled}>
      <legend>{question.prompt}</legend>
      <p id={`${id}-why`} className="question-why">{question.why_ask}</p>
      {showScope && <p className="question-scope">Applies to {optionLabels.join(" · ")}</p>}
      {question.choices.length > 0 && (
        <div className="question-choices">
          {question.choices.map((choice, index) => {
            const effect = question.choice_effects?.find((effect) => effect.choice === choice);
            const label = effect ? effectLabel(question, effect, effect.statement, optionLabels) : choice;
            const detail = effect ? (effect.statement === choice ? null : choice)
              : structured ? "Leaves the decision open" : null;
            const labelId = `${id}-effect-choice-${index}`;
            const descriptionId = `${id}-choice-${index}`;
            return (
              <label key={choice} className="question-choice">
                <input type="radio" name={id} checked={chosen === choice} aria-labelledby={labelId}
                  aria-describedby={detail ? descriptionId : undefined}
                  onChange={() => onChange({ answer_text: choice })} />
                <span><span id={labelId}>{label}</span>
                  {detail && <span id={descriptionId} className="question-choice-detail">{detail}</span>}
                </span>
              </label>
            );
          })}
          {structured && <label className="question-choice">
            <input type="radio" name={id} checked={custom}
              onChange={() => onChange({ answer_text: "", custom: true })} />
            Write my own answer
          </label>}
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
            aria-describedby={`${id}-length`}
            onChange={(event) => onChange({ ...value, answer_text: event.currentTarget.value, custom: true })}
          />
          <div className="free-answer-meta">
            <p id={`${id}-length`} className={length > MAX_ANSWER_LENGTH ? "error" : "muted"}>
              {length > MAX_ANSWER_LENGTH ? "Use 1,000 characters or fewer. " : ""}
              {length.toLocaleString("en-US")} / 1,000
            </p>
            {question.choices.length === 0 && <button className="button button-secondary" type="button" aria-pressed={text === null}
              onClick={() => onChange({ answer_text: text === null ? "" : null })}>
              Not sure / Skip
            </button>}
          </div>
          {structured && matchedChoice && <p className="question-why">This text matches an offered choice. Its listed effect will apply.</p>}
          {structured && !matchedChoice && (
            <div className="free-answer-effect">
              <label htmlFor={`${id}-effect`}>How should this answer affect your requirements?</label>
              <select id={`${id}-effect`} aria-describedby={`${id}-effect-note`}
                disabled={!text?.trim()}
                value={value?.requirement_effect ? effectKey(value.requirement_effect) : ""}
                onChange={(event) => {
                  const effect = freeTextEffects.find((effect) => effectKey(effect) === event.currentTarget.value);
                  onChange({ answer_text: text ?? null, requirement_effect: effect, custom: true });
                }}>
                <option value="">Leave the decision open</option>
                {freeTextEffects.map((effect) => (
                  <option key={effectKey(effect)} value={effectKey(effect)}>{effectLabel(question, effect, null, optionLabels)}</option>
                ))}
              </select>
              <p id={`${id}-effect-note`} className="question-why">An open decision will be flagged in the review.</p>
            </div>
          )}
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
          {round.questions.map((question) => {
            const answer = savedAnswer(round, question.question_id);
            const choice = question.choice_effects?.find((choice) => choice.choice === answer?.answer_text);
            const effect = choice ?? answer?.requirement_effect;
            return (
              <div key={question.question_id}>
                <dt>{question.prompt}</dt>
                <dd>
                  {answer?.answer_text ?? "Skipped / unknown"}
                  {effect && <span className="question-choice-detail">{effectLabel(question, effect, choice?.statement)}</span>}
                </dd>
              </div>
            );
          })}
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
  onConfirmOrder: (clarification: OrderClarification) => void;
}

export function Interview({
  hidden, snapshot, selected, context, view, busy, blocked, workingMessage, error, needsRefresh,
  onChangeSelection, onStartAgain, onRetry, onRefresh, onSubmit, onConfirmOrder,
}: Props) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [editingRound, setEditingRound] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [draftEdit, setDraftEdit] = useState<{ revision: number; text: string } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const errorAlert = useRef<HTMLParagraphElement>(null);
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
    if (!hidden) heading.current?.focus();
  }, [hidden, activeRound?.round_id, showReport]);

  useEffect(() => {
    if (!hidden && error) errorAlert.current?.focus();
  }, [hidden, error]);

  function answerValue(round: QuestionRound, id: string) {
    return Object.hasOwn(drafts, id) ? drafts[id] : savedAnswer(round, id);
  }

  function setAnswer(id: string, answer: Draft) {
    setDrafts((existing) => ({ ...existing, [id]: answer }));
  }

  const invalidAnswer = activeRound?.questions.some(
    (question) => [...(answerValue(activeRound, question.question_id)?.answer_text ?? "")].length > MAX_ANSWER_LENGTH,
  );

  async function submitAnswers(action: "continue" | "review") {
    if (locked || invalidAnswer) return;
    setNotice("");
    const submission = activeRound ? {
      round_id: activeRound.round_id,
      answers: activeRound.questions.map((question) => submissionAnswer(question, answerValue(activeRound, question.question_id))),
    } : null;
    const result = await onSubmit(submission, action);
    if (submission && result && result.case_id === view?.case_id && result.rounds.some((round) => (
      round.round_id === submission.round_id && round.answers !== null
      && submission.answers.every((answer) => sameAnswer(savedAnswer(round, answer.question_id), answer))
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
        <h1 id="interview-title" tabIndex={-1} ref={heading}>
          {showReport ? "Before you order" : reviewPrepared && !edited ? "Review inputs ready" : "Clarify your purchase"}
        </h1>
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
          {error && <p className="interview-error" role="alert" tabIndex={-1} ref={errorAlert}>{error}</p>}
          <PurchaseReview key={report.revision} report={report} snapshot={snapshot}
            requirements={view.requirements}
            onConfirmOrder={onConfirmOrder}
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
                  <button type="submit" className="button button-primary" disabled={locked || invalidAnswer}>Continue</button>
                  <button className="button button-secondary" type="button" disabled={locked || invalidAnswer}
                    onClick={() => void submitAnswers("review")}>
                    View purchase review
                  </button>
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
