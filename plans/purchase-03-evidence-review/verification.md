# PR-03 verification

Phase 3 handoff for issue #3. The branch `phase-3/evidence-review` starts at `6fd499e`, the merge of PR #6 at `799e028`. Integrated acceptance for all four phases is tracked separately.
Authority: [plan](plan.md), [report contract](../../docs/contracts/report-contract.md)

## ตรวจส่วนของตนก่อนเปิด PR

เกณฑ์ตรวจแยกตามผู้รับผิดชอบ ดูผลรันจริงด้านล่างและ [รูปแบบส่งงาน](../../docs/team-plan.md#วิธีส่ง-pr-ของแต่ละคน) output ที่เขียนขึ้นใช้ทดสอบ rendering/validation เท่านั้น ไม่ยืนยันการค้นพบของโมเดล

| ID / เจ้าของ | วิธีตรวจและผลที่ต้องได้ | หลักฐานก่อนส่ง |
| --- | --- | --- |
| V03-A / A | รัน frontend type check/build และ checks ของ report ลอง shared/per-option findings, coverage ครบ 3 กลุ่มและ zero findings; เปิด original/translation/ภาพตรง source; แก้/copy draft ไม่เปลี่ยน findings; draft ว่างซ่อน copy; failed/retry/edit/stale ไม่แสดงผลผิด case/revision | คำสั่ง/ผล, actions/expected/actual และ report/source IDs ระบุ harness/HTTP จริง รวม copy success/failure ตามที่ตรวจได้ |
| V03-B / B | รัน report HTTP/state checks ด้วย adapter ที่หน่วง/failed/exception ได้: จับ input/image bytes ก่อน await; เปลี่ยน snapshot/selection/context/answers แล้วผลเก่า commit ไม่ได้; running=409, ready ใช้ cache, failed retry เริ่ม run ใหม่และปล่อย reservation; images คนละ snapshot เปิดไม่ได้ | คำสั่ง/ผล, binding/case/revision/generation, call counts และ error mapping ระบุ adapter ไม่อ้างว่าเป็น API จริง |
| V03-C / C | รัน validator/draft checks กับ bad quote/source/option/requirement refs, missing/unreadable/unsent-image citations, unknown scope, comparison/coverage และ null requirement; ตรวจ draft ตาม order/A–B/private margin/empty; จำกัดส่งภาพจริง ≤3 และไม่ใส่ expected answers จากนั้นอ่านรายงาน API เทียบหลักฐานใน T1–T5 | คำสั่ง/ผล rejected mutations และ draft checks, prompt version/actual source-image inventory ผล API จริงอ้าง PR-04 ร่วมได้; ระบุ semantic checks ที่ยัง pending ไม่ใช้ schema pass แทน |

ก่อนต่อ report runner จริง ให้ B/C ใช้ ReviewInput จาก PR-02 ที่มีหลายรอบและไม่มีคำตอบจากแขนงเก่า และตรวจว่า bytes รูปมาจาก snapshot เดียวกับ selected SKU ตาม source IDs ลองกรณีรูปหายและ snapshot เปลี่ยนระหว่างรอ แล้วตรวจว่าไม่ยืมรูปหรือแสดงผลเก่า

ทดสอบคู่รายงานใน T1 ด้วย ZIP/SKU เดิม แต่สลับคำตอบว่าซื้อเฉพาะสำเร็จรูปหรือยอมสั่งทำ ลำดับ/ทางเลือกและคำถามผู้ขายต้องสะท้อนเงื่อนไข หลักฐานและสิ่งที่ยังต้องยืนยันของ SKU เดิมคงเดิม

## เกณฑ์รวมของ ticket

| กลุ่ม | สิ่งที่ต้องตรวจจริง | หลักฐานที่ต้องเก็บ |
| --- | --- | --- |
| References | quotes/requirement refs/option scopes/coverage/comparison rows | ผล schema และ cross-reference checks รวมตัวอย่างผิดที่ถูกปฏิเสธ |
| ข้อมูลขาด | source removed/missing/unreadable/not-sent เทียบกับ seller states no report | รายงานและชุดที่ส่งจริง ไม่อ้างเพียง availability |
| ความต้องการ | skip/เปลี่ยนใจ/บริบทขัดคำตอบ | requirement/open preference ที่ตาม input จริง ไม่มี requirement จาก null |
| Draft/UI | ลำดับคำถาม/ชื่อ SKU/ไม่มี margin, copy แก้ได้และไม่ย้อนแก้ findings | ข้อความ draft กับ UI จริงและผลเมื่อไม่มีคำถาม |
| Failure/state | invalid/refusal/incomplete/timeout/retry/stale | failed result, retained answers, calls/IDs ไม่ใช้ sample fallback |

กฎ quote/ID ถูกไม่ได้พิสูจน์ความหมาย ต้องอ่าน output เทียบหลักฐานด้วยใน PR-04 ยังไม่อ้างคุณภาพโมเดลจาก expected report หรือ fixture เติม actual commands/code state/results เมื่อพัฒนา


## Checks run

On implementation commit `d75450e`, the full offline backend suite passed 185 tests, including 36 report generator tests. All 27 Chrome Playwright tests passed, including five report UI checks. The frontend typecheck, production build and `git diff --check` passed. Commands from the repository root:

```sh
OPENAI_LIVE_TRANSLATION=0 OPENAI_LIVE_QUESTIONS=0 OPENAI_LIVE_REVIEW=0 .venv/bin/python -m pytest -q backend/tests
npm --prefix frontend run typecheck
npm --prefix frontend run build
PURCHASE_TEST_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm --prefix frontend test -- --workers=2
```

## Report UI and original ZIP (A)

The production frontend, Uvicorn, and SQLite ran locally with scripted providers. In Chrome, the buyer uploaded the original `Website Test.zip`, selected three options out of catalog order and completed two interview rounds. After restarting the backend, **View purchase review** showed one shared finding and one finding restricted to the first selected option. Its evidence opened the exact Chinese name, labeled English translation, and photo at the matching snapshot/source URL. All 12 SKU image hashes matched the independent inventory.

A second backend restart recovered the identical report by GET. Editing the supplier message and copying it put the edited text on the clipboard. The stored report still contained the original findings and draft. Editing round one from custom orders to ready-made only produced revision 3, kept the exact deadline including its surrounding spaces, and removed the dependent sample answer. The new revision displayed its own draft. Desktop (1280 px) and mobile (390 px) screenshots covered the top, middle with evidence open, and bottom. Both layouts had zero horizontal overflow.

The browser regressions also cover zero findings, all three coverage groups, an empty supplier draft, clipboard permission denial, a missing or stale report, an edited draft restored after canceling an answer edit, retrying after failure, and curated guidance labeled separately from seller evidence.

## Report state and retrieval (B)

The report workflow tests delay generation while the buyer replaces the ZIP, changes selection or context, or edits an answer. They verify that the superseded result cannot publish, the report for the new revision takes precedence, and the generator receives image bytes bound to the same snapshot as its input. Additional checks cover missing images, running and cached operations, malformed result bindings, cancellation, and retry after reopening SQLite.

An independent Uvicorn process test killed the backend during generation after the answers had committed. On restart, GET recovered the failed operation, saved answers, and original SKU image. Explicit retry generated one report. Repeating the request served the saved report. A separate compatibility check created a review handoff using the actual Phase 2 code at `6fd499e`, then opened that database under Phase 3. It generated a report without changing the case ID, revision, answers or selection.

## Report generation (C)

The production adapter used strict structured responses and local image bytes. Offline SDK tests exercised a real parsed ZIP and malformed responses. They checked exact buyer quotes and source quotes, wrong-SKU references, out-of-scope or missing image citations, metadata-only evidence, skipped answers, comparison and coverage completeness, and one corrective retry within the shared deadline. Draft tests covered original SKU labels in buyer selection order, supplier questions in finding order, private business notes and an empty draft.

## Live comparison

Two reports used the public redacted raw-save ZIP, the same three ordered SKU thumbnails, 46 text sources, nine metadata-only image references, and synthetic answers. The user-supplied original ZIP stayed local. Translation and interview generation were offline. The buyer required 720 ml and screen printing. Only the answer about accepting a custom order changed. The transport checked the endpoint, model and SHA-256 of each outgoing image. The model was `gpt-6.1-terra`, medium reasoning.

| Prompt and answer | Input tokens | Output tokens (reasoning) | Provider time |
| --- | ---: | ---: | ---: |
| v1, ready-made only | 6,427 | 4,608 (556) | 53.99 s |
| v1, custom orders acceptable | 6,425 | 4,800 (286) | 54.89 s |
| v2, ready-made only | 6,605 | 5,774 (1,456) | 65.53 s |
| v2, custom orders acceptable | 6,605 | 5,873 (1,208) | 66.17 s |

Both v1 reports validated on their first request and kept the synthetic margin out of the supplier message. The ready-made report requested confirmation of the existing stock's decoration method. The custom-order report also asked for custom production details and samples. Both distinguished general customization from SKU-specific evidence and kept the skipped deadline open. Neither mentioned the 750 ml labels visible on the three selected images alongside the 720 ml selector. The custom-order report did request a measurement. Prompt v2 asks the model to read visible labels and units for each selected image, compare them with selected values and buyer requirements, and request a measurement where they disagree.

Both v2 requests at `b7278dd` returned a validated report on their first attempt. Each led with the selector/image discrepancy and cited the three correct SKU images labeled 750 ml. Both requested SKU-identified capacity measurements and kept delivered capacity unconfirmed. One asked for stock evidence for the ready-made designs. The other asked whether a custom screen-printed version could be produced and requested a sample. Both left the skipped deadline open and kept the internal margin out of every supplier question. The saved ready-made report also reopened in Chrome at both widths with all five findings and the exact server draft, using zero writes. Four provider requests total were authorized and sent.

The final frontend change on `07559e6` labels the collapsed SKU evidence controls. Chrome showed **SKU image · Option A**, **Option B** and **Option C** on the live report’s capacity finding. The original ZIP walkthrough, saved live-report walkthrough, 27 Playwright tests, typecheck and production build all passed after that change. The full backend suite passed again after the v2 prompt change.

## Review รวมก่อนปิด ticket

The integrated Standards and Spec reviews and T1–T5 acceptance remain pending. Issue #3 stays open for that review.
