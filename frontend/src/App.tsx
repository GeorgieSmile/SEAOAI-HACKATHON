import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { caseVersion, currentPurchaseReview, getCurrentCase, importListing, resetCase } from "./api";
import type { CaseVersion, ListingSnapshot } from "./api";
import { Interview } from "./Interview";
import { Listing, OptionImage } from "./Listing";
import { useInterviewSession } from "./useInterviewSession";

type ImportState =
  | { status: "recovering" | "recovery-failed" | "replacement" | "empty" }
  | { status: "loading"; sourceLabel: string }
  | { status: "failed"; sourceLabel?: string; message: string }
  | { status: "ready"; sourceLabel?: string; snapshot: ListingSnapshot };

const MAX_CONTEXT_LENGTH = 2_000;

function isProductLink(value: string) {
  const prefix = "https://taobao.ttpcargo.com/product?";
  if (!value.startsWith(prefix)) return false;
  const fields = value.slice(prefix.length).split("&");
  const keys = new Set(fields.map((field) => field.split("=")[0]));
  return keys.size === fields.length && keys.has("provider") && keys.has("itemId")
    && fields.every((field) => /^(?:provider=(?:1688|taobao|tmall)|(?:itemId|skuId)=[1-9][0-9]{0,17})$/.test(field));
}

interface ResetTarget {
  snapshotId: string;
  previousCase: CaseVersion | null;
}

function matchesResetTarget(target: ResetTarget, listing: Awaited<ReturnType<typeof getCurrentCase>>) {
  return target.snapshotId === listing.snapshot?.snapshot_id
    && target.previousCase?.case_id === listing.case?.case_id
    && target.previousCase?.revision === listing.case?.revision;
}

const workMessages = {
  start: "Preparing your questions…",
  continue: "Saving answers and checking what else to ask…",
  review: "Preparing your purchase review…",
  retry: "Retrying your last request…",
  refresh: "Loading your saved progress…",
};

