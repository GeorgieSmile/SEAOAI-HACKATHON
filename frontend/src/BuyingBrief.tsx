import { useRef, useState } from "react";
import { previewCorrection, previewUndo } from "./api";
import type { BriefCorrection, BriefEdit, BriefImpact, BriefPreview, CaseView, ListingSnapshot, OrderClarification } from "./api";
import { briefDecisionText, groupBriefRequirements, OrderClarificationForm, RequirementDecisions } from "./PurchaseReview";

function optionLabels(snapshot: ListingSnapshot, selected: string[], ids: string[]) {
  return selected.filter((id) => ids.includes(id)).map((id) => {
    const option = snapshot.options.find((item) => item.option_id === id);
    const label = `Option ${"ABC"[selected.indexOf(id)]}`;
    const name = option?.name_en ?? option?.name_original;
    return name ? `${label} · ${name}` : label;
  }).join(", ");
}

function hasChanges(impact: BriefImpact | undefined): impact is BriefImpact {
  return Boolean(impact && (impact.changed_requirements.length || impact.removed_requirements.length || impact.discarded_rounds.length));
}

const changeStatuses = { active: "Updated", waived: "Waived", unresolved: "Open" };

export function BriefChanges({ impact, snapshot, selected, saved }: {
  impact: BriefImpact;
  snapshot: ListingSnapshot;
  selected: string[];
  saved?: boolean;
}) {
  const rounds = impact.discarded_rounds;
  return (
    <div className="brief-changes">
      {impact.changed_requirements.length > 0 && (
        <div>
          <h4>Updated requirements</h4>
          <ul aria-label="Updated requirements">
            {groupBriefRequirements(impact.changed_requirements).map(({ row }) => (
              <li key={row.requirement_id}>
                <strong>{optionLabels(snapshot, selected, row.option_ids)}</strong>
                <span>{changeStatuses[row.status]} · {briefDecisionText(row)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {impact.removed_requirements.length > 0 && (
        <div>
          <h4>Requirements removed</h4>
          <ul aria-label="Requirements removed">
            {groupBriefRequirements(impact.removed_requirements).map(({ row }) => (
              <li key={row.requirement_id}>
                <strong>{optionLabels(snapshot, selected, row.option_ids)}</strong>
                <span>{briefDecisionText(row)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {rounds.length > 0 && (
        <div>
          <h4>{saved ? "Follow-up questions removed" : "Follow-up questions to revisit"}</h4>
          <ul>
            {rounds.flatMap((round) => round.questions.map((question) => {
              const answer = round.answers?.find((item) => item.question_id === question.question_id);
              return (
                <li key={question.question_id}>
                  <strong>Round {round.round_number} · {question.prompt}</strong>
                  <span>{optionLabels(snapshot, selected, question.option_ids)}</span>
                  {answer?.answer_text && <span>{saved ? "Removed answer" : "Answer to be removed"} · {answer.answer_text}</span>}
                </li>
              );
            }))}
          </ul>
        </div>
      )}
      {!hasChanges(impact) && <p>No requirements or follow-up answers will change.</p>}
    </div>
  );
}

export function SavedBriefChanges({ view, snapshot }: { view: CaseView; snapshot: ListingSnapshot }) {
  if (!hasChanges(view.brief_impact)) return null;
  return (
    <section className="review-panel" aria-label="Latest changes to the buying brief">
      <h2>Latest changes to the buying brief</h2>
      <BriefChanges impact={view.brief_impact} snapshot={snapshot} selected={view.selected_option_ids} saved />
    </section>
  );
}

export function BriefSummary({ view, snapshot, expanded, onToggle }: {
  view: CaseView;
  snapshot: ListingSnapshot;
  expanded: boolean;
  onToggle: () => void;
}) {
  const requirements = view.requirements ?? [];
  const impact = view.brief_impact;
  const open = requirements.filter((row) => row.status === "unresolved").flatMap((row) => {
    const changed = impact?.changed_requirements.find((change) => change.requirement_id === row.requirement_id);
    const option_ids = row.option_ids.filter((id) => !changed?.option_ids.includes(id));
    return option_ids.length ? [{ ...row, option_ids }] : [];
  });
  return (
    <section className="review-panel brief-summary" aria-labelledby="brief-summary-title">
      <h2 id="brief-summary-title">Buying brief so far</h2>
      {!expanded && hasChanges(impact) && <BriefChanges impact={impact} snapshot={snapshot} selected={view.selected_option_ids} saved />}
      {!expanded && open.length > 0 && <ul className="brief-summary-open" aria-label="Open decisions">
        {groupBriefRequirements(open).map(({ row }) => <li key={row.requirement_id}>
          <span className="requirement-status is-unresolved">Open</span>
          <span>{briefDecisionText(row)}</span>
          <small>{optionLabels(snapshot, view.selected_option_ids, row.option_ids)}</small>
        </li>)}
      </ul>}
      {!expanded && !hasChanges(impact) && open.length === 0 && (
        <p className="muted">{requirements.length ? "No new changes." : "No requirements recorded yet."}</p>
      )}
      <button type="button" className="button button-secondary" aria-expanded={expanded}
        aria-controls={expanded ? "expanded-buying-brief" : undefined} onClick={onToggle}>
        {expanded ? "Hide buying brief" : "View or edit buying brief"}
      </button>
    </section>
  );
}

export function BuyingBrief({ id, view, snapshot, disabled, onEdit, onConfirmOrder, onConfirm, onResume }: {
  id?: string;
  view: CaseView;
  snapshot: ListingSnapshot;
  disabled: boolean;
  onEdit: (edit: BriefEdit) => Promise<CaseView | null>;
  onConfirmOrder: (order: OrderClarification) => void;
  onConfirm?: () => void;
  onResume?: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [option, setOption] = useState("");
  const [resolves, setResolves] = useState<string | undefined>();
  const [preview, setPreview] = useState<{ result: BriefPreview; edit: BriefEdit; revision: number } | null>(null);
  const [error, setError] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const pending = useRef(false);
  const editField = useRef<HTMLTextAreaElement>(null);
  const selected = view.selected_option_ids;
  const requirements = view.requirements ?? [];
  const candidate = preview?.revision === view.revision ? preview : null;
  const lastCorrection = [
    ...(view.brief_changes ?? []).map((change) => ({ id: change.change_id, revision: change.revision })),
    ...(view.order_clarifications ?? []).map((change) => ({ id: change.clarification_id, revision: change.revision ?? 0 })),
  ].sort((first, second) => second.revision - first.revision)[0];
  const openCorrections = view.brief_changes?.filter((change) => change.question && requirements.some(
    (row) => row.status === "unresolved" && row.topic === "other" && row.provenance[0]?.input_ref === `brief:${change.change_id}`,
  )) ?? [];

  function startEditing(changeId?: string) {
    setEditing(true);
    setPreview(null);
    setError("");
    setText("");
    setOption("");
    setResolves(changeId);
    requestAnimationFrame(() => editField.current?.focus());
  }

  async function inspect(undoId?: string) {
    if (disabled || pending.current) return;
    pending.current = true;
    setError("");
    setPreview(null);
    setPreviewing(true);
    try {
      if (undoId) {
        const result = await previewUndo(view, undoId);
        setPreview({ result, edit: { undo_change_id: undoId }, revision: view.revision });
      } else {
        const correction: BriefCorrection = {
          text, option_id: option || null, case_id: view.case_id, revision: view.revision,
          resolves_change_id: resolves,
        };
        const result = await previewCorrection(correction);
        if (result.proposal) setPreview({ result, edit: { correction, proposal: result.proposal }, revision: view.revision });
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not preview the change.");
    } finally {
      pending.current = false;
      setPreviewing(false);
    }
  }

  async function apply(edit: BriefEdit) {
    const result = await onEdit(edit);
    if (result && result.revision > view.revision) {
      setPreview(null);
      setEditing(false);
      setText("");
      setOption("");
    }
  }

  return (
    <section id={id} className="buying-brief" aria-label="Buying brief">
      <SavedBriefChanges view={view} snapshot={snapshot} />
      <RequirementDecisions buyingBrief requirements={requirements} selected={selected} snapshot={snapshot}
        changed={view.brief_impact?.changed_requirements} />
      {requirements.filter((row) => row.order_conflict).flatMap((row) => row.option_ids.map((id) => (
        <OrderClarificationForm key={`${row.requirement_id}:${id}`} requirement={row} optionId={id} snapshot={snapshot}
          selected={selected} onConfirm={onConfirmOrder} disabled={disabled || previewing} />
      )))}
      {requirements.some((row) => row.delivery_condition && !row.delivery_condition.latest_arrival) && (
        <p className="interview-status">What is the latest date the custom order can arrive? Edit the brief to give the date, or leave it open.</p>
      )}
      {openCorrections.map((row) => (
        <div className="review-panel brief-preview" key={row.change_id}>
          <p>{row.question}</p>
          <button type="button" className="button button-secondary" disabled={disabled || previewing}
            onClick={() => startEditing(row.change_id)}>Clarify open correction</button>
        </div>
      ))}
      <div className="interview-actions">
        <button type="button" className="button button-secondary" disabled={disabled || previewing}
          onClick={() => startEditing()}>
          Edit buying brief
        </button>
        {lastCorrection && (
          <button type="button" className="button button-secondary" disabled={disabled || previewing}
            onClick={() => { setEditing(false); void inspect(lastCorrection.id); }}>
            Undo last brief change
          </button>
        )}
      </div>
      {editing && (
        <form className="brief-editor review-panel" onSubmit={(event) => { event.preventDefault(); void inspect(); }}>
          <label>
            <strong>What should change?</strong>
            <textarea ref={editField} value={text} maxLength={1000} rows={3} disabled={disabled || previewing}
              onChange={(event) => { setText(event.target.value); setPreview(null); }} />
          </label>
          <label>
            <strong>Applies to</strong>
            <select value={option} disabled={disabled || previewing} onChange={(event) => { setOption(event.target.value); setPreview(null); }}>
              <option value="">Match my wording</option>
              {selected.map((id) => <option key={id} value={id}>
                {optionLabels(snapshot, selected, [id])}
              </option>)}
            </select>
          </label>
          <div className="interview-actions">
            <button className="button button-primary" type="submit" disabled={disabled || previewing || !text.trim()}>Preview change</button>
            <button className="button button-secondary" type="button" disabled={disabled || previewing}
              onClick={() => { setEditing(false); setPreview(null); setError(""); }}>Cancel edit</button>
          </div>
        </form>
      )}
      {previewing && <p role="status">Checking the effect on your brief…</p>}
      {error && <p role="alert" className="interview-error">{error}</p>}
      {candidate && (
        <section className="review-panel brief-preview" aria-label="Preview of brief changes" key={candidate.revision}>
          <h3>{"undo_change_id" in candidate.edit ? "Preview undo" : "Preview change"}</h3>
          {candidate.result.question && <p role="status">{candidate.result.question}</p>}
          <BriefChanges impact={candidate.result} snapshot={snapshot} selected={selected} />
          <div className="interview-actions">
            <button type="button" className="button button-primary" disabled={disabled || previewing}
              onClick={() => {
                if (resolves && candidate.result.proposal?.kind === "unclear") {
                  setEditing(false);
                  setPreview(null);
                } else void apply(candidate.edit);
              }}>
              {candidate.result.question ? "Leave this item open" : "undo_change_id" in candidate.edit ? "Undo change" : "Save change"}
            </button>
            <button type="button" className="button button-secondary" disabled={disabled}
              onClick={() => setPreview(null)}>Dismiss preview</button>
          </div>
        </section>
      )}
      {onConfirm && (
        <div className="interview-actions">
          <button type="button" className="button button-primary"
            disabled={disabled || editing || previewing || Boolean(candidate)} onClick={onConfirm}>
            Confirm brief and generate review
          </button>
          {onResume && <button type="button" className="button button-secondary" onClick={onResume}
            disabled={disabled || editing || previewing || Boolean(candidate)}>Continue questions</button>}
        </div>
      )}
    </section>
  );
}
