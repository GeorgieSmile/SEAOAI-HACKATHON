import { useRef, useState } from "react";
import { sourceImageUrl } from "./api";
import type {
  ListingOption, ListingSnapshot, OrderClarification, PurchaseReview as Review, ResolvedRequirement, ReviewClaim,
  ReviewFinding, ReviewRequirement, Source,
} from "./api";

const findingKinds = {
  listing_conflict: "Listing conflict",
  requirement_unconfirmed: "Requirement unconfirmed",
  clarification_needed: "Clarification needed",
  evidence_gap: "Evidence gap",
};

const actions = {
  decide_preference: "Your decision",
  ask_supplier: "Ask the supplier",
  request_evidence: "Request supporting evidence",
};

const unknownReasons = {
  not_found: "Not found in reviewed evidence",
  not_reviewed: "Not reviewed",
  unreadable: "Unavailable or unreadable",
};

const coverageGroups = {
  product_match: "Product match",
  intended_use: "Intended use",
  supporting_evidence: "Supporting evidence",
};

const coverageStatuses = {
  reviewed: "Reviewed",
  limited: "Limited evidence",
  not_assessed: "Not assessed",
};

const optionLetters = ["A", "B", "C"];

function optionLabel(ids: string[], id: string) {
  return `Option ${optionLetters[ids.indexOf(id)]}`;
}

function findingAnchor(id: string) {
  return `finding-${encodeURIComponent(id)}`;
}

function EvidenceImage({ source, snapshotId }: { source: Source; snapshotId: string }) {
  const [failed, setFailed] = useState(false);

  if (failed) return <p className="muted">Could not load this listing image.</p>;
  return <img src={sourceImageUrl(snapshotId, source.source_id)} alt={source.label}
    loading="lazy" onError={() => setFailed(true)} />;
}

interface EvidenceContext {
  snapshotId: string;
  selected: string[];
  sources: Map<string, Source>;
  claims: Map<string, ReviewClaim>;
}

function EvidenceSource({ context, id, claim, showOptionScope = true }: {
  context: EvidenceContext;
  id: string;
  claim?: ReviewClaim;
  showOptionScope?: boolean;
}) {
  const source = context.sources.get(id);
  if (!source) return null;
  const scope = context.selected.filter((optionId) => (claim?.option_ids ?? source.option_ids).includes(optionId));
  const optionScope = scope.map((optionId) => optionLabel(context.selected, optionId)).join(", ");
  const isGuidance = source.kind === "official_guidance";
  const isImage = source.kind === "listing_image";
  const showScopeInLabel = showOptionScope && !isGuidance && scope.length > 0
    && (source.option_ids.length === 1 || scope.length < context.selected.length);

  return (
    <details className="review-source" data-claim-id={claim?.claim_id}>
      <summary>{claim?.display_text ?? source.label}{showScopeInLabel && ` · ${optionScope}`}</summary>
      <div className="review-source-body">
        {claim && <p><strong>{source.label}</strong></p>}
        <p className="muted">
          {isGuidance ? "Official guidance · curated reference" : isImage ? "Seller listing image" : "Seller listing claim"}
          {scope.length > 0 ? ` · ${optionScope}`
            : !isGuidance && source.option_ids.length === 0 ? " · listing level" : ""}
        </p>
        {source.availability !== "available" ? (
          <p>{source.availability === "missing" ? "Missing from the saved listing." : "This source could not be read."}</p>
        ) : isImage ? (
          <EvidenceImage snapshotId={context.snapshotId} source={source} />
        ) : (
          <>
            {claim?.kind === "text_excerpt" && claim.quote !== source.original_text && (
              <div><p className="review-source-label">Cited excerpt</p><p className="review-quote" lang={isGuidance ? undefined : "zh-Hans"}>{claim.quote}</p></div>
            )}
            <div><p className="review-source-label">Original source text</p><p className="review-quote" lang={isGuidance ? undefined : "zh-Hans"}>{source.original_text}</p></div>
            <div><p className="review-source-label">English translation of the source</p><p>{source.text_en ?? "English translation unavailable."}</p></div>
            {isGuidance && source.url?.startsWith("https://") && (
              <a href={source.url} target="_blank" rel="noreferrer">Open guidance document (new tab)</a>
            )}
          </>
        )}
      </div>
    </details>
  );
}

