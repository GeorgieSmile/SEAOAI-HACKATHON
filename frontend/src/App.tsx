import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { confirmSelection, getCurrentSnapshot, uploadListing } from "./api";
import type { ConfirmSelection, ListingSnapshot, Selection } from "./api";
import { Listing, OptionImage } from "./Listing";

type ImportState =
  | { status: "recovering" | "recovery-failed" | "replacement" | "empty" }
  | { status: "loading"; filename: string }
  | { status: "failed"; filename?: string; message: string }
  | { status: "ready"; filename?: string; snapshot: ListingSnapshot };

type ConfirmationState =
  | { status: "idle" }
  | { status: "saving" }
  | { status: "failed"; message: string }
  | { status: "confirmed"; selection: Selection };

const MAX_CONTEXT_LENGTH = 2_000;

export function App() {
  const [intake, setIntake] = useState<ImportState>({ status: "recovering" });
  const [selected, setSelected] = useState<string[]>([]);
  const [buyerContext, setBuyerContext] = useState("");
  const [selectionError, setSelectionError] = useState("");
  const [confirmation, setConfirmation] = useState<ConfirmationState>({ status: "idle" });
  const [dragging, setDragging] = useState(false);
  const generation = useRef(0);
  const pendingConfirmation = useRef<ConfirmSelection | null>(null);
  const saving = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const statusElement = useRef<HTMLParagraphElement>(null);
  const recovering = intake.status === "recovering";
  const recoveryFailed = intake.status === "recovery-failed";
  const replacing = intake.status === "replacement";
  const canUpload = !recovering && !recoveryFailed;

  useEffect(() => {
    if (!recovering) return;
    const controller = new AbortController();
    const activeGeneration = generation.current;
    const isCurrent = () => !controller.signal.aborted && generation.current === activeGeneration;
    void getCurrentSnapshot(controller.signal).then((snapshot) => {
      if (isCurrent()) {
        setIntake(snapshot ? { status: "ready", snapshot } : { status: "empty" });
      }
    }).catch(() => {
      if (isCurrent()) setIntake({ status: "recovery-failed" });
    });
    return () => controller.abort();
  }, [recovering]);

  useEffect(() => {
    if (confirmation.status === "confirmed") statusElement.current?.focus();
  }, [confirmation.status]);

  async function importFile(file: File) {
    if (!canUpload) return;
    const activeGeneration = ++generation.current;
    setSelected([]);
    setBuyerContext("");
    setSelectionError("");
    setConfirmation({ status: "idle" });
    pendingConfirmation.current = null;
    saving.current = false;
    setIntake({ status: "loading", filename: file.name });
    try {
      const snapshot = await uploadListing(file);
      if (activeGeneration === generation.current) {
        setIntake({ status: "ready", filename: file.name, snapshot });
      }
    } catch (error) {
      if (activeGeneration === generation.current) {
        setIntake({ status: "failed", filename: file.name, message: (error as Error).message });
      }
    }
  }

  function forgetConfirmation() {
    pendingConfirmation.current = null;
    setConfirmation({ status: "idle" });
  }

  function toggleSelection(id: string) {
    if (saving.current) return;
    if (selected.includes(id)) {
      setSelected(selected.filter((selectedId) => selectedId !== id));
    } else if (selected.length < 3) {
      setSelected([...selected, id]);
    } else {
      setSelectionError("Choose up to 3 options. Remove one before adding another.");
      return;
    }
    setSelectionError("");
    forgetConfirmation();
  }

  const contextLength = [...buyerContext].length;
  const contextTooLong = contextLength > MAX_CONTEXT_LENGTH;
  const selectionDisabled = confirmation.status === "saving";
  const snapshot = intake.status === "ready" ? intake.snapshot : null;

  async function submitSelection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!snapshot || selected.length < 1 || selected.length > 3 || contextTooLong || saving.current) return;

    const activeGeneration = generation.current;
    const payload = pendingConfirmation.current ?? {
      operation_id: crypto.randomUUID(),
      snapshot_id: snapshot.snapshot_id,
      previous_case: null,
      selected_option_ids: selected,
      buyer_context: buyerContext,
    };
    pendingConfirmation.current = payload;
    saving.current = true;
    setConfirmation({ status: "saving" });
    try {
      const selection = await confirmSelection(payload);
      if (activeGeneration === generation.current) setConfirmation({ status: "confirmed", selection });
    } catch (error) {
      if (activeGeneration === generation.current) setConfirmation({ status: "failed", message: (error as Error).message });
    } finally {
      if (activeGeneration === generation.current) saving.current = false;
    }
  }

  const filename = "filename" in intake ? intake.filename : undefined;
  const summaryIds = confirmation.status === "confirmed" ? confirmation.selection.selected_option_ids : selected;

  return (
    <>
      <header className="site-header">
        <div className="container site-header-inner">Before you order</div>
      </header>
      <main className="container">
        <div className="intro">
          <h1>Confirm what you’re buying</h1>
          <p>Upload a saved TTP Cargo listing, check the seller’s wording and photos, then confirm the exact options you want to buy.</p>
        </div>

        <section
          className={`upload${canUpload && dragging ? " is-dragging" : ""}`}
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
            if (file) void importFile(file);
          }}
        >
          <div className="upload-copy">
            <h2 id="upload-heading">Listing ZIP</h2>
            {recovering && <p className="muted" role="status">Checking for a saved listing…</p>}
            {recoveryFailed && <p className="error" role="alert">Could not check for a saved listing.</p>}
            {replacing && (
              <p className="replacement-warning" id="replacement-warning" role="status">
                Uploading a new ZIP can replace the saved listing and its evidence, even if the import fails.
              </p>
            )}
            {filename && (
              <p className="file-name">{filename}</p>
            )}
            {canUpload && !replacing && !filename && (
              <p className="muted">{snapshot ? "Current listing restored" : "Drop a .zip file here or choose one from your device. Up to 20 MiB."}</p>
            )}
            {intake.status === "loading" && <p className="muted" role="status">Reading listing and preparing translations…</p>}
            {intake.status === "failed" && <p className="error" role="alert">{intake.message}</p>}
          </div>
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
              if (file) void importFile(file);
            }}
          />
          <div className="upload-actions">
            {(recoveryFailed || replacing) && (
              <button className="button button-secondary" type="button" onClick={() => setIntake({ status: "recovering" })}>
                Retry saved listing
              </button>
            )}
            {canUpload ? (
              <button
                className="button button-secondary"
                type="button"
                aria-describedby={replacing ? "replacement-warning" : undefined}
                onClick={() => fileInput.current?.click()}
              >
                {replacing ? "Choose replacement ZIP" : filename || snapshot ? "Choose another ZIP" : "Choose ZIP"}
              </button>
            ) : (
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
        </section>

        {snapshot && (
          <form onSubmit={submitSelection}>
            <Listing
              key={snapshot.snapshot_id}
              snapshot={snapshot}
              selected={selected}
              disabled={selectionDisabled}
              selectionError={selectionError}
              onToggle={toggleSelection}
            />
            <section className="confirmation" aria-labelledby="confirm-heading">
              <div>
                <h2 id="confirm-heading">Confirm your selection</h2>
                {summaryIds.length === 0 ? <p className="muted">Choose an option to see it here.</p> : (
                  <ol className="selected-options" aria-label="Selected options in order">
                    {summaryIds.map((id, index) => {
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
                  disabled={selectionDisabled}
                  onChange={(event) => {
                    setBuyerContext(event.currentTarget.value);
                    forgetConfirmation();
                  }}
                />
                <p id="context-length" className={contextTooLong ? "error" : "muted"}>
                  {contextTooLong ? "Use 2,000 characters or fewer. " : ""}
                  {contextLength.toLocaleString("en-US")} / 2,000
                </p>
                {confirmation.status === "failed" && <p role="alert" className="error">{confirmation.message}</p>}
                {confirmation.status === "confirmed" ? (
                  <p className="confirmed" tabIndex={-1} ref={statusElement} role="status">
                    {summaryIds.length === 1 ? "1 option confirmed." : `${summaryIds.length} options confirmed.`}
                  </p>
                ) : (
                  <button
                    className="button button-primary"
                    type="submit"
                    disabled={selected.length === 0 || contextTooLong || selectionDisabled}
                  >
                    {selectionDisabled ? "Confirming…" : "Confirm options"}
                  </button>
                )}
              </div>
            </section>
          </form>
        )}
      </main>
    </>
  );
}
