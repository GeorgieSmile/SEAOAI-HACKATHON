# PR-02 verification

Status: Phase 2 handoff for issue #2. The internal `ReviewInput` is available to the next phase after the buyer ends the interview. The integrated report and T1–T5 review belong to later phases.

- Branch `phase-2/buyer-questions` from `dcabdf1`, the merge of PR #5 at `547341a`.
- [Local run instructions](../../README.md) include an offline mode and a persistent test database for restarting the app.
- Tested implementation `16cced4`, including the frontend recovery follow-up and mobile history fix. With live question and translation calls disabled, `.venv/bin/python -m pytest -q backend/tests` passed all 99 tests. The frontend typecheck, production build and all 15 Playwright tests passed using the installed Chrome. `git diff --check` passed.
Authority: [plan](plan.md), [question contract](../../docs/contracts/question-contract.md)

## PR #6 review regressions

On `50bc102`, two offline checks reproduced the review findings with the original user-supplied ZIP:

- The question route accepted a mock response that cited A’s name (暮色316-贴画粉蓝) while telling the buyer that B’s name (暮色316-字母粉蓝) described applied-picture artwork. The false attribution survived reopening SQLite.
- In two Chrome tabs, A’s Continue response was delayed after the server committed it. B started a different case on the same listing and saved a deadline. A then confirmed different SKUs. Its request used B’s case ID and revision and replaced the saved case.

Verification on implementation commit `003fe1e` included 117 backend tests, 19 Playwright tests in installed Chrome, the frontend typecheck and production build, and `git diff --check`. All passed. The fix checks used offline model responses, including for the original ZIP walkthrough.

- The same mock provider response returned `invalid_questions`. Reopening SQLite showed a failed operation and zero issued rounds. Offline replay of the previously authorized four-question comparison still passed with its three correctly attributed option names.
- In the same two-tab Chrome run, the stale confirmation and subsequent reload sent zero POSTs. The other tab’s deadline reappeared at revision 1. A deliberate selection change after the reload sent one POST bound to the newly displayed case and revision.
- The original `Website Test.zip` also passed the edit/restart walkthrough at 1280 and 390 px. Three selected SKUs kept their order across two backend process restarts. Changing round one to “Ready-made only” preserved the exact purchase deadline, including surrounding spaces, and removed the dependent sample-approval answer from both the browser and `ReviewInput`. All 12 recovered SKU names and image hashes matched the independent inventory. A request using the old revision received HTTP 409.

A fresh Astra 6.2 high review of `348eb5f` confirmed both original P1 fixes. It found three valid questions that the new guard rejected: a rationale beginning “For Option A, the SKU name …”, a buyer choice beginning “For Option A, I need …”, and a question asking whether the buyer requires proof of testing for Option B. Each false rejection reproduced through the normal question route with the original ZIP and an offline provider.

On `ef0096f`, all three valid cases issued a round and reopened correctly from SQLite. The original A-only citation falsely naming B still failed with zero issued rounds. The stored response from the previously approved live four-question plan also passed offline. The full backend suite passed all 126 tests. The edit/restart browser walkthrough passed again on this commit. All 19 Playwright regressions, typechecking, and the production build had passed on the same frontend code. No additional paid requests were made.

On `0891ba6` the reviewer reproduced another stale-tab overwrite through **Resume questions** after a delayed Continue, and a B-only choice beginning “For Option B, a printed proof …” was rejected. On `a1cf550` the same two-tab walkthrough with the original ZIP preserved the second tab’s case and deadline. Resume and the subsequent reload sent zero writes. The B-only choice issued a round and survived reopening SQLite. Regressions cover Confirm and Resume for both a replaced case and a newer revision, same-tab recovery, and the distinction between buyer prose and actual option lists.

On that implementation all 135 backend tests, all 22 Chrome Playwright tests, typechecking and the production build passed. The original ZIP edit/restart walkthrough passed again at desktop and mobile widths, including the exact saved deadline, discarded dependent answer and all 12 SKU photo hashes. The original wrong-SKU case still failed before publication, and the recorded four-question comparison still passed. All provider responses used for these checks were offline.