function ClaimEvidence({ claimIds, context, showOptionScope }: {
  claimIds: string[];
  context: EvidenceContext;
  showOptionScope?: boolean;
}) {
  return claimIds.map((id) => {
    const claim = context.claims.get(id);
    return claim && <EvidenceSource key={id} context={context} id={claim.source_id} claim={claim} showOptionScope={showOptionScope} />;
  });
}

function narrowerScope(selected: string[], optionIds: string[]) {
  if (optionIds.length >= selected.length) return "";
  return selected.filter((id) => optionIds.includes(id)).map((id) => optionLabel(selected, id)).join(", ");
}

function Requirement({ requirement, selected }: { requirement: ReviewRequirement; selected: string[] }) {
  const scope = narrowerScope(selected, requirement.option_ids);
  const origin = requirement.question_text ? `Your answer to “${requirement.question_text}”` : "Your purchase context";
  return (
    <div className="review-requirement">
      {requirement.text !== requirement.quote && <p>{requirement.text}</p>}
      <blockquote>
        <p>{requirement.quote}</p>
        <footer>{origin}{scope && ` · ${scope}`}</footer>
      </blockquote>
    </div>
  );
}

function FindingCard({ finding, report, context }: {
  finding: ReviewFinding;
  report: Review;
  context: EvidenceContext;
}) {
  const requirements = report.buyer_requirements.filter((requirement) =>
    finding.requirement_ids.includes(requirement.requirement_id),
  );
  const preferences = report.open_preferences.filter((preference) => preference.finding_ids.includes(finding.finding_id));
  return (
    <article className="review-finding" id={findingAnchor(finding.finding_id)}>
      <header>
        <p className="review-kicker">{actions[finding.action_type]}</p>
        <h3>{finding.title}</h3>
        <p className="review-scope">
          {findingKinds[finding.kind]} · {context.selected.filter((id) => finding.option_ids.includes(id))
            .map((id) => optionLabel(context.selected, id)).join(", ")}
        </p>
      </header>
      <div className="review-finding-content">
        {requirements.length > 0 && (
          <div>
            <h4>Buyer requirement{requirements.length === 1 ? "" : "s"}</h4>
            {requirements.map((requirement) => (
              <Requirement key={requirement.requirement_id} requirement={requirement} selected={context.selected} />
            ))}
          </div>
        )}
        <div>
          <h4>Assessment from the evidence</h4>
          <p>{finding.interpretation}</p>
          <p>{finding.impact}</p>
          <p className="review-priority"><strong>Why it’s prioritized</strong> {finding.priority_reason}</p>
        </div>
        {finding.claim_ids.length > 0 && (
          <section aria-label="Source evidence">
            <h4>Source evidence</h4>
            <ClaimEvidence claimIds={finding.claim_ids} context={context} />
          </section>
        )}
        {finding.unknowns.length > 0 && (
          <div>
            <h4>What’s still unknown</h4>
            <ul className="review-unknowns">
              {finding.unknowns.map((unknown, index) => (
                <li key={index}>
                  <p className="muted">{unknownReasons[unknown.reason]}</p>
                  <p>{unknown.text}</p>
                  {unknown.source_ids
                    .filter((sourceId) => !finding.claim_ids.some((id) => context.claims.get(id)?.source_id === sourceId))
                    .map((sourceId) => <EvidenceSource key={sourceId} context={context} id={sourceId} />)}
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className="review-next">
          <h4>What to do next</h4>
          {preferences.map((preference) => <p key={preference.preference_id}>{preference.text}</p>)}
          {finding.supplier_question && <p>{finding.supplier_question}</p>}
          <p><strong>What to request</strong> {finding.requested_evidence}</p>
          <p><strong>Evidence limits</strong> {finding.evidence_limits}</p>
        </div>
      </div>
    </article>
  );
}

function OptionName({ option }: { option: ListingOption | undefined }) {
  return option && (
    <>
      {option.name_en && <span className="review-option-name">{option.name_en}</span>}
      <span className="original" lang="zh-Hans">{option.name_original}</span>
    </>
  );
}

const orderChoices = [
  ["ready_made_only", "Ready-made stock only"],
  ["custom_orders_acceptable", "Custom orders are acceptable"],
] as const satisfies ReadonlyArray<readonly [OrderClarification["choice"], string]>;

function OrderClarificationForm({ requirement, snapshot, selected, onConfirm }: {
  requirement: ResolvedRequirement;
  snapshot: ListingSnapshot;
  selected: string[];
  onConfirm: (clarification: OrderClarification) => void;
}) {
  const [choice, setChoice] = useState<OrderClarification["choice"] | null>(null);
  const optionId = requirement.option_ids[0]!;
  const option = snapshot.options.find((item) => item.option_id === optionId);
  const label = optionLabel(selected, optionId);
  const headingId = `clarify-order-${requirement.requirement_id}`;

  return (
    <section className="review-panel order-clarification" aria-labelledby={headingId}>
      <h2 id={headingId}>Clarify order type for {label}</h2>
      {option && <p className="order-clarification-option"><OptionName option={option} /></p>}
      <div className="order-clarification-quotes">
        {requirement.provenance.map((entry, index) => entry.quote !== null && (
          <blockquote key={`${entry.input_ref}-${index}`}>
            <p>{entry.quote}</p>
            <footer>{entry.question_text ? `Your answer to “${entry.question_text}”` : "Your purchase context"}</footer>
          </blockquote>
        ))}
      </div>
      <form onSubmit={(event) => {
        event.preventDefault();
        if (choice) onConfirm({ requirement_id: requirement.requirement_id, option_id: optionId, choice });
      }}>
        <fieldset className="interview-question">
          <legend>Which order type should apply to {label}?</legend>
          <div className="question-choices">
            {orderChoices.map(([value, text]) => (
              <label key={value} className="question-choice">
                <input type="radio" name={headingId} checked={choice === value} onChange={() => setChoice(value)} />
                <span>{text}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <button className="button button-primary" type="submit" disabled={!choice}>Save and update review</button>
      </form>
    </section>
  );
}

const requirementStatuses = {
  active: "Required",
  waived: "Waived",
  unresolved: "Decision open",
};

const decisionActions = {
  keep: "Kept",
  replace: "Replaced",
  waive: "Waived",
  add: "Added",
  unresolved: "Left open",
};

function RequirementDecision({ requirement, optionId }: { requirement: ResolvedRequirement; optionId: string }) {
  const history = requirement.provenance.filter((entry) => entry.option_ids.includes(optionId));
  const latest = history.at(-1);
  const openAnswer = requirement.status === "unresolved" && latest?.action === "unresolved" && latest.question_text;

  return (
    <li>
      <details className="requirement-decision">
        <summary>
          <span className={`requirement-status is-${requirement.status}`}>
            {requirementStatuses[requirement.status]}
            {openAnswer && (latest.quote === null ? " · Question skipped" : " · Answer left open")}
          </span>
          <span className="requirement-statement">{requirement.statement}</span>
        </summary>
        <ol className="requirement-history" aria-label="Decision history">
          {history.map((entry, index) => (
            <li key={`${entry.input_ref}-${index}`}>
              <p><strong>{decisionActions[entry.action]}</strong> · {entry.question_text
                ? `Your answer to “${entry.question_text}”` : "Your purchase context"}</p>
              {entry.quote === null ? <p className="muted">No answer submitted.</p> : <blockquote>{entry.quote}</blockquote>}
            </li>
          ))}
        </ol>
      </details>
    </li>
  );
}

function RequirementDecisions({ requirements, selected, snapshot }: {
  requirements: ResolvedRequirement[];
  selected: string[];
  snapshot: ListingSnapshot;
}) {
  return (
    <section className="review-panel requirement-decisions" aria-labelledby="requirement-decisions-heading">
      <h2 id="requirement-decisions-heading">Your requirements by option</h2>
      <div className="requirement-options">
        {selected.map((id) => {
          const decisions = requirements.filter((requirement) => requirement.option_ids.includes(id));
          const option = snapshot.options.find((option) => option.option_id === id);
          const headingId = `requirement-option-${id}`;
          return (
            <section key={id} className="requirement-option" aria-labelledby={headingId}>
              <h3 id={headingId}>{optionLabel(selected, id)}</h3>
              <div className="requirement-option-name"><OptionName option={option} /></div>
              {decisions.length === 0 ? <p className="muted">No requirement decisions recorded.</p> : (
                <ul className="requirement-decisions-list">
                  {decisions.map((requirement) => (
                    <RequirementDecision key={requirement.requirement_id} requirement={requirement} optionId={id} />
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>
    </section>
  );
}

function Comparisons({ report, snapshot, context }: { report: Review; snapshot: ListingSnapshot; context: EvidenceContext }) {
  const optionIds = report.selected_option_ids;
  return (
    <section className="review-panel review-comparison-panel" aria-labelledby="comparison-heading">
      <h2 id="comparison-heading">Compare selected options</h2>
      {report.comparisons.length === 0 ? (
        <div className="review-no-comparisons">
          <p className="muted">The report has no comparison rows.</p>
          <ul>{optionIds.map((id) => (
            <li key={id}><strong>{optionLabel(optionIds, id)}</strong> <OptionName option={snapshot.options.find((option) => option.option_id === id)} /></li>
          ))}</ul>
        </div>
      ) : (
        <table className="review-comparison" role="table" aria-labelledby="comparison-heading">
          <thead role="rowgroup">
            <tr role="row">
              <th className="comparison-field" role="columnheader" scope="col">Listing detail</th>
              {optionIds.map((id) => (
                <th key={id} role="columnheader" scope="col">
                  <span className="review-kicker">{optionLabel(optionIds, id)}</span>
                  <OptionName option={snapshot.options.find((option) => option.option_id === id)} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody role="rowgroup">
            {report.comparisons.map((comparison, index) => (
              <tr key={index} role="row">
                <th role="rowheader" scope="row">
                  {comparison.label}
                  <ClaimEvidence claimIds={comparison.shared_claim_ids} context={context} showOptionScope={false} />
                </th>
                {optionIds.map((id) => {
                  const value = comparison.values.find((candidate) => candidate.option_id === id);
                  return (
                    <td key={id} role="cell">
                      <span className="comparison-mobile-option" aria-hidden="true">{optionLabel(optionIds, id)}</span>
                      {value?.claim_ids.length
                        ? <ClaimEvidence claimIds={value.claim_ids} context={context} showOptionScope={false} />
                        : <p className="muted">{value?.summary ?? "No comparison value provided."}</p>}
                      {value && value.limitations.length > 0 && (
                        <ul className="comparison-limitations muted">
                          {value.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}
                        </ul>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function FindingLinks({ ids, findings }: { ids: string[]; findings: ReviewFinding[] }) {
  return ids.length > 0 && (
    <ul className="review-finding-links">
      {ids.map((id) => {
        const finding = findings.find((candidate) => candidate.finding_id === id);
        return finding && <li key={id}><a href={`#${findingAnchor(id)}`}>{finding.title}</a></li>;
      })}
    </ul>
  );
}

function ReportCoverage({ report }: { report: Review }) {
  return (
    <section className="review-panel" aria-labelledby="coverage-heading">
      <h2 id="coverage-heading">Coverage and limitations</h2>
      <ul className="review-coverage">
        {report.coverage.map((coverage) => (
          <li key={coverage.group}>
            <h3>{coverageGroups[coverage.group]}</h3>
            <p className="review-coverage-status">{coverageStatuses[coverage.status]}</p>
            <p>{coverage.note}</p>
            <FindingLinks ids={coverage.finding_ids} findings={report.findings} />
          </li>
        ))}
      </ul>
      {report.limitations.length > 0 && (
        <div className="review-limits">
          <h3>Review limitations</h3>
          <ul>{report.limitations.map((limitation, index) => <li key={index}>{limitation}</li>)}</ul>
        </div>
      )}
    </section>
  );
}

function SupplierDraft({ draft, text, onChange }: { draft: string; text: string; onChange: (text: string) => void }) {
  const [copyResult, setCopyResult] = useState<"" | "success" | "failed">("");
  const [copying, setCopying] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);

  async function copyDraft() {
    setCopying(true);
    try {
      await navigator.clipboard.writeText(text);
      setCopyResult("success");
    } catch {
      setCopyResult("failed");
      input.current?.focus();
      input.current?.select();
    } finally {
      setCopying(false);
    }
  }

  return (
    <section className="review-panel review-draft" aria-labelledby="supplier-heading">
      <h2 id="supplier-heading">Message to supplier</h2>
      {draft.trim() ? (
        <>
          <label htmlFor="supplier-draft">Supplier message draft</label>
          <textarea id="supplier-draft" ref={input} rows={10} value={text} readOnly={copying}
            onChange={(event) => {
              onChange(event.currentTarget.value);
              setCopyResult("");
            }} />
          <div className="review-draft-actions">
            {text.trim() && <button className="button button-primary" type="button" disabled={copying} onClick={() => void copyDraft()}>
              Copy message
            </button>}
            {copyResult === "success" && <p role="status">Message copied.</p>}
            {copyResult === "failed" && <p role="alert" className="error">Could not copy the message. Select and copy the text manually.</p>}
          </div>
        </>
      ) : <p className="muted">This review has no questions for the supplier.</p>}
    </section>
  );
}

export function PurchaseReview({ report, snapshot, requirements, supplierDraft, onChangeDraft, onConfirmOrder }: {
  report: Review;
  snapshot: ListingSnapshot;
  requirements?: ResolvedRequirement[] | null;
  supplierDraft: string;
  onChangeDraft: (text: string) => void;
  onConfirmOrder: (clarification: OrderClarification) => void;
}) {
  const context = {
    snapshotId: snapshot.snapshot_id,
    selected: report.selected_option_ids,
    sources: new Map(snapshot.sources.map((source) => [source.source_id, source])),
    claims: new Map(report.claims.map((claim) => [claim.claim_id, claim])),
  };
  const otherRequirements = report.buyer_requirements.filter((requirement) => (
    !report.findings.some((finding) => finding.requirement_ids.includes(requirement.requirement_id))
  ));
  const orderConflicts = (requirements ?? []).filter((requirement) => requirement.order_conflict);
  const otherPreferences = report.open_preferences.filter((preference) => (
    !orderConflicts.some((requirement) => preference.preference_id === `requirement:${requirement.requirement_id}`)
  ));

  return (
    <div className="purchase-review">
      {orderConflicts.map((requirement) => <OrderClarificationForm key={requirement.requirement_id} requirement={requirement}
        snapshot={snapshot} selected={context.selected} onConfirm={onConfirmOrder} />)}
      {requirements && <RequirementDecisions requirements={requirements.filter((requirement) => !requirement.order_conflict)} selected={context.selected}
        snapshot={snapshot} />}
      <div className="review-workspace">
        <section className="review-findings" aria-labelledby="findings-heading">
          <h2 id="findings-heading">What to resolve</h2>
          {report.findings.length === 0 && <p className="review-panel">No findings were reported. See the coverage and limitations below.</p>}
          {report.findings.map((finding) => (
            <FindingCard key={finding.finding_id} finding={finding} report={report} context={context} />
          ))}
        </section>
        <Comparisons report={report} snapshot={snapshot} context={context} />
        <SupplierDraft draft={report.supplier_draft} text={supplierDraft} onChange={onChangeDraft} />
      </div>
      {((!requirements && otherRequirements.length > 0) || otherPreferences.length > 0) && (
        <section className="review-panel review-buyer-inputs" aria-labelledby="buyer-inputs-heading">
          <h2 id="buyer-inputs-heading">Buyer inputs</h2>
          {!requirements && otherRequirements.length > 0 && (
            <div>
              <h3>Other buyer requirements</h3>
              {otherRequirements.map((requirement) => (
                <Requirement key={requirement.requirement_id} requirement={requirement} selected={context.selected} />
              ))}
            </div>
          )}
          {otherPreferences.length > 0 && (
            <div>
              <h3>Open preferences</h3>
              <ul>
                {otherPreferences.map((preference) => {
                  const scope = narrowerScope(context.selected, preference.option_ids);
                  return (
                    <li key={preference.preference_id}>
                      <p>{preference.text}{scope && ` · ${scope}`}</p>
                      {preference.input_refs.includes("context") && <p className="muted">Your purchase context</p>}
                      {preference.questions.map((question) => {
                        const questionScope = narrowerScope(context.selected, question.option_ids);
                        return (
                          <p key={question.question_id} className="muted">
                            Question “{question.question_text}”{questionScope && ` · ${questionScope}`}
                          </p>
                        );
                      })}
                      <FindingLinks ids={preference.finding_ids} findings={report.findings} />
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </section>
      )}
      <ReportCoverage report={report} />
    </div>
  );
}
