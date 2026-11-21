import { useRef, useState } from "react";
import { sourceImageUrl } from "./api";
import type {
  CaseView, ListingOption, ListingSnapshot, PurchaseReview as Review, ReviewCitation, ReviewFinding, ReviewRequirement, Source,
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
}

function EvidenceSource({ context, id, citation }: {
  context: EvidenceContext;
  id: string;
  citation?: ReviewCitation;
}) {
  const source = context.sources.get(id);
  if (!source) return null;
  const scope = context.selected.filter((optionId) => source.option_ids.includes(optionId));
  const isGuidance = source.kind === "official_guidance";
  const isImage = source.kind === "listing_image";

  return (
    <details className="review-source">
      <summary>{source.label}</summary>
      <div className="review-source-body">
        <p className="muted">
          {isGuidance ? "Official guidance · curated reference" : isImage ? "Seller listing image" : "Seller listing claim"}
          {scope.length > 0 ? ` · ${scope.map((optionId) => optionLabel(context.selected, optionId)).join(", ")}`
            : !isGuidance && source.option_ids.length === 0 ? " · listing level" : ""}
        </p>
        {source.availability !== "available" ? (
          <p>{source.availability === "missing" ? "Missing from the saved listing." : "This source could not be read."}</p>
        ) : isImage ? (
          <>
            <EvidenceImage snapshotId={context.snapshotId} source={source} />
            {citation?.image_observation && <div><h4>Image observation</h4><p>{citation.image_observation}</p></div>}
          </>
        ) : (
          <>
            {citation?.quote && citation.quote !== source.original_text && (
              <div><h4>Cited excerpt</h4><p className="review-quote" lang={isGuidance ? undefined : "zh-Hans"}>{citation.quote}</p></div>
            )}
            <div><h4>Original source text</h4><p className="review-quote" lang={isGuidance ? undefined : "zh-Hans"}>{source.original_text}</p></div>
            <div><h4>English translation of the source</h4><p>{source.text_en ?? "English translation unavailable."}</p></div>
            {isGuidance && source.url?.startsWith("https://") && (
              <a href={source.url} target="_blank" rel="noreferrer">Open guidance document (new tab)</a>
            )}
          </>
        )}
      </div>
    </details>
  );
}

function requirementOrigin(inputRef: string, rounds: CaseView["rounds"]) {
  const question = rounds.flatMap((round) => round.questions)
    .find((candidate) => `answer:${candidate.question_id}` === inputRef);
  return question ? `Your answer to “${question.prompt}”` : "Your purchase context";
}

function Requirement({ requirement, rounds }: { requirement: ReviewRequirement; rounds: CaseView["rounds"] }) {
  return (
    <div className="review-requirement">
      <p>{requirement.text}</p>
      <blockquote>
        <p>{requirement.quote}</p>
        <footer>{requirementOrigin(requirement.input_ref, rounds)}</footer>
      </blockquote>
    </div>
  );
}

function FindingCard({ finding, report, context, rounds }: {
  finding: ReviewFinding;
  report: Review;
  context: EvidenceContext;
  rounds: CaseView["rounds"];
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
            {requirements.map((requirement) => <Requirement key={requirement.requirement_id} requirement={requirement} rounds={rounds} />)}
          </div>
        )}
        <div>
          <h4>Assessment from the evidence</h4>
          <p>{finding.interpretation}</p>
          <p>{finding.impact}</p>
          <p className="review-priority"><strong>Why it’s prioritized</strong> {finding.priority_reason}</p>
        </div>
        {finding.citations.length > 0 && (
          <section aria-label="Source evidence">
            <h4>Source evidence</h4>
            {finding.citations.map((citation, index) => (
              <EvidenceSource key={`${citation.source_id}-${index}`} id={citation.source_id}
                context={context} citation={citation} />
            ))}
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
                    .filter((sourceId) => !finding.citations.some((citation) => citation.source_id === sourceId))
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
                <th role="rowheader" scope="row">{comparison.label}</th>
                {optionIds.map((id) => {
                  const value = comparison.values.find((candidate) => candidate.option_id === id);
                  return (
                    <td key={id} role="cell">
                      <span className="comparison-mobile-option" aria-hidden="true">{optionLabel(optionIds, id)}</span>
                      <p>{value?.summary ?? "No comparison value provided."}</p>
                      {value?.source_ids.map((sourceId) => <EvidenceSource key={sourceId} context={context} id={sourceId} />)}
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

export function PurchaseReview({ report, snapshot, rounds, supplierDraft, onChangeDraft }: {
  report: Review;
  snapshot: ListingSnapshot;
  rounds: CaseView["rounds"];
  supplierDraft: string;
  onChangeDraft: (text: string) => void;
}) {
  const context = {
    snapshotId: snapshot.snapshot_id,
    selected: report.selected_option_ids,
    sources: new Map(snapshot.sources.map((source) => [source.source_id, source])),
  };
  const otherRequirements = report.buyer_requirements.filter((requirement) => (
    !report.findings.some((finding) => finding.requirement_ids.includes(requirement.requirement_id))
  ));

  return (
    <div className="purchase-review">
      <div className="review-workspace">
        <section className="review-findings" aria-labelledby="findings-heading">
          <h2 id="findings-heading">What to resolve</h2>
          {report.findings.length === 0 && <p className="review-panel">No findings were reported. See the coverage and limitations below.</p>}
          {report.findings.map((finding) => (
            <FindingCard key={finding.finding_id} finding={finding} report={report} context={context} rounds={rounds} />
          ))}
        </section>
        <Comparisons report={report} snapshot={snapshot} context={context} />
        <SupplierDraft draft={report.supplier_draft} text={supplierDraft} onChange={onChangeDraft} />
      </div>
      {(otherRequirements.length > 0 || report.open_preferences.length > 0) && (
        <section className="review-panel review-buyer-inputs" aria-labelledby="buyer-inputs-heading">
          <h2 id="buyer-inputs-heading">Buyer inputs</h2>
          {otherRequirements.length > 0 && (
            <div>
              <h3>Other buyer requirements</h3>
              {otherRequirements.map((requirement) => <Requirement key={requirement.requirement_id} requirement={requirement} rounds={rounds} />)}
            </div>
          )}
          {report.open_preferences.length > 0 && (
            <div>
              <h3>Open preferences</h3>
              <ul>
                {report.open_preferences.map((preference) => (
                  <li key={preference.preference_id}>
                    <p>{preference.text}</p>
                    {preference.input_refs.map((ref) => <p key={ref} className="muted">{requirementOrigin(ref, rounds)}</p>)}
                    <FindingLinks ids={preference.finding_ids} findings={report.findings} />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}
      <ReportCoverage report={report} />
    </div>
  );
}