## ตรวจส่วนของตนก่อนเปิด PR

เกณฑ์ V02-A/B/C ใช้ตรวจ flow, binding และ question generation ผลจากการรันจริงอยู่ใต้ตาราง

| ID / เจ้าของ | วิธีตรวจและผลที่ต้องได้ | หลักฐานก่อนส่ง |
| --- | --- | --- |
| V02-A / A | รัน frontend type check/build และ harness สำหรับ zero/partial/skip, แสดงทุกข้อพร้อมกันและไม่ preselect, Continue ส่งทั้งรอบครั้งเดียว, View purchase review ใช้ร่างที่กรอก, edit ตัดรอบหลัง, retry/restore/เปลี่ยน SKU ไม่ยอมให้ผลเก่าทับ | คำสั่ง ผลบน 1280px/390px และ request ที่ไม่มี question_text แยก harness/HTTP จริง |
| V02-B / B | ใช้ snapshot/รอบที่ server ออก normalize null/reconstruct question_text ปฏิเสธ missing/duplicate/unknown IDs, client fields, stale revision หรือ reuse ID ต่าง payload ก่อน AI ตรวจ 409/ready/retry, restart SQLite และรูปเดิม, edit ตัดรอบหลัง/ไม่เพิ่ม revision เมื่อข้อมูลเดิม | คำสั่ง ผลเทียบ ReviewInput และ call counts จาก test adapter พร้อมผล restart โดยไม่ log ข้อมูลผู้ซื้อ |
| V02-C / C | schema ต้องปฏิเสธ >5 ข้อ คำถามซ้ำ source ที่ไม่ได้ส่ง scope ผิด SKU และอ้างภาพ ไม่แปลงเป็นผลจบ T1/T2 สดตรวจว่ารอบต่อมารับคำตอบเดิม ถามเฉพาะข้อที่เป็นอิสระกัน และ T2 ไม่ถามการใช้งานซ้ำเมื่อทราบแล้ว | deterministic checks, prompt/version/source IDs และผลจริงจาก T1/T2 ใน PR-04 หากยังไม่รันให้ pending |

ก่อนส่งต่อ PR-03 ส่งคำตอบชุดเดิมซ้ำแล้ว revision ต้องคงเดิม เมื่อแก้คำตอบรอบต้นให้ได้ revision ใหม่ตาม contract ตรวจทั้ง zero/partial/skip, กดรายงานก่อนจบ, เพดาน 3×5 และ recovery หลัง restart

## เกณฑ์รวมของ ticket

| กลุ่ม | สิ่งที่ต้องตรวจจริง | หลักฐานที่ต้องเก็บ |
| --- | --- | --- |
| 0 คำถาม | ไปขอรายงานโดยไม่ผ่านหน้าถาม ReviewInput ใช้ answers: [] | HTTP/request flow และ ReviewInput |
| ตอบบางข้อ/ข้าม | คงคำตอบที่มี, null/whitespace, issued prompt ถูกต้อง | input/plan/submission/ReviewInput ที่รหัสเชื่อมกัน |
| Input ผิด | question_id ซ้ำ/ไม่เคยถาม, extra fields, reuse operation กับ payload ใหม่, stale revision | ปฏิเสธก่อน paid call |
| ผลคำถามผิด | มากกว่า 5/ผิดข้อความที่ส่ง หรือผิด scope/อ้างภาพ และไม่มีรอบที่สี่ | invalid_questions และไม่ truncate/fallback |
| แก้และ retry | เปลี่ยนคำตอบ, ZIP/options/order/context, failed retry, running/ready ซ้ำ | case/revision/operation และจำนวน calls |
| กู้เคส | restart หลัง commit รอบหนึ่งและระหว่าง AI ค้าง เปิดเคส/รูป/คำตอบเดิมและ Retry หรือ Continue | process ใหม่, DB ไฟล์เดิม, IDs และ HTTP ที่ไม่เรียก AI ตอนโหลด |

