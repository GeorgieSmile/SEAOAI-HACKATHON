import { useState } from "react";
import { sourceImageUrl } from "./api";
import type { ListingOption, ListingSnapshot, Source } from "./api";

function BilingualText({ original, translation }: {
  original: string | null;
  translation: string | null;
}) {
  return (
    <>
      {translation && <span className="translation">{translation}</span>}
      {original && <span className="original" lang="zh-Hans">{original}</span>}
      {!translation && <small className="translation-status">{original ? "English translation unavailable" : "Text unavailable"}</small>}
    </>
  );
}

export function OptionImage({ option, imageSource, snapshotId }: {
  option: ListingOption;
  imageSource: Source | undefined;
  snapshotId: string;
}) {
  const [failed, setFailed] = useState(false);

  if (!option.image_source_id) {
    return <span className="image-placeholder">No SKU-specific image</span>;
  }
  if (!imageSource || imageSource.availability === "missing") {
    return <span className="image-placeholder">Seller image unavailable</span>;
  }
  if (failed || imageSource.availability === "unreadable") {
    return <span className="image-placeholder">Seller image unreadable</span>;
  }
  return (
    <img
      alt=""
      src={sourceImageUrl(snapshotId, imageSource.source_id)}
      onError={() => setFailed(true)}
    />
  );
}

export function Listing({ snapshot, selected, disabled, selectionError, onToggle }: {
  snapshot: ListingSnapshot;
  selected: string[];
  disabled: boolean;
  selectionError: string;
  onToggle: (id: string) => void;
}) {
  const sources = new Map(snapshot.sources.map((source) => [source.source_id, source]));
  const titleSource = snapshot.sources.find(
    (source) => source.kind === "listing_text" && source.label === "Product title"
      && source.original_text === snapshot.title,
  );
  const sharedSources = snapshot.sources.filter(
    (source) => source.kind === "listing_text" && source.label === "Shared selector value",
  );
  const sellerDetails = snapshot.sources.filter(
    (source) => source.kind === "listing_text" && source !== titleSource
      && !sharedSources.includes(source),
  );

  return (
    <section aria-labelledby="listing-title" className="listing">
      <div className="listing-header">
        <div className="listing-identity">
          <p className="section-label">Seller listing</p>
          <h2 id="listing-title">
            <BilingualText original={snapshot.title} translation={titleSource?.text_en ?? null} />
          </h2>
        </div>
        {sharedSources.length > 0 && (
          <div className="shared-values">
            <h3>Applies to all options</h3>
            {sharedSources.map((source) => (
              <div key={source.source_id}>
                <BilingualText original={source.original_text} translation={source.text_en} />
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="options-heading">
        <div>
          <h3>Choose the options you want to buy</h3>
          <p id="selection-instructions" className="muted">
            Pick 1 to 3. The order you pick sets their order in the report.
          </p>
        </div>
        <p className="selection-count" role="status">{selected.length} of 3 selected</p>
      </div>
      {selectionError && <p role="alert" className="error options-error">{selectionError}</p>}
      <fieldset aria-label="Product options" aria-describedby="selection-instructions" disabled={disabled}>
        <div className="option-grid">
          {snapshot.options.map((option) => {
            const index = selected.indexOf(option.option_id);
            return (
              <label className="option" key={option.option_id}>
                <span className="option-image">
                  <OptionImage
                    option={option}
                    snapshotId={snapshot.snapshot_id}
                    imageSource={option.image_source_id ? sources.get(option.image_source_id) : undefined}
                  />
                  {index !== -1 && <span className="selection-order" aria-hidden="true">{index + 1}</span>}
                </span>
                <span className="option-name">
                  <input
                    type="checkbox"
                    value={option.option_id}
                    checked={index !== -1}
                    onChange={() => onToggle(option.option_id)}
                  />
                  <span>
                    {snapshot.linked_option_id === option.option_id && <strong className="linked-option">From your link</strong>}
                    <BilingualText original={option.name_original} translation={option.name_en} />
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>
      <a className="selection-jump button button-secondary" href="#confirm-heading">Go to your selection →</a>
      {(sellerDetails.length > 0 || snapshot.limitations.length > 0) && (
        <details className="seller-details">
          <summary>{sellerDetails.length ? "What the seller claims" : "Source limitations"}</summary>
          {sellerDetails.length > 0 && (
            <dl className="source-list">
              {sellerDetails.map((source) => (
                <div key={source.source_id}>
                  <dt>{source.label}</dt>
                  <dd><BilingualText original={source.original_text} translation={source.text_en} /></dd>
                </div>
              ))}
            </dl>
          )}
          {snapshot.limitations.length > 0 && (
            <div className="limitations">
              <h4>Source limitations</h4>
              <ul>{snapshot.limitations.map((limitation, index) => <li key={index}>{limitation}</li>)}</ul>
            </div>
          )}
        </details>
      )}
    </section>
  );
}
