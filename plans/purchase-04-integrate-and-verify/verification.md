# PR-04 verification

Status: not run; no app implementation

- Branch/base และ tested SHA: pending
- ส่งต่อได้แล้ว: ยังไม่มีผลจากโค้ดจริง เริ่มงานจาก interface/fixture ได้
- Blocker และเจ้าของ: pending ให้ผู้ประสาน ticket อัปเดตเมื่อพบ
- รอตรวจเพื่อส่งต่อ: ทีมเดโมใช้ SHA ที่ผ่าน gate รวมและ restart/rehearsal ตามเกณฑ์ด้านล่าง
- Gate รวม: pending ตามเกณฑ์ด้านล่าง
Authority: [plan](plan.md), [T1–T5 และ integration gates](../../docs/integration-plan.md#ชุดกรณีตรวจที่ใช้ร่วมกัน)

## ตรวจส่วนของตนก่อนเปิด PR

ทุกรายการยัง not run ใช้ [รูปแบบส่งงาน](../../docs/team-plan.md#วิธีส่ง-pr-ของแต่ละคน) ส่ง harness/wiring ที่ตรวจแยกแล้วได้ก่อน และระบุ gate จริง/review/rehearsal ที่ยังไม่รันเป็น pending ส่วนการปิด ticket ต้องครบตามตารางรวม

| ID / เจ้าของ | วิธีตรวจและผลที่ต้องได้ | หลักฐานก่อนส่งและก่อนปิดงาน |
| --- | --- | --- |
| V04-A / A | รัน frontend type check/build และ deterministic UI flow ที่เพิ่ม ตรวจ UI จริง ZIP→เลือก→คำถามปรับตามคำตอบ/0/partial/skip/edit→report→source/copy รวมผลเก่า/failed; ประสานรีวิว integrated commit แล้ว restart/rehearse กับ B | ก่อนส่ง harness: คำสั่ง/ผลและสิ่งที่ยัง stub; ก่อนปิด: UI actions จริง, review briefs/results/dispositions/ledger ครบ 4 ใบ และ restart/rehearsal record |
| V04-B / B | รัน HTTP/security/state ชุดรวมบน integrated state ตรวจ malicious/oversize/client edits/duplicate/cache/retry/stale และ ZIP T4/T5; ร่วม A เปิดจาก process ใหม่ด้วย run instructions จริง กู้เคสเดิมจาก SQLite พร้อมรูป/คำตอบ งานค้าง Retry ได้ เมื่อเปลี่ยน ZIP เคสและรูปเดิมใช้ไม่ได้ | ก่อนส่ง wiring: คำสั่ง/ผล boundary/state และ ZIP mutation provenance; ก่อนปิด: integrated commit/run commands/เครื่องเดโมและผล restart จริง ไม่มี fixture fallback |
| V04-C / C | รัน T1–T5 ผ่าน API จริงและอ่านความหมายเทียบ source/requirements เก็บ usage/time/attempts/incomplete และ cases ที่แก้แล้วตรวจใหม่ ทำ repeat เมื่อ gate ผ่านและมีเวลา | ก่อนส่ง evaluation: run IDs/code state/prompt version/actual model, expected/actual และ defects; ก่อนปิด: T1–T5 แต่ละกรณี passed หรือข้อค้างที่ทำให้ปิดไม่ได้ พร้อมจำนวน repeat จริงและข้อจำกัด |

## เกณฑ์รวมของ ticket

| Gate | สถานะปัจจุบัน | หลักฐานที่ต้องเติมเมื่อรัน |
| --- | --- | --- |
| Real ZIP → real model → report | not run | snapshot/case/code state, actions และผลจริง ไม่ใช้ sample |
| T1 sparse/skip/edit | not run | พบประเด็นเอง คู่แขนงให้คำถามและรายงานต่างกันตามคำตอบ เก็บ revision และข้อมูลที่ข้าม |
| T2 explicit/private note | not run | ไม่ถามซ้ำ/ไม่รับรอง SKU จาก menu/ไม่มี margin ใน draft |
| T3 changed SKU/order | not run | ชุดใหม่และ A/B ใหม่ ไม่มีคำ/ภาพปนจาก SKU ที่ไม่ได้เลือก |
| T4 removed report + untrusted instruction | not run | mutation provenance, actual model exposure, ไม่อ้างข้อความที่ลบ/ไม่ทำตามคำสั่ง |
| T5 missing image | not run | missing state, source scope/limitations และข้อมูลที่ยังใช้ได้ |
| UI/HTTP/security/state failures | not run | คำขอ/การกระทำ/code state/expected/actual รวม zero/partial/stale/retry |
| Standards + Spec code review | pending implementation | fixed base/target, briefs/results ครอบคลุม PR-01–04, reviewer route, findings/dispositions/final checks และ review-round ledger ต่อทุกใบที่ได้รับผล |
| SQLite restart + English rehearsal | not run | process ใหม่, คง SKU/รูป/คำตอบที่ commit, Retry งานค้างได้, เปิด ZIP ใหม่แล้วเคสเก่าใช้ไม่ได้ และซ้อมเดโมอังกฤษ |
| Usage/latency/repeats | not measured | actual model/tokens/time/attempts และจำนวนรอบที่ทำจริง |

บันทึก secret และบันทึกธุรกิจส่วนตัวให้น้อยที่สุด ไม่ใช้ชื่อโมเดลที่ตั้งไว้แทน actual model ที่รัน ไม่กล่าวว่า accuracy สูงกว่าหรือประหยัดกว่า ChatGPT หากยังไม่มี benchmark



## ผลจริงของ A

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว

## ผลจริงของ B

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว

## ผลจริงของ C

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว


## Local combined UI check — 2026-11-21

Local branch `codex/combined-ui` combines main `30c6d94`, evidence review `b3fe973`, and product-link import `1dc3041`. The reset handler keeps the case workflow and import ordering from the two branches. The running demo restored the existing snapshot with 12 options and displayed the new product-link input.

- `npm --prefix frontend run typecheck` and `npm --prefix frontend run build` passed.
- `npm --prefix frontend test -- --workers=3 --reporter=dot` passed 29 tests using installed Edge.
- `.venv/Scripts/python.exe -m pytest -q backend/tests --tb=short` passed 329 tests after explicitly reading the official-source test fixture as UTF-8 on Windows.
- Isolated offline server on port 8001 and Vite on 5175. Uploaded `samples/raw-save/raw-save-redacted.zip` by multipart POST through the Vite proxy, opened the saved listing in Edge, selected one SKU, confirmed options, and opened the purchase review. Playwright screenshots of the top, middle, and bottom of each screen at 1280 and 390 px showed no visible overlap. Document width stayed within the viewport. Browser file-picker upload was not verified end to end in this smoke check.
- Live translation stays enabled on port 8000. Live link retrieval and paid question/report generation were not exercised. Independent integrated release reviews and rehearsal remain pending.

## Proofly redesign · 2026-11-21

Working tree on `codex/link-import-purchase-review` over `8a33503`. Used the four primary screens from `frontend/design/proofly-design.zip`. The UI uses the current React workflow and report source IDs. Inter and Space Grotesk are served locally with their licenses. Chinese text uses the available system font.

- Ran `npm run typecheck` and `npm run build` from `frontend`. Both passed.
- Ran `node_modules/.bin/playwright.cmd test --workers=3 --reporter=line` from `frontend` with `PURCHASE_TEST_BROWSER` set to installed Chrome. On the final application code, 31 tests passed and one still expected combined text for tags now displayed separately. Updated that assertion to verify every expected tag in order and ran `node_modules/.bin/playwright.cmd test --last-failed --reporter=line`. It passed, covering all 32 tests across the two runs.
- Launched `.venv/Scripts/python.exe -m backend.tests.offline_server --port 8183` with its own temporary database. Set `PURCHASE_BACKEND_URL=http://127.0.0.1:8183` for the Vite process and ran `node_modules/.bin/vite.cmd --host 127.0.0.1 --port 5173 --strictPort` from `frontend`. An initial smoke attempt on port 5183 hit the backend origin check. Repeated the complete browser flow at the supported origin.
- Ran `node "$env:TEMP/proofly-visual.cjs"` from `frontend` with installed Chrome. Imported the real raw-save ZIP from the file input, selected two SKUs, entered context, answered one question, continued to round 2 and requested the review. Opened Option A’s source. Repeated at 1280 × 900 and 390 × 844. Inspected top, middle and bottom screenshots for all four screens at both widths. No visible overlap or horizontal page overflow. The mobile comparison uses stacked SKU cells. The selection shortcut skips the long seller-claims list.
- The offline server supplied translations, questions and reports for this check. Live model quality, live link retrieval, independent integrated Standards/Spec reviews and release rehearsal remain pending.

## PR #10 Standards and Spec review · 2026-11-21

Round 1 reviewed the staged integration of PR head `7c698814da8c9ff1ad8548ecb73194e614d3ede4` and Phase 3 `2652215bc120bc2ed38ab82a2bd64d751df2dec3` against main `324616b`. Application diff SHA-256 `13c5c301bf516172dfea94002e7d7d5f31b8aec1e28ce9be60525c73b85cf756`. A coordinated two independent Codex reviewers. The intake checks covered the PR #9 seam, and the buyer questions and evidence review checks exercised the local API and SQLite with the saved listing ZIP and scripted providers.

| Finding | Confirmed by | Disposition |
| --- | --- | --- |
| P2 · Report heading counts findings only | Standards and Spec | The API returned zero findings and one open order decision. Spec opened that API result in Chrome at 1280 and 390 px and saw “0 things to resolve” above the open decision. Open. |
| P2 · An ambiguous conflict follow-up disappears from provenance | Spec | After an answer left an order conflict open, the answer survived in the interview and the report omitted it from the decision's history and open-preference references. The incomplete report survived restart. Open. |
| P2 · A skipped repeat question can reopen a settled requirement | Standards | A scripted question repeated an existing screen-printing requirement in the same SKU scope with empty requirement IDs. The report showed both the active requirement and the same preference as open. This path was inherited from Phase 3. Open. |

Standards ran 139 offline backend tests across import, purchase decisions, requirements, and review recovery. Spec ran 188 offline tests across those areas and report validation, plus the production build. The controller ran 113 focused tests across purchase decisions, requirements, and report privacy. All suites passed. The reviewers reproduced the gaps through separate local API checks. Push is pending fixes and focused re-review. Live supplier/model checks, T1–T5, and final integrated acceptance remain pending.

### Round 2 · Follow-up fixes and verification

The final staged application diff has SHA-256 `65912bae9979ee60ae7af7ca04cde8579590400d8c7d200ae6ce7b413fbdb7d9` using `git diff --cached --binary HEAD -- backend frontend README.md docs`. The parents and main baseline are the same as round 1.

- **Heading.** Proofly now uses **Before you order**. With zero findings and one open order decision, the report linked directly to **Open preferences** and showed no supplier draft.
- **Provenance.** The exact ambiguous answer and issued question stayed on the unresolved order decision through SQLite recovery. Scope tests checked that the answer remained out of supplier copy and that a later explicit choice could resolve its affected SKUs. A case with an older question that referenced a shared conflict retained its original answers and revision and replayed that question after splitting the decision history by scope.
- **Skipped repeat.** With decoration settled for A, a skipped decoration question issued for A and B left only B open on that facet. A's independent surface-finish question stayed open. This also passed for an older answer with an inferred decoration target and a null question facet, both when the decision stayed active and when the buyer explicitly waived it. A stale SQLite report required explicit regeneration, retaining its case, revision, and answers. Recovery made zero provider requests in the relevant test.

The full offline backend suite passed **436 tests**. Chrome Playwright passed **39 tests**, including all three provider formats and ZIP fallback. Typecheck, the production build, and both staged and unstaged whitespace checks passed.

```sh
PYTHONDONTWRITEBYTECODE=1 OPENAI_LIVE_TRANSLATION=0 OPENAI_LIVE_QUESTIONS=0 OPENAI_LIVE_REVIEW=0 "$PROOFLY_CHECKOUT_PYTHON" -m pytest -q -p no:cacheprovider backend/tests
PURCHASE_TEST_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm --prefix frontend test -- --workers=2
npm --prefix frontend run typecheck
npm --prefix frontend run build
git diff --cached --check
git diff --check
```

`PROOFLY_CHECKOUT_PYTHON` was the source checkout's `.venv/bin/python`. The temporary walkthrough ran a local Uvicorn harness with scripted providers on port 18111, `PURCHASE_BACKEND_URL=http://127.0.0.1:18111 frontend/node_modules/.bin/vite frontend --host 127.0.0.1 --port 5173 --strictPort`, and `node .followup-check/walk.cjs`. Chrome uploaded the real `samples/raw-save/raw-save-redacted.zip`, selected three SKUs, answered two rounds, opened the decision history, and reloaded the report at 1280 × 840 and 390 × 840. Top, middle, and bottom screenshots were inspected. Playwright found zero horizontal overflow and no findings/supplier-panel overlap while scrolling. The 87 tracked source, contract, and sample files matched the verified copy byte for byte.

Standards and Spec both cleared the focused fixes on the final staged application hash. Standards initially ran 206 targeted offline tests and reproduced a duplicate open preference when an active answer had a validated decoration target and a null facet. Spec initially ran 132 targeted backend tests and four Chrome tests, and recovered an actual `2652215` SQLite case. Spec then reproduced the same facet gap after an explicit waiver. These two scope-local corrections were checked within round 2.

On the final candidate Standards passed 56 focused tests and independently exercised a three-SKU replay where A stayed waived, B stayed active, and only C's decoration decision remained open. A and B's independent texture decisions stayed open. Spec passed five focused tests and replayed its original waiver case, where only B stayed open and the supplier draft remained empty. Both independently verified SQLite recovery with provider calls forbidden. All three round 1 findings are closed for this focused review. Live providers, integrated T1–T5, and release acceptance remain pending.

## DiliDoo! frontend handoff · 2026-11-21

Verified the DiliDoo! changes on `feat/ui-dilidoo-brand` over `7c69881`. The command `grep -ri proofly frontend/src frontend/public frontend/index.html frontend/tests` produced no output. `npm --prefix frontend run typecheck && npm --prefix frontend test` passed with installed Chrome (32 tests), including collapsed evidence, navigation from finding links, original SKU evidence and editable supplier drafts.

Ran `.venv/Scripts/python.exe -m backend.tests.offline_server --port 8191` with a temporary database and Vite on port 5173 pointed at that server. Ran `node "$env:TEMP/dilidoo-visual.cjs"` from frontend. Imported `samples/raw-save/raw-save-redacted.zip`, selected two SKUs, answered two rounds and opened the review at 1280 × 900 and 390 × 844. Checked top, middle and bottom screenshots of upload, selection, questions, collapsed findings and expanded evidence. Both layouts fit the viewport, the sticky panels stayed clear of adjacent content and the expanded source showed the selected SKU. Confirmed the desktop slogan breaks between the two sentences. These checks used offline AI results. Live model quality and integrated release reviews remain pending.

## Hosted demo rehearsal

Working tree based on `840ee64`, including the natural-answer changes. Original archive used locally at `~/Downloads/Website Test.zip`; provider outputs were scripted. Railway deployment and live model capacity are pending separate approval.

- `PURCHASE_TEST_ZIP="$HOME/Downloads/Website Test.zip" OPENAI_LIVE_TRANSLATION=0 OPENAI_LIVE_QUESTIONS=0 OPENAI_LIVE_REVIEW=0 python -m pytest -q -s -p no:cacheprovider backend/tests/test_hosted_http.py` ran against actual loopback HTTP and uvicorn processes. The 10 clients had independent cookies, case IDs, selected SKU orders, image bytes and buyer deadlines. Copied image URLs and case commands could not cross browser workspaces. All confirmed cases preserved the same report, evidence and supplier draft after restart.
- Ten simultaneous submissions with three report workers produced three active provider calls and seven waiting jobs. With ten workers, all ten provider calls were active together. In both runs each case generated once. One local run with a held provider and a 60 ms scripted delay measured median/slowest submit-to-ready of 0.224/0.348 seconds (three workers) and 0.148/0.182 seconds (ten workers). These measurements exercise coordination only.
- A SIGKILL during generation left the interrupted case recoverable for explicit retry while a waiting report resumed once. The completed state survived a fresh process. The queue-level regression covers FIFO across reversed workspace discovery, superseded jobs and duplicate commands.
- `python -m pytest -q backend/tests/test_hosted_access.py` exercises the code gate, cross-origin POST rejection, cookie flags and private response caching, expired cases, and fail-closed Railway volume selection. A real SDK using a local HTTP mock verifies that retried provider requests consume a persistent allowance, including after new cookies and a fresh app lifespan.
- Independent review found and rechecked three defects. Distinct operation IDs from tabs confirming the same revision now follow one canonical report. A hosted retry carries a one-use token from the displayed failure so replaying an old retry cannot spend again after a newer failure. A writable startup scan allows SQLite to roll back a hot journal after a crash. One irrecoverable case returns 503 for its own browser while the other browsers remain available. Targeted backend and Chrome checks passed after the fixes.
- Production frontend typecheck/build and all 44 Chrome tests passed. A separate desktop (1280×850) and mobile (390×850) Chrome walkthrough imported the original ZIP in two isolated contexts, selected three/two SKUs and submitted two rounds each. The mobile job waited through reload, resumed after a server restart, and the interrupted desktop job required an explicit retry. Both reports reopened with their own findings, working evidence links and supplier drafts. Inspected top/middle/bottom screenshots and checked for horizontal overflow and sticky panel overlap.
- Built the Dockerfile for linux/amd64 and started its normal entry point with a custom PORT, temporary /data, isolated Docker network and no API key. Inside the image, verified the frontend, /healthz, unauthenticated 401, unlock, empty private workspace, case database on /data, cross-origin 403 and absence of local credentials, tests, sample ZIP and developer databases.
- Final integrated run with the original ZIP, all live flags disabled and loopback available: `python -m pytest -q -p no:cacheprovider backend/tests --tb=short` passed 524 tests in 33.72 seconds. All 44 Chrome tests passed, including the canonical-operation and failure-token paths. Independent recheck cleared the three review findings.
