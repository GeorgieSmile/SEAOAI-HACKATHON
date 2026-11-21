# DiliDoo! — Purchase Review

Built for the Sea × OpenAI Regional Codex Hackathon in Thailand on 21 November 2026.

**[Open the live demo](https://dilidoo-production.up.railway.app)** · The app opens directly into a private browser workspace.

## Project write-up

Buyers sourcing products for resale can receive a different variant from the one they expected. In the case that inspired DiliDoo!, a buyer expected a screen-printed design and received a sticker. The clue was buried in the Chinese option label.

DiliDoo! turns a TTP Cargo product link or saved listing ZIP into a pre-order review for 1–3 selected SKUs. It brings together the original Chinese listing, English translations, SKU images, and the buyer’s requirements. It asks follow-up questions when the answers could change what to check, shows the buyer an editable brief to confirm, and produces an English report with evidence, open questions, and a supplier message the buyer can edit and copy.

### Build directions

- **AI-Native Products & Operations.** The buyer's answers shape what the app asks next and what the final review checks. The same listing can produce different reviews for a buyer who needs ready-made stock and one who can accept custom production.
- **Deep Domain AI.** The review follows how sourcing buyers check an order. It ties listing claims to the relevant SKU, tracks buyer requirements, and turns missing or conflicting evidence into supplier questions. It records whether a photo was omitted from the review or reviewed and found unreadable. If a capacity selector says 720 ml and the selected SKU photo says 750 ml, the report can show both sources and ask the supplier to confirm the capacity for that SKU.

## Try the demo

1. Open the demo and click **Import link** to use the prefilled [sample TTP Cargo listing](https://taobao.ttpcargo.com/product?provider=1688&itemId=1035537244639) or upload the [redacted listing ZIP](samples/raw-save/raw-save-redacted.zip).
2. Select 1–3 variants and add a buying requirement. Answer the follow-up questions or open **Review buying brief** early. Review the requirements by SKU, edit them if needed, and select **Confirm brief and generate review**.
   To change a submitted answer, go to **Saved answers**, click **Edit** beside its round, and update the answer. Click **Preview answer changes**, scroll to the preview, then click **Save answer changes**.
3. In **Before you order**, open a finding to inspect the original text or SKU image. Review and copy the supplier message.

## How we used Codex

We used Codex to implement and integrate the revision-confirmed buying brief, including edit and undo previews, conditional requirements by SKU, and guards against confirming a stale revision. We also used Codex to review the UI and backend integration and exercise the purchase flow in Chrome with a real saved listing. One walkthrough checked that allowing a custom order for the blue cup kept its delivery deadline and left the other two variants ready-made only, including after a restart. [Buying brief implementation](https://github.com/GeorgieSmile/SEAOAI-HACKATHON/commit/95131901dbfa35c0f67b23a08bdbdf7537975d11) · [Verification notes](plans/purchase-02-buyer-questions/verification.md)

The app uses the OpenAI Responses API for listing translation, adaptive questions, and evidence-backed review generation.

## Hackathon declaration

**Prepared before the event.** We brought the problem brief, interface contracts, test plans, a static UI mock, and sample data from a saved TTP Cargo listing. The [starter commit](https://github.com/GeorgieSmile/SEAOAI-HACKATHON/commit/55bbc64) records those materials. [Data provenance](samples/README.md) · [Pre-event handoff](HANDOFF_CHECKS.md)

**Built on 21 November 2026.** We started the application code from scratch on-site in this hackathon repo. We built link and ZIP import, SKU selection and translation, adaptive buyer questions, an editable buying brief, and a report that opens the original evidence and provides a copyable supplier message. We added SQLite recovery, a durable report queue, and a public Railway demo with a separate workspace for each browser. [First app commit](https://github.com/GeorgieSmile/SEAOAI-HACKATHON/commit/a79ec3e) · [Hosted demo implementation](https://github.com/GeorgieSmile/SEAOAI-HACKATHON/commit/c2c7943a68b4897925c7205b3c1b1eea641bd8bb)

**Third-party dependencies and assets.** The app uses React, Vite, FastAPI, and the OpenAI Python SDK. The listing sample comes from TTP Cargo, and the bundled fonts are Inter and Space Grotesk. [Frontend dependencies](frontend/package.json) · [Backend dependencies](backend/requirements.txt) · [Inter license](frontend/public/fonts/inter-LICENSE.txt) · [Space Grotesk license](frontend/public/fonts/space-grotesk-LICENSE.txt)

## Hosted Railway demo

One HTTPS link serves the React build and the API. Opening the link gives each browser profile a private case, SKU images, answers and report. Tabs in the same profile share progress. Clearing cookies or switching browsers starts another workspace. Cases expire after 48 hours of inactivity by default.

Deploy the repository root using its Dockerfile and `railway.toml`. Attach one Railway volume at `/data`, use one replica and one Uvicorn worker, and generate one HTTPS domain. The healthcheck at `/healthz` opens the volume-backed registry and uses no provider requests. Use an empty volume for the first deployment. Reuse that same volume for redeployments and rollbacks so returning browsers can reopen their cases. Keep the local demo database and `.env` on the developer machine. [Railway volumes](https://docs.railway.com/volumes)

| Backend variable | Hosted setting |
|---|---|
| `DEMO_ORIGIN` | Exact HTTPS origin, including the scheme, without a trailing slash |
| `DEMO_REPORT_CONCURRENCY` | 1–10 workers for report generation inside the single process. Default 2. Choose the live setting after checking the OpenAI project's limits. |
| `OPENAI_API_KEY` and `OPENAI_LIVE_TRANSLATION`, `OPENAI_LIVE_QUESTIONS`, `OPENAI_LIVE_REVIEW` | Set the key as a service secret and set approved stages to 1 when live use is authorized. Keep disabled for infrastructure checks. |
| `DEMO_SESSION_TTL_HOURS` | Inactive case lifetime. Default 48 hours, with at most 100 stored browser workspaces. |

Confirming a brief saves its report request. The browser can refresh while waiting or generating. Following a service restart, waiting reports resume in order, completed reports reopen, and an interrupted generation offers an explicit retry. An interrupted provider request may already have incurred a charge. Back up the volume before release changes. Paid stages have no application-level request cap. To suspend paid use, disable the three live AI stages and restart. To roll back, redeploy the last known-good **hosted** image using the same volume.

For a local rehearsal with scripted providers, run these commands from the repository root after installing the dependencies below. The test server binds to loopback. Open `http://127.0.0.1:18187`. Reuse the data directory when rehearsing a restart.

```sh
npm --prefix frontend run build
.venv/bin/python -m backend.tests.hosted_server --data-dir backend/.local/hosted-rehearsal
```

## Run locally

Run from the repository root on the demo machine. Install dependencies once using the Python and Node versions in the [toolchain guide](docs/toolchain.md).

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
npm --prefix frontend ci
```

To test without spending API credits, start the offline backend in the first terminal. It imports real product links or ZIPs, displays the original Chinese names, and uses scripted questions and reports. Choose “Custom orders are acceptable” and select **Continue** for the next question round. To test a conditional purchase, select three SKUs with exactly one blue variant, enter “Ready-made only. All three must arrive by 1 December.” Then edit the brief to say “Actually, custom is fine for the blue cup if it still arrives by then.” Reuse the database path to test case recovery after a restart. Omitting `--database` uses a temporary database for that run.

```sh
.venv/bin/python -m backend.tests.offline_server --database backend/.local/offline-demo.sqlite3
```

Start the frontend in a second terminal from the same repository root.

```sh
npm --prefix frontend run dev
```

Open the [local app](http://127.0.0.1:5173), use the prefilled [sample product link](https://taobao.ttpcargo.com/product?provider=1688&itemId=1035537244639), and select **Import link**. The importer supports 1688, Taobao, and Tmall. If the link contains a `skuId`, the corresponding variant is marked **From your link** for you to select and confirm. If the source site is unavailable or the demo machine is offline, use **Choose ZIP** with the [redacted browser save](samples/raw-save/raw-save-redacted.zip). Select 1–3 SKUs in your preferred order, add optional buying context, and start the question round. Reopen the same URL to see the saved case and answers.

To run the regular backend, stop the offline server and run the command below. The app stores its SQLite database in `backend/.local/`. Live API calls incur charges and require approval. Set `OPENAI_API_KEY` in the backend process environment and enable only the approved stages. Use `OPENAI_LIVE_TRANSLATION=1` for translation, `OPENAI_LIVE_QUESTIONS=1` for questions, and `OPENAI_LIVE_REVIEW=1` for reports. The app reports an error at any disabled stage it reaches. Keep the key local, for example in the Git-ignored `backend/.env`, and load that file into the backend process environment explicitly.

```sh
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

## Verification

The [report verification notes](plans/purchase-03-evidence-review/verification.md) document real-ZIP browser walkthroughs and earlier live model checks. The [public demo verification](plans/purchase-04-integrate-and-verify/verification.md#public-railway-entry-and-report-evidence-correction) covers browser isolation, report queue recovery after restart, and evidence correction with scripted providers. The [Railway release checks](plans/purchase-04-integrate-and-verify/verification.md#railway-release-checks) record the live report and the subsequent translation-prompt deployment.

## Pre-event planning and handoff

The sections below record how the team prepared and split the work before building the app.

### First 30 minutes

- **B** prepares the repo and demo machine, owns the commands for starting both services from the repo root, and records the demo URL in the README as soon as the app runs. Use the backend's allowed origin, `http://127.0.0.1:5173`. Run one backend worker and test a fresh process with A.
- **A** starts the UI from the linked fixture and writes smoke-test steps that identify the file to select, actions to take, and expected results. Start with upload, SKU selection, and confirmation.
- **C** shares schemas and function signatures, checks API access, and marks each check as **deterministic** (using fixtures or stubs) or **live** (using API credits). Run the deterministic suite while waiting for an API key.

### Where to start

1. Read the problem, goals, and scope in the [project brief](docs/brief.md).
2. Find the team roles and four tickets in the [team plan](docs/team-plan.md), then open your assigned ticket.
3. Review the handoffs and checkpoints in the [six-hour schedule](docs/integration-plan.md#%E0%B8%95%E0%B8%B2%E0%B8%A3%E0%B8%B2%E0%B8%87-6-%E0%B8%8A%E0%B8%B1%E0%B9%88%E0%B8%A7%E0%B9%82%E0%B8%A1%E0%B8%87).

Read the [spec](docs/spec.md) before building. AI coding agents start with [AGENTS.md](AGENTS.md). At handoff, the team still needed to assign roles A, B, and C and pick a demo machine.

| Role | Main references | First tasks |
| --- | --- | --- |
| A — Frontend and demo | [Report contract](docs/contracts/report-contract.md), [question contract](docs/contracts/question-contract.md) | Build the page structure and linked fixture with shared IDs and prompts. |
| B — ZIP intake, backend, and integration | [Intake contract](docs/contracts/intake-contract.md), [integration plan](docs/integration-plan.md) | Set up the repo baseline and handle real ZIP input, state, routes, and image serving. |
| C — Schemas, AI, and quality | [AI plan](docs/ai-execution-plan.md), [report contract](docs/contracts/report-contract.md), [question contract](docs/contracts/question-contract.md) | Define schemas and function names, then verify API access and structured outputs. |

### Taking work and opening PRs

Each ticket has role-specific checklists and verification criteria. For example, A starts with the [PR-01 role checklist](plans/purchase-01-import-and-select/plan.md#%E0%B8%87%E0%B8%B2%E0%B8%99%E0%B8%82%E0%B8%AD%E0%B8%87%E0%B9%81%E0%B8%95%E0%B9%88%E0%B8%A5%E0%B8%B0%E0%B8%84%E0%B8%99%E0%B8%81%E0%B9%88%E0%B8%AD%E0%B8%99%E0%B9%80%E0%B8%9B%E0%B8%B4%E0%B8%94-pr) and runs [V01-A](plans/purchase-01-import-and-select/verification.md#%E0%B8%95%E0%B8%A3%E0%B8%A7%E0%B8%88%E0%B8%AA%E0%B9%88%E0%B8%A7%E0%B8%99%E0%B8%82%E0%B8%AD%E0%B8%87%E0%B8%95%E0%B8%99%E0%B8%81%E0%B9%88%E0%B8%AD%E0%B8%99%E0%B9%80%E0%B8%9B%E0%B8%B4%E0%B8%94-pr) before handing off their part.

Adapt the [example coding-agent prompt](docs/team-plan.md#%E0%B8%95%E0%B8%B1%E0%B8%A7%E0%B8%AD%E0%B8%A2%E0%B9%88%E0%B8%B2%E0%B8%87%E0%B8%84%E0%B8%B3%E0%B8%AA%E0%B8%B1%E0%B9%88%E0%B8%87%E0%B9%83%E0%B8%AB%E0%B9%89-coding-agent) to your role, ticket, and verification criteria. Read the [PR and evidence guidelines](docs/team-plan.md#%E0%B8%A7%E0%B8%B4%E0%B8%98%E0%B8%B5%E0%B8%AA%E0%B9%88%E0%B8%87-pr-%E0%B8%82%E0%B8%AD%E0%B8%87%E0%B9%81%E0%B8%95%E0%B9%88%E0%B8%A5%E0%B8%B0%E0%B8%84%E0%B8%99) before opening a PR. Close tickets after the integrated checks and reviews required by the team plan.

### Reference materials

- PR-01 uses two ZIPs for the same product. The raw save preserves the browser-save structure and Finder filenames. [Curated ZIP](samples/listing.zip) · [Raw ZIP](samples/raw-save/raw-save-redacted.zip) · [Sample usage and limitations](samples/README.md)
- Use excerpts from official sources to inform buyer questions. Product claims still need evidence for the selected item. [Source excerpts](samples/official-sources.json) · [Reference pack](docs/reference-pack.md)
- The report and question examples are fixtures for frontend development and tests. Keep expected answers out of model prompts. [Report example](docs/contracts/examples/report-contract-example.json) · [Question examples](docs/contracts/examples/question-contract-examples.json)
- The static mock shows the earlier one-round design and how evidence opens. Its ZIP and AI behavior is simulated. Follow spec v0.16 for the working flow and keep simulated analysis in the mock. [UI mock](references/mock-reference.html) · [Spec](docs/spec.md)
- Proposed dependencies and the checks that remained open on team machines. [Toolchain guide](docs/toolchain.md)
- Checks and limitations of the pre-event handoff package. [Handoff checks](HANDOFF_CHECKS.md)