Fixture ใช้ตรวจการเชื่อมข้อมูล ไม่ยืนยันว่า AI ถามเหมาะสม ความเหมาะสมและการค้นพบเองต้องอ่านผลจริงใน PR-04 เติม actual commands/code state/results เมื่อพัฒนา ไม่ติ๊กผ่านล่วงหน้า


## ผลจริงของ A

Chrome uploaded the unmodified, user-supplied ZIP through Vite and the production HTTP routes. The test server parsed all 12 SKUs and injected offline translation and scripted questions. Three SKUs selected in a different order retained that order and matched the independent SHA-256 image inventory after a backend restart.

The browser chose a custom order and left the deadline empty. Continue committed both answers at revision 1. A new backend process opened the same SQLite file. Reloading the browser used only GETs and recovered the dependent sample-approval question. An early review committed the sample answer at revision 2. Editing the first answer to ready-made only and requesting review removed the dependent round at revision 3 and preserved the skipped deadline. Desktop (1280 px) and mobile (390 px) screenshots covered top, middle, and bottom. The checks found no horizontal overflow or overlapping controls.

The browser regression suite also checked zero questions, no preselected answers, the round limit, unchanged edits with a later draft, retry after an ambiguous network result, delayed responses after selection changes, and cancellation or reconfirmation of a stale reset.

A second browser run opened the same case in two tabs. One tab began a reset at revision 1. The other tab saved review inputs at revision 2. The stale confirmation sent no reset request. After a read-only reload and a new confirmation, the reset cleared the case and snapshot, and the old image URL returned 404.

## ผลจริงของ B

In a separate test with the redacted raw ZIP, a backend process was killed while generating round two, after committing the two answers. A new process opened the same SQLite database. GET returned the original SKU image hash, committed answers, case ID and revision, and marked the interrupted operation failed. Retrying its original operation ID issued one second round and preserved revision 1.

The 15 case workflow tests cover all 15 submitted answers at the 3×5 limit, zero/partial/skipped answers, server-issued wording and order, exact nonblank text, client fields, stale revisions and operation IDs, duplicate operations, superseded results, cancellation, and manual retry after configuration repair. C independently checked B's persistence and publication logic.

## ผลจริงของ C

Three authorized requests sent the redacted raw ZIP's selected text through the production question adapter and the normal start/continue routes. Translation was offline. Each request contained the same 3 selected SKUs and 46 available text sources (including public guidance). There was no application cache or retry. All three responses passed validation.

| Prompt | Step | Input tokens | Output tokens (reasoning) | Provider time |
| --- | --- | ---: | ---: | ---: |
| buyer-questions-v1 | Start, sparse resale context | 3,609 | 1,062 (385) | 16.12 s |
| buyer-questions-v1 | Continue, four synthetic answers and a skip | 3,870 | 1,159 (433) | 14.62 s |
| buyer-questions-v2 | Fresh first round, same sparse context | 3,720 | 1,233 (516) | 16.19 s |

All used `gpt-6.1-terra` at medium reasoning. With v1, the first round asked five questions. After the buyer specified cold use and required test reports, the second round asked about independent testing, report coverage, drink type, cold retention, and leak resistance. It did not re-ask the skipped rights question. The cited report, material, and performance attributes were checked against the parsed originals. Neither round raised the differences in the option names. This matters for T1, where the buyer has supplied little context. Prompt v1 SHA-256 was `ccef1a90147a4decd75b697af525bc502aecee16c2aa7a8ac808cbdd1545dabc`.

The active `buyer-questions-v2` prompt asks the model to compare the selected names and their scoped text, and prioritize material buying decisions before general logistics. With the same options and buyer context, the live first-round recheck returned four questions. It led with the buyer's preference for picture-style designs, lettering, or a mix. The explanation attributed 贴画 to A/B and 字母 to C using the three correct option-name sources. Its separate logo question attributed customization to a general seller claim and asked whether to verify availability for the selected options. This was one first-round recheck. Follow-up quality under v2 and the integrated T1–T5 cases remain to be assessed.