export function App() {
  const [intake, setIntake] = useState<ImportState>({ status: "recovering" });
  const [selected, setSelected] = useState<string[]>([]);
  const [buyerContext, setBuyerContext] = useState("");
  const [selectionError, setSelectionError] = useState("");
  const [editingSelection, setEditingSelection] = useState(true);
  const [resetTarget, setResetTarget] = useState<ResetTarget | null>(null);
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState("");
  const [resetStale, setResetStale] = useState(false);
  const [productLink, setProductLink] = useState("");
  const [dragging, setDragging] = useState(false);
  const [reportVisible, setReportVisible] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const generation = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const selectionTitle = useRef<HTMLHeadingElement>(null);
  const resetHeading = useRef<HTMLHeadingElement>(null);
  const startOverButton = useRef<HTMLButtonElement>(null);
  const interview = useInterviewSession();
  const restoreInterview = interview.restore;
  const recovering = intake.status === "recovering";
  const recoveryFailed = intake.status === "recovery-failed";
  const replacing = intake.status === "replacement";
  const resetPrompt = resetTarget !== null;
  const canUpload = !recovering && !recoveryFailed && !resetPrompt;

  useEffect(() => {
    if (!recovering) return;
    const controller = new AbortController();
    const activeGeneration = generation.current;
    const isCurrent = () => !controller.signal.aborted && generation.current === activeGeneration;
    void getCurrentCase(controller.signal).then(({ snapshot, case: saved }) => {
      if (isCurrent()) {
        setIntake(snapshot ? { status: "ready", snapshot } : { status: "empty" });
        if (snapshot && saved?.snapshot_id === snapshot.snapshot_id) {
          restoreInterview(saved);
          setSelected(saved.selected_option_ids);
          setBuyerContext(saved.buyer_context);
          setEditingSelection(false);
        }
      }
    }).catch(() => {
      if (isCurrent()) setIntake({ status: "recovery-failed" });
    });
    return () => controller.abort();
  }, [recovering, restoreInterview]);

  useEffect(() => {
    if (resetPrompt) resetHeading.current?.focus();
  }, [resetPrompt]);

  async function startImport(source: File | string) {
    if (!canUpload) return;
    const sourceLabel = typeof source === "string" ? source : source.name;
    if (typeof source !== "string") setProductLink("");
    const activeGeneration = ++generation.current;
    setShowImport(false);
    setSelected([]);
    setBuyerContext("");
    setSelectionError("");
    setEditingSelection(true);
    setResetError("");
    restoreInterview(null);
    setIntake({ status: "loading", sourceLabel });
    try {
      const snapshot = await importListing(source);
      if (activeGeneration === generation.current) {
        setIntake({ status: "ready", sourceLabel, snapshot });
        if (typeof source === "string") {
          setProductLink((current) => current.trim() === source ? "" : current);
        }
      }
    } catch (error) {
      if (activeGeneration === generation.current) {
        setIntake({ status: "failed", sourceLabel, message: (error as Error).message });
      }
    }
  }

  function toggleSelection(id: string) {
    if (selected.includes(id)) {
      setSelected(selected.filter((selectedId) => selectedId !== id));
    } else if (selected.length < 3) {
      setSelected([...selected, id]);
    } else {
      setSelectionError("Choose up to 3 options. Remove one before adding another.");
      return;
    }
    setSelectionError("");
  }

  const contextLength = [...buyerContext].length;
  const contextTooLong = contextLength > MAX_CONTEXT_LENGTH;
  const snapshot = intake.status === "ready" ? intake.snapshot : null;
  // A failed or in-flight POST can leave the saved workspace unknown to this tab.
  const showReplacementWarning = canUpload && intake.status !== "empty";
  const activeInterview = snapshot && interview.view?.snapshot_id === snapshot.snapshot_id
    && interview.view.buyer_context === buyerContext && interview.view.selected_option_ids.length === selected.length
    && interview.view.selected_option_ids.every((id, index) => id === selected[index])
    ? interview.view
    : null;
  const savedInterviewChanged = !editingSelection && interview.view !== null && activeInterview === null
    && !interview.error && !interview.work;
  const mustReloadInterview = interview.requiresReload || savedInterviewChanged;

  async function clearListing() {
    if (!resetTarget || resetting || resetStale) return;
    setResetting(true);
    setResetError("");
    interview.suspend();
    try {
      const saved = await getCurrentCase();
      if (!matchesResetTarget(resetTarget, saved)) {
        setResetError("The saved listing or answers changed. Reload saved progress before confirming again.");
        setResetStale(true);
        setResetting(false);
        return;
      }
      await resetCase(resetTarget.snapshotId, resetTarget.previousCase);
    } catch (error) {
      const saved = await getCurrentCase().catch(() => null);
      if (!saved || saved.snapshot !== null || saved.case !== null) {
        const changed = saved !== null && !matchesResetTarget(resetTarget, saved);
        setResetStale(changed);
        setResetError(changed
          ? "The saved listing or answers changed. Reload saved progress before confirming again."
          : error instanceof Error ? error.message : "Could not clear the saved listing.");
        setResetting(false);
        return;
      }
    }
    ++generation.current;
    restoreInterview(null);
    setShowImport(false);
    setSelected([]);
    setBuyerContext("");
    setSelectionError("");
    setIntake({ status: "empty" });
    setProductLink("");
    setEditingSelection(true);
    setResetTarget(null);
    setResetting(false);
    requestAnimationFrame(() => document.getElementById("upload-heading")?.focus());
  }

  function reloadListing() {
    ++generation.current;
    restoreInterview(null);
    setShowImport(false);
    setSelected([]);
    setBuyerContext("");
    setSelectionError("");
    setEditingSelection(true);
    setResetTarget(null);
    setResetError("");
    setResetStale(false);
    setIntake({ status: "recovering" });
  }

  function beginQuestions() {
    if (!snapshot || resetPrompt || mustReloadInterview || selected.length < 1 || selected.length > 3 || contextTooLong) return;
    setEditingSelection(false);
    void interview.start({
      snapshot_id: snapshot.snapshot_id,
      selected_option_ids: selected,
      buyer_context: buyerContext,
    });
  }

  function submitSelection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (resetPrompt || mustReloadInterview) return;
    if (snapshot && activeInterview) {
      setEditingSelection(false);
      if (interview.needsRefresh) void interview.refresh(snapshot.snapshot_id);
    } else {
      beginQuestions();
    }
  }

  const sourceLabel = "sourceLabel" in intake ? intake.sourceLabel : undefined;

  const progress = !snapshot || editingSelection ? 1 : reportVisible ? 3 : 2;
  const steps = ["Choose options", "Buyer questions", "Evidence report"];
  const pageTitle = !snapshot ? "Import a listing" : steps[progress - 1];
  useEffect(() => {
    document.title = `${pageTitle} · Proofly`;
  }, [pageTitle]);

  const replaceButton = (
    <button className="button button-secondary" type="button" aria-expanded={showImport} aria-controls="import-panel"
      onClick={() => setShowImport(!showImport)}>{showImport ? "Close" : editingSelection ? "Replace" : "Replace listing"}</button>
  );

  return (
    <>
      <header className="site-header">
        <div className="container site-header-inner">
          <span className="brand"><img src="/proofly-icon.svg" alt="" width="36" height="36" />proofly</span>
          <ol className="steps" aria-label="Case progress">
            {steps.map((step, index) => <li key={step} aria-current={progress === index + 1 ? "step" : undefined}
              className={index + 1 < progress ? "done" : ""}><b aria-hidden="true">{index + 1 < progress ? "✓" : index + 1}</b>{step}</li>)}
          </ol>
          {snapshot && !resetPrompt && (
            <div className="header-actions">
              {!editingSelection && replaceButton}
              <button ref={startOverButton} className="button button-secondary" type="button" onClick={() => {
                setResetTarget({
                  snapshotId: snapshot.snapshot_id,
                  previousCase: caseVersion(interview.view),
                });
              }}>
                New listing
              </button>
            </div>
          )}
        </div>
      </header>
      <main className={`container ${snapshot ? "workspace" : "landing"}`}>
        {(!snapshot || editingSelection) && (
          <div className="intro">
            <span className="eyebrow">{snapshot ? "Step 1 of 3" : "Check a seller before you pay"}</span>
            <h1>{snapshot ? "Confirm what you’re buying" : "Know what you’re buying before you order"}</h1>
            <p>{snapshot ? "Check the seller’s wording and photos, then pick up to three options."
              : "Paste a TTP Cargo product link. Check the seller’s wording, choose your options, and get an evidence report with questions to send the seller."}</p>
          </div>
        )}

        {snapshot && editingSelection && !resetPrompt && (
          <div className="listing-source">
            <svg className="source-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z M14 3v5h5 M8 14l2.5 2.5L16 11" />
            </svg>
            <div><strong>{sourceLabel ?? "Current listing restored"}</strong><small>{snapshot.options.length} options found</small></div>
            {replaceButton}
          </div>
        )}
        <section id="import-panel" hidden={Boolean(snapshot && !showImport && !resetPrompt)}
          className={`upload${canUpload && dragging ? " is-dragging" : ""}${snapshot && !editingSelection ? " interview-upload" : ""}`}
          aria-labelledby="upload-heading"
          onDragOver={(event) => {
            if (event.dataTransfer.types.includes("Files")) {
              event.preventDefault();
              event.dataTransfer.dropEffect = canUpload ? "copy" : "none";
              setDragging(canUpload);
            }
          }}
          onDragLeave={(event) => {
            if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) {
              setDragging(false);
            }
          }}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            const file = event.dataTransfer.files.item(0);
            if (file) void startImport(file);
          }}
        >
          <div className="upload-copy">
            <svg className="link-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="m10 13 4-4m-6 2-2 2a4 4 0 0 0 6 6l3-3m-1-4 2-2a4 4 0 0 0-6-6L7 7" />
            </svg>
            <h2 id="upload-heading" tabIndex={-1}>Import a listing</h2>
            {snapshot && !editingSelection && showImport && <p className="muted">{sourceLabel ?? "Current listing restored"} · {snapshot.options.length} options found</p>}
            {recovering && <p className="muted" role="status">Checking for a saved listing…</p>}
            {recoveryFailed && <p className="error" role="alert">Could not check for a saved listing.</p>}
            {sourceLabel && !snapshot && (
              <p className="file-name">{sourceLabel}</p>
            )}
            {!snapshot && <p className="muted">Supports 1688, Taobao, and Tmall product links.</p>}
            {intake.status === "loading" && <p className="muted" role="status">Reading listing and preparing translations…</p>}
            {intake.status === "failed" && <p className="error" role="alert">{intake.message}</p>}
          </div>
          {canUpload && (
            <form className="link-import" onSubmit={(event) => {
              event.preventDefault();
              const link = productLink.trim();
              if (!isProductLink(link)) {
                const field = event.currentTarget.elements.namedItem("productLink") as HTMLInputElement;
                field.setCustomValidity("Use a TTP Cargo link with provider 1688, taobao, or tmall, an itemId, and optionally a skuId.");
                field.reportValidity();
                return;
              }
              void startImport(link);
            }}>
              {showReplacementWarning && (
                <p className="replacement-warning" id="replacement-warning">
                  Starting a link or ZIP import clears any saved listing, photos, and answers, even if the new import fails.
                </p>
              )}
              <label htmlFor="product-link">TTP Cargo product link</label>
              <div className="link-controls">
                <input id="product-link" name="productLink" type="url" required maxLength={512}
                  value={productLink} onChange={(event) => {
                    event.currentTarget.setCustomValidity("");
                    setProductLink(event.currentTarget.value);
                  }}
                  placeholder="https://taobao.ttpcargo.com/product?…" />
                <button className="button button-primary" type="submit" disabled={intake.status === "loading"}
                  aria-describedby={showReplacementWarning ? "replacement-warning" : undefined}>
                  Import link
                </button>
              </div>
            </form>
          )}
          <input
            ref={fileInput}
            type="file"
            accept=".zip,application/zip,application/x-zip-compressed"
            aria-label="Listing ZIP file"
            hidden
            disabled={!canUpload}
            onChange={(event) => {
              const file = event.currentTarget.files?.item(0);
              event.currentTarget.value = "";
              if (file) void startImport(file);
            }}
          />
          <div className="upload-actions">
            {canUpload && <span className="zip-hint muted">Or drop a saved ZIP here (up to 20 MiB)</span>}
            {(recoveryFailed || replacing) && (
              <button className="button button-secondary" type="button" onClick={() => setIntake({ status: "recovering" })}>
                Retry saved listing
              </button>
            )}
            {canUpload && (
              <button
                className="button button-secondary"
                type="button"
                aria-describedby={showReplacementWarning ? "replacement-warning" : undefined}
                onClick={() => fileInput.current?.click()}
              >
                {replacing ? "Choose replacement ZIP" : sourceLabel || snapshot ? "Choose another ZIP" : "Choose ZIP"}
              </button>
            )}
            {(recovering || recoveryFailed) && (
              <button
                className="button button-secondary"
                type="button"
                onClick={() => {
                  ++generation.current;
                  setIntake({ status: "replacement" });
                }}
              >
                Start a new listing
              </button>
            )}
          </div>
          {resetPrompt && (
            <div className="reset-confirmation" role="group" aria-labelledby="reset-heading">
              <h3 id="reset-heading" ref={resetHeading} tabIndex={-1}>Clear the saved listing and answers?</h3>
              <p className="muted">Starting over removes the current listing, photos and answers from this app.</p>
              {resetError && <p className="error" role="alert">{resetError}</p>}
              <div className="upload-actions">
                <button className="button button-secondary" type="button" disabled={resetting} onClick={() => {
                  setResetTarget(null);
                  setResetError("");
                  setResetStale(false);
                  requestAnimationFrame(() => startOverButton.current?.focus());
                }}>Cancel</button>
                {resetStale ? (
                  <button className="button button-secondary" type="button" onClick={reloadListing}>Reload saved progress</button>
                ) : (
                  <button className="button button-danger" type="button" disabled={resetting} onClick={() => void clearListing()}>
                    {resetting ? "Clearing…" : "Clear listing and answers"}
                  </button>
                )}
              </div>
            </div>
          )}
        </section>

        {!snapshot && <ol className="step-cards">
          <li><strong>Choose options</strong><p>Pick 1–3 options with seller photos and Chinese names side by side.</p></li>
          <li><strong>Answer a few questions</strong><p>Up to three rounds of five questions, tailored to your answers. Skip anytime.</p></li>
          <li><strong>Get your report</strong><p>See findings with evidence and questions you can copy and send.</p></li>
        </ol>}
        {snapshot && editingSelection && (
          <form className="selection-layout" onSubmit={submitSelection}>
            <Listing
              key={snapshot.snapshot_id}
              snapshot={snapshot}
              selected={selected}
              disabled={false}
              selectionError={selectionError}
              onToggle={toggleSelection}
            />
            <section className="confirmation" aria-labelledby="confirm-heading">
              <div>
                <h2 id="confirm-heading" tabIndex={-1} ref={selectionTitle}>Your selection</h2>
                {selected.length === 0 ? <p className="muted">Choose an option to see it here.</p> : (
                  <ol className="selected-options" aria-label="Selected options in order">
                    {selected.map((id, index) => {
                      const option = snapshot.options.find((candidate) => candidate.option_id === id);
                      if (!option) return null;
                      const imageSource = snapshot.sources.find((source) => source.source_id === option.image_source_id);
                      return (
                        <li key={id}>
                          <span className="summary-index" aria-hidden="true">{index + 1}</span>
                          <div className="summary-image">
                            <OptionImage option={option} imageSource={imageSource} snapshotId={snapshot.snapshot_id} />
                          </div>
                          <div>
                            {option.name_en && <strong>{option.name_en}</strong>}
                            <span className="original" lang="zh-Hans">{option.name_original}</span>
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                )}
              </div>
              <div className="confirmation-fields">
                <label htmlFor="buyer-context">What are you buying for? <span className="muted">(optional)</span></label>
                <textarea
                  id="buyer-context"
                  rows={3}
                  placeholder="Purpose, customers, or requirements you already know"
                  value={buyerContext}
                  aria-invalid={contextTooLong || undefined}
                  aria-describedby="context-length"
                  onChange={(event) => setBuyerContext(event.currentTarget.value)}
                />
                <p id="context-length" className={contextTooLong ? "error" : "muted"}>
                  {contextTooLong ? "Use 2,000 characters or fewer. " : ""}
                  {contextLength.toLocaleString("en-US")} / 2,000
                </p>
                {currentPurchaseReview(interview.view) && !activeInterview && (
                  <p className="muted">Confirming changes starts a new review with a new supplier draft.</p>
                )}
                <button
                  className="button button-primary"
                  type="submit"
                  disabled={resetPrompt || mustReloadInterview || selected.length === 0 || contextTooLong}
                >
                  {activeInterview
                    ? currentPurchaseReview(activeInterview) ? "Resume purchase review" : "Resume questions"
                    : "Confirm options"}
                </button>
                <p className="next-note">Next: up to three short rounds of questions, or skip straight to the report.</p>
              </div>
            </section>
          </form>
        )}
        {snapshot && (!editingSelection || interview.view) && (
          <Interview
            key={interview.view?.case_id ?? snapshot.snapshot_id}
            hidden={editingSelection}
            onReportVisibilityChange={setReportVisible}
            snapshot={snapshot}
            selected={selected}
            context={buyerContext}
            view={activeInterview}
            busy={interview.work !== null}
            blocked={resetPrompt}
            workingMessage={interview.work ? workMessages[interview.work] : ""}
            error={savedInterviewChanged ? "The saved interview changed. Reload saved progress to open it." : interview.error}
            needsRefresh={interview.needsRefresh || mustReloadInterview}
            onChangeSelection={mustReloadInterview ? undefined : () => {
              interview.suspend();
              setEditingSelection(true);
              requestAnimationFrame(() => selectionTitle.current?.focus());
            }}
            onStartAgain={beginQuestions}
            onRetry={() => { if (activeInterview) interview.retry(activeInterview); }}
            onRefresh={() => {
              if (mustReloadInterview) reloadListing();
              else void interview.refresh(snapshot.snapshot_id);
            }}
            onSubmit={(submission, action) => activeInterview
              ? interview.submit(activeInterview, submission, action)
              : Promise.resolve(null)}
          />
        )}
      </main>
    </>
  );
}