## Review รวมก่อนปิด ticket

Integrated Standards/Spec reviews and the full T1–T5 runs remain pending. The Phase 2 review handoff contains the input for future report generation. This issue stays open for integrated acceptance.

## Proofly redesign · 2026-11-21

The real ZIP flow opened round 1 with both selected SKUs. Choosing “Custom orders are acceptable” and continuing opened round 2. From the saved report, editing round 1 and cancelling restored the report and updated the progress indicator and page title. The offline server supplied the questions. See [integrated UI verification](../purchase-04-integrate-and-verify/verification.md#proofly-redesign--2026-11-21) for code state, commands and screenshot coverage.

## Issue #12 buying brief

**Code state.** Local working tree on `phase-3/evidence-review` at base `0f15d51`. The walkthrough used the production React/FastAPI path with scripted translation, interview, brief interpretation and report providers. The original `Website Test.zip` and disposable SQLite databases stayed local.

**Checks from the repository root**

```sh
PURCHASE_TEST_ZIP="$HOME/Downloads/Website Test.zip" OPENAI_LIVE_TRANSLATION=0 OPENAI_LIVE_QUESTIONS=0 OPENAI_LIVE_REVIEW=0 .venv/bin/python -m pytest -q backend/tests --tb=short
PURCHASE_TEST_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm --prefix frontend test -- --workers=2
npm --prefix frontend run typecheck
npm --prefix frontend run build
git diff --check
```

- All 468 backend tests passed. The HTTP suite exercised the case/revision confirmation gate, the zero-question and early-exit paths, a concurrent confirmation with a different operation ID, late report results, and recovery of older unfinished and interrupted cases. It also checked the original quotes, the wrong-SKU default action and exclamation-mark regressions from PR #7, the independent engraving and texture decisions, and the skipped sample question. The scripted SDK adapter rejected a proposal for the wrong SKU, retried with the correct one, and made no interpretation call when saving or confirming. Undo preview listed the saved sample answer before discarding the dependent rounds.
- The Chrome suite passed all 38 tests. Frontend typechecking, the production build and the whitespace check passed.
- For the real browser walkthrough, ran `.venv/bin/python -m backend.tests.offline_server --database /tmp/purchase-12-local-final.sqlite3` and `npm --prefix frontend run dev`. Uploaded the original ZIP in Chrome and selected black/white, blue/white, and black/pink. Only B became conditional. Moved the shared date to 15 December, saw B's condition change, and undid the date change. A stale tab got HTTP 409, and a double-click sent one confirmation from the current tab. The clipboard matched the draft. Its custom question was scoped to B and its ready-made question to A and C, both with 1 December.
- Inspected the brief and report at the top, middle, and bottom at 1280 × 850 and 390 × 850. The layout was readable and the page did not overflow horizontally. After restarting the backend on the same database, fresh Chrome sessions at both widths reopened the confirmed report with GET requests only. Images were served from the imported snapshot.

**Pending.** The fresh independent Standards and Spec reviews still need to cover the final integrated changes, especially the confirmation and retry entry points, SKU ownership of order conditions, and pre-#12 SQLite recovery. Scripted checks give no evidence about live model interpretation quality.

## Issue #12 on Proofly main

**Code state.** The `proofly-buying-brief` worktree is based on `edbfb83d23df6b069845067bd1584d5701209051` (PR #10). This integration retains Proofly's three-step layout, evidence report and scoped decision replay. Step 2 now includes the compact brief during an interview and the full review before report confirmation.

- The combined backend suite passed **478 tests** using the original `Website Test.zip` and scripted providers. The combined Playwright suite passed **41 Chrome tests**. Typecheck and production build passed.
- The real React and FastAPI walkthrough uploaded the original ZIP, selected black/white, blue/white, and black/pink in order, and corrected only the blue option. Moving the date to 15 December updated its condition and displayed the shared deadline once. Undo restored 1 December. An outdated tab got HTTP 409 and a double-click sent one report request. The clipboard matched the draft, which scoped conditional custom ordering to B and ready-made ordering to A and C. Returning to the brief from the report moved the active step without triggering a report.
- A separate walkthrough answered the order and deadline questions and showed the compact brief beside the next round. Ending early with the sample question skipped opened the full brief with only the sample decision open and no report.
- Inspected top, middle, and bottom screenshots for the interview, full brief and report at 1280 × 850 and 390 × 850. Checked document width and desktop sticky supplier-panel separation while scrolling.
- Restarted the offline backend on the same database and reopened the confirmed report at both widths in fresh Chrome sessions with GET requests only. Also tested two disposable databases written by the unmodified `edbfb83` backend using the original ZIP. The unfinished case kept its decision history and generated once after confirmation. The previously reviewed case reopened the existing report. Both restored without calling a provider.

```sh
PURCHASE_TEST_ZIP="$HOME/Downloads/Website Test.zip" OPENAI_LIVE_TRANSLATION=0 OPENAI_LIVE_QUESTIONS=0 OPENAI_LIVE_REVIEW=0 "$PROOFLY_PYTHON" -m pytest -q backend/tests --tb=short
PURCHASE_TEST_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm --prefix frontend test -- --workers=2
npm --prefix frontend run typecheck
npm --prefix frontend run build
"$PROOFLY_PYTHON" -m backend.tests.offline_server --port 8187 --database /tmp/proofly-12-browser.sqlite3
PURCHASE_BACKEND_URL=http://127.0.0.1:8187 npm --prefix frontend run dev
node /tmp/proofly-12-browser.cjs
node /tmp/proofly-12-browser.cjs restart
node /tmp/proofly-12-browser.cjs compact
git diff --check
```

`PROOFLY_PYTHON` was the source checkout's `.venv/bin/python`. Screenshots and local database copies stayed outside the checkout. Fresh Standards and Spec reviews of this combined change and live provider quality checks remain pending.

## Natural interview answers

Tested the local changes based on `840ee64` using the original Website Test.zip, a disposable SQLite database, the React app, and FastAPI with scripted providers. In Chrome at 1280 × 850 and 390 × 850, selected black/white (A), blue/white (B), and black/pink (C). The starting brief required ready-made stock for all three by 1 December.

- Typed “Maybe for one of them” and “15 December is fine” in the same round. The order question asked which option could be custom-made. Both draft answers stayed in place. Editing the first answer to “Custom is fine for the blue cup” saved B’s exception and the shared deadline. A and C kept their stock requirement. Checked the per-SKU changes, reopened the brief, confirmed it, and verified the supplier draft had separate B and A/C questions. Inspected top, middle, and bottom screenshots of the clarification and brief at both widths and checked for horizontal overflow.
- Through the actual SDK with a local mock transport, the round with “A bespoke production run works for us for the blue cup” used one interpretation request. The locally recognized deadline was excluded from that request. Saving after preview and a backend restart reused the result. The report kept the order requirement scoped to B and reopened after a second restart.
- Ran the following from the repo root using the shared project virtualenv. All 513 backend and 43 Chrome tests passed. Typecheck, production build, and whitespace checks passed.

```sh
PURCHASE_TEST_ZIP="$HOME/Downloads/Website Test.zip" OPENAI_LIVE_TRANSLATION=0 OPENAI_LIVE_QUESTIONS=0 OPENAI_LIVE_REVIEW=0 "$PYTHON" -m pytest -q -p no:cacheprovider backend/tests --tb=short
PURCHASE_TEST_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm --prefix frontend test -- --workers=2 --reporter=dot
npm --prefix frontend run typecheck
npm --prefix frontend run build
git diff --check
```

Restarted the live local backend and verified that its existing reviewed case reopened at revision 3 through the frontend proxy. Verification used zero paid requests. Live interpretation quality was not measured.
