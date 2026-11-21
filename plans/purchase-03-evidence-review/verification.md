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

## PR #7 review regressions

On `6f38c3c`, the original user-supplied ZIP reproduced the three review findings through the production report endpoint with an offline SDK transport. With A selected as the lettering variant and B as the applied-picture variant, a joint finding attributed B’s name to A. The provider could also copy an email address out of a private buyer answer, or leave “this option” ambiguous in a draft listing both options. All three reports were accepted and saved.

With the fixes, the wrong-source attribution, original terms swapped between two cited SKU names, and copied private address returned `invalid_review` after the offline correction attempt. The correctly attributed comparison still issued a report. Each numbered supplier question now identifies its applicable options in buyer selection order.

The same checks reopened five databases written by the actual `6f38c3c` implementation. Startup withheld the three invalid reports, preserved the submitted answers and allowed explicit retry. It rebuilt the two valid drafts with option labels. Recovery issued zero provider requests.

The Chrome E2E rerun used the original ZIP, scripted model responses, real HTTP routes and disposable SQLite. Three selected 150 × 150 images matched the original ZIP members byte for byte. The copied private draft edit left the stored report unchanged. Editing round one from custom orders to ready-made only advanced revision 2 to 3, removed the sample answer and preserved the exact 33-character deadline. The replacement draft labeled the shared question `[Options A, B, C]` and the single-SKU question `[Option A]`. A new backend process and a fresh browser page restored the edited report with GETs and zero writes. Desktop (1280 px) and mobile (390 px) views remained readable.

All 214 offline backend tests and all 27 Chrome tests passed, along with frontend typechecking, the production build and whitespace checks. No paid requests were made for the review fixes.

A fresh Astra 6.2 high review of `e240ce2` confirmed all three original cases, then found two regressions. A joint finding could append B’s term to a correct A term or falsely deny a term in A’s name. A valid hyphenated UPC copied into a product question was misidentified as the buyer’s phone number.

Both follow-ups were exercised through the report endpoint with the original ZIP and a mocked provider. The mixed-SKU and false-negative claims returned `invalid_review`, while a correct multi-term claim, a true negative, and a question about the UPC returned `reviewed`. The same two invalid and three valid results were verified on databases written by `6f38c3c`, without any provider call on restore. The original-ZIP Chrome edit/restart walkthrough passed again with both sets of fixes. All 240 backend tests passed, including the unchanged Phase 2 question suite.

Astra’s review of `f6b438c` found a mixed list starting with `316` could hide a wrong-SKU term, and a correct sentence switching from A to B could be rejected. The regression tests cover numeric and English list items and positive and negative claims about another named option. With the original ZIP, the report endpoint rejected both incorrect cases and accepted both correct controls. Opening four databases saved on `6f38c3c` gave the same results with zero provider requests. All 255 offline backend tests passed. The original-ZIP Chrome edit/restart check again restored revision 3, the scoped supplier draft and matching SKU images.

### Follow-up to the review of `12b9d4f`

The original ZIP reproduced all three findings through `POST /api/reviews` on `12b9d4f` with scripted OpenAI SDK responses. Both selected names contained `粉蓝`, and only B’s contained `贴画`. The earlier build accepted a claim attributing `贴画` to A “according to the seller”, a claim that `粉蓝` appeared only in B, and supplier actions missing one or all of their questions.

On the fixed branch, those four malformed responses each exhausted one correction attempt and returned `invalid_review`. The correctly qualified claim about B, a genuinely exclusive term, and a complete supplier draft all returned ready on the first attempt. Restarting against seven databases written by `12b9d4f` produced the same four rejections and three valid reports, preserved the answers, and issued zero provider requests. Buyer-only decisions can still have an empty draft.

The Chrome walkthrough uploaded the original ZIP, selected three distinct SKUs, edited and copied the draft, then changed the first-round answer from custom orders to ready-made stock. Revision 3 replaced revision 2, removed the dependent sample answer, kept the 33-character deadline exactly, and rebuilt the `[Options A, B, C]` and `[Option A]` questions. All three served images matched the corresponding ZIP members byte for byte before and after restarting the backend. Fresh desktop and mobile browser contexts restored the edited report using GET requests. Model responses were scripted locally throughout.

`python -m pytest -q backend/tests` passed 288 tests. `npm --prefix frontend test -- --workers=2` passed 27 Chrome tests. `npm --prefix frontend run typecheck`, `npm --prefix frontend run build` and `git diff --check` passed.

Astra’s follow-up on `05cc61d` confirmed those results and found two additional cases. In the original ZIP, A’s comparison cell could say “The SKU name includes 贴画” despite citing its own lettering variant. The same build rejected the true statement that only B’s name included **both** `贴画` and `粉蓝`. The candidate binds an implicit SKU name to its comparison cell and validates the complete condition for an exclusive claim. On fresh reports and on four databases saved by `12b9d4f`, the wrong cell and a false combination returned `invalid_review`; the correct cell and true combination remained ready. The seven prior fresh-generation cases kept their expected outcomes. All 309 offline backend tests passed.

The review of `edc2f75` confirmed all 11 previous cases. It found that numeric operands could disappear from a compound claim and that an image-only observation about an illegible name could be rejected. On the subsequent candidate, both fresh generation and startup rejected false combinations involving `316` or `318`, preserved the true `316`/`贴画` combination, and accepted a thumbnail visibility limitation while rejecting a false literal name claim backed by that same image. The five legacy databases were generated by `12b9d4f`. Recovery issued no requests. The full backend suite passed 314 tests.

A final comparison check on the original ZIP separated the `316` in A’s cited name from the `750 ml` visible on its cited thumbnail. The combined sentence and two-sentence control both generated and reopened, while the false claim that the name contained both numbers returned `invalid_review` and was withheld on restart. This covers the regression reported on `d61c993`. All 316 offline backend tests passed.

## Version 2 evidence and explicit regeneration

The redesigned provider response binds each text excerpt, visible measurement, or visible detail to a source and, for SKU-specific evidence, an option ID. The application assembles the public report and supplier draft after validating those references against the evidence actually sent. Older live runs above exercised the previous report contract.

The offline endpoint walkthrough used the original 5,284,828-byte Website Test.zip locally. The independent inventory read the HTML `img[data-sku]` mapping, decoded its member paths, and inspected the three original 150 × 150 thumbnails. Each showed a 750 ml label. For every scripted SDK call, the verification compared the actual image payload bytes and source IDs against those members. Each case saved answers and reopened SQLite without an additional provider request.

| Provider response and evidence | Expected behavior |
| --- | --- |
| A’s name excerpt and A’s attached image claim | Both claims publish in A’s cell; the claim IDs point to separate text and image sources. |
| A’s thumbnail removed from a disposable ZIP, A’s source retained as metadata | An affirmative image claim exhausts one correction and returns `invalid_review`. A truthful missing-source unknown publishes without an image measurement. |
| A’s observation cites B’s available, attached thumbnail | Rejected with both the original and reversed selection order. B’s own claim against the same image publishes in both orders. |
| Image observation cites the name source; arbitrary summary, title, or supplier question supplied | Each invalid response gets one correction and fails before publication. A private email in buyer context stays out of the valid supplier draft. |

A separate check used a real saved case created by the `b3fe973` backend, with three selected SKUs, a ready-made answer and a deadline with surrounding spaces. Two starts of the updated app preserved the case, revision, selected order, rounds and exact answers, withheld the unversioned report, and made zero provider requests. Explicit regeneration produced one version 2 report. Replaying the operation and restarting again both returned that report without another generation.

The numeric conflict check produced one supplier question naming A’s 720 ml shared selector and 750 ml thumbnail. Equal readings and unrelated source pairs failed. Across 15 original/derived-ZIP cases, six valid responses published on the first attempt and nine malformed responses returned `invalid_review` after one correction. Each reopened with its original answers and outcome.

A local Chrome run independently verified the shared 720 ml selector and each of three separate name and 750 ml thumbnail controls. All three image responses matched their original members before and after restart. Editing and copying a draft preserved its stored version. Changing the first answer from custom orders to ready-made advanced revision 2 to 3, removed the sample round, retained the spaced deadline, and replaced the scoped draft. A new backend and fresh browser contexts restored the edited case using GETs only. The legacy database regenerated only after clicking **Regenerate report**; removing A’s image showed the correct limitation. At 1280 px and 390 px the top, middle, comparison and bottom views were readable with no page overflow or overlapping controls.

`python -m pytest -q backend/tests` passed 264 offline tests, including replacements for the old English-sentence parser tests. `npm --prefix frontend test -- --workers=2` passed 28 Chrome tests. Frontend typechecking, the production build and `git diff --check` passed. The v2 report checks used scripted provider responses; zero paid requests were made.

### Follow-up to the review of `a7dfe95`

The first independent Astra review of the redesigned report reproduced six problems. A buyer answer could influence the wrong SKU or lose its negation. A shared measurement could appear under the wrong heading. Supplier questions could omit a supported preference or the deadline topic. Coverage notes could repeat across all three groups. A SKU name in a table cell could hide the limitation for its missing thumbnail.

The follow-up regression suite exercises the actual issued scope, complete answer, skipped and superseded answers, incompatible units, positive and negative screen-printing preferences, delivery timing, and neutral questions for ambiguous answers. Coverage and per-cell limitations are tested in both rendered reports and Chrome. In particular, the missing-thumbnail cell retains A’s available name evidence, explains A’s missing photo, and leaves B’s and C’s cells independently sourced.

Three copies of reports saved on `a7dfe95` reopened twice each with their original answers and selection. Both correctly scoped reports remained ready. Applying A’s answer to B withheld the report and allowed an explicit retry. These reads made zero provider requests. The separate unversioned database still required explicit regeneration.

The follow-up passed 291 offline backend tests, 30 Chrome tests, frontend typechecking, the production build and whitespace checks. The 15-case local ZIP matrix repeated its six valid and nine invalid outcomes. The Chrome walkthrough covered the original ZIP, a disposable copy missing A’s image, restart after editing, and explicit regeneration of the legacy case at desktop and mobile widths. Provider output was scripted for all redesigned-report checks.

### Follow-up to the review of `359d195`

All four reported cases reproduced through the FastAPI review endpoint on the earlier commit using the original Website Test.zip. Three incorrect reports published and survived restart. The fourth case, two compatible finish requirements, returned `invalid_review` after one correction.

| Original-ZIP case | Expected and observed on the follow-up |
| --- | --- |
| Shared capacity selector | Quoting `20ml` from the saved `720ml` failed validation. The whole value and a supported difference with a SKU’s thumbnail published. |
| Capacity label on A’s 150 × 150 thumbnail | Either a readable `750 ml` claim or an unreadable limitation can publish on its own. Using both for the same source and topic failed validation. |
| Required screen printing followed by “Any finish is acceptable” | A response that selected only the superseded answer failed. The neutral supplier question remained available and preserved both original answers. |
| Screen printing and matte surface required for the same SKU | The report kept both quotes, their SKU scope and both supplier questions. The previously failed case generated on one explicit retry and reopened twice without another request. |

The ten scenarios exercised on `359d195` include six valid controls. Three additional cases exercised questions carrying the new buyer facet. On the follow-up all 13 scenarios matched their expectations. Nine valid reports published on the first response and four invalid selections exhausted the permitted correction. The local SDK transport compared the transmitted thumbnail bytes with an inventory read directly from the archived HTML and image members.

The Chrome walkthrough used the production frontend, local FastAPI and SQLite with scripted SDK responses. Editing the earlier order preference from custom to ready-made advanced revision 2 to 3, removed the dependent sample answer and preserved the exact deadline including surrounding whitespace. The copied and saved drafts, three original SKU images and GET-only restart were checked at 1280 px and 390 px. A separate browser check expanded a numeric excerpt to the complete original range and its English translation at both widths.

Databases produced by the actual `b3fe973`, `a7dfe95` and `359d195` backends were reopened on the candidate. The unversioned report required one explicit regeneration. Valid versioned reports reopened without calling a provider, including a saved matte-only report whose rebuilt draft now explicitly asks for matte. The three incorrect ready reports from the earlier commit became retryable failures with their case, selection and answers intact.

The follow-up passed 318 backend tests, 30 Chrome tests, frontend typechecking, the production build and `git diff --check`. The question and report SDK checks used the current `buyer-questions-v3` and `purchase-review-v4` prompts with scripted responses. Zero paid provider requests were made.

### Follow-up to the review of `85e01b2`

Astra independently repeated the four fixes above and identified three further cases. An untagged historical question answered “No” could leave a revoked decoration requirement active. A later waiver could leave an earlier buyer-context statement displayed as a binding requirement even when the supplier draft was neutral. A revised delivery deadline could appear alongside the old one.

The additional original-ZIP checks exercise those three invalid reports and five valid controls through the review endpoint. The valid cases keep the revised deadline only, preserve unrelated sample answers and surface decisions, and publish neutral wording for a waived requirement. The checks reopened copies of all three incorrectly published databases from the review, preserving the original buyer context, rounds, SKU order and revision while withholding the obsolete report. No restore invoked the provider. The combined matrix covers 21 fresh-and-restart scenarios.

The full offline backend suite passed 333 tests. All 30 Chrome tests, frontend typechecking, the production build and whitespace checks passed. The original-ZIP Chrome edit and restart walkthrough again verified the edited draft, dependent answer removal, exact deadline and the three selected SKU images at both desktop and mobile widths.

### Follow-ups scoped to buyer requirements and selected SKUs

The original Website Test.zip reproduced three errors on `1e950d0`. A valid stock-only decision and an independent engraving requirement both failed to publish. A deadline change for A left both dates current for A. The same archive now has these acceptance results through the question and review endpoints.

| Buyer decision | Expected report and supplier draft |
| --- | --- |
| Keep ready-made stock only | The original stock requirement stays active. The supplier is asked about ready-made stock. |
| Require laser engraving in the initial context, then allow any surface texture | Laser engraving stays active for its original SKUs. The supplier is asked about engraving. |
| Start with 1 December for A and B, then replace A’s deadline with 15 December | A shows only 15 December and B shows only 1 December. Editing A to 20 December changes only A’s report entry and supplier question, including after restart. |

The 24-case endpoint suite passed with the original archive via `PURCHASE_TEST_ZIP` and uses the redacted archive by default. Scripted SDK responses exercise the application’s production request and validation paths. A separate transport check compared transmitted thumbnail bytes with the original archive. No tests used a paid provider.

The production frontend was exercised in Chrome at 1280 × 850 and 390 × 850. A stock-only answer for A affected only A, while custom-order and MOQ questions applied only to B and C. Editing the first answer made all three ready-made, removed the dependent sample answer, and preserved the buyer’s exact delivery-date wording. The copied draft and revision 3 reopened via GET after a backend restart. All three SKU thumbnail hashes matched their original archive members.

Copies of 26 databases written by `1e950d0` reopened twice without provider requests. Seven reports could be reconstructed directly, nine earlier validation failures stayed failed, and ten ambiguous cases retained their inputs and required explicit regeneration. Each explicit regeneration completed with one scripted request and reopened without further requests.

The integrated checks passed 373 backend tests, 31 Chrome tests, frontend typechecking, the production build and whitespace checks.

### Product-link integration and follow-up review

The branch incorporates the product-link import from main at `324616b`. Chrome exercised the default link path through a controlled supplier response and the fallback from an upstream error to the actual Website Test.zip. The ZIP case reached revision 3 after editing an earlier answer, copied its revised supplier draft, and reopened after restarting the backend at both desktop and mobile widths. Its three images matched the original ZIP entries by SHA-256.

Astra’s review of `2276432` identified three additional cases. The combined branch accepts “The logo needs to be laser engraved.” and carries engraving into the scoped supplier question. Skipping a follow-up about a settled deadline preserves that deadline, while a separate unanswered sample question remains open. A legacy report containing independent screen-printing and matte-finish requirements can now reopen with both original requirements and supplier questions.

The expanded original-ZIP endpoint suite passed 26 cases. The database comparison used 26 cases saved on `1e950d0`: nine reports reopened directly, nine retained validation failures, and eight needed explicit regeneration. A second set of 26 cases written by `2276432` retained their exact saved state over two restarts after link support was added. Recovery used local SQLite and made zero provider requests.

All 417 backend tests and 36 Chrome tests passed with the combined implementation. Frontend typechecking, the production build and whitespace checks passed. A separate HTTP test exercised a simulated 1688 product response, linked SKU selection, its original image, purchase review, workspace replacement and restart.

### One buyer decision per requirement and SKU

Both P2 findings reproduced with the local original Website Test.zip before the change. The `0d6749f` backend accepted a settled deadline as open when the only cited answer was a skipped sample question. The contradiction survived restart. Two SQLite databases produced by `2276432` retained the original engraving context and the independent texture waiver. On `0d6749f` the reviewed database reopened with generic supplier wording, while the unfinished case still had no engraving detail.

The original-ZIP endpoint suite now covers 27 scenarios. In the new case, the report provider linked a deadline finding to the unresolved sample decision. The review failed validation. A corrected response left only sample open. Editing A's skipped revisit to 15 December produced a report and supplier draft with 15 December for A and 1 December for B and C. They reopened unchanged after restart.

The completed engraving case required explicit regeneration. The unfinished case used engraving in its first report. Both produced exactly the same supplier draft as a fresh case with identical answers and selected SKUs. Case IDs, requirement IDs, answers, selection order and evidence were retained. A previously published wrong-topic deadline report also required explicit regeneration; its corrected report retained the date and left sample open. For an older valid report, reopening preserved the public report and made no provider request.

Existing SQLite cases from all 26 endpoint scenarios on `0d6749f` also retained their exact public state after two restarts with provider calls forbidden (19 reviewed, seven earlier validation failures). The original ZIP and these local databases were used only for verification.

The full backend suite passed 426 tests. Frontend typechecking, the production build, all 36 Chrome tests and whitespace checks passed. The production browser walkthrough uploaded the original ZIP, changed custom ordering to ready-made, changed only A’s deadline from 15 to 20 December, and checked the copied draft. B and C kept 1 December. Editing removed the dependent sample round and kept the buyer’s exact date text. Three SKU photos and the outbound report image bytes matched the original archive. After restarting the backend, fresh desktop (1280 × 850) and mobile (390 × 850) sessions restored revision 3 with GET requests only. The eight inspected screenshots showed no overlap or horizontal overflow. Providers were scripted throughout.

A separate original-ZIP probe surfaced an interview issue. When the buyer explicitly gave opposite order instructions for one SKU, the question provider could submit both as active. With those two records, even a report response containing zero findings produced both a ready-made-only and a custom-order supplier question for the same SKU. The same report reopened after restart. This case is distinct from mislabeling an unrelated skipped answer and from recovering a missing context detail.

## Review รวมก่อนปิด ticket

The integrated Standards and Spec reviews and T1–T5 acceptance remain pending. Issue #3 stays open for that review.

## Proofly redesign · 2026-11-21

After two offline question rounds, the review displayed two findings. Opened the first finding’s source and checked its Option A label against the selected SKU. The editable supplier draft and the ordered comparison remained accessible in both layouts. These observations cover presentation and source navigation only. See [integrated UI verification](../purchase-04-integrate-and-verify/verification.md#proofly-redesign--2026-11-21) for code state, commands and screenshot coverage.

Checked the revised report layout on the Proofly working tree over `8a33503`. Playwright opened the restored three-SKU report at 1280 × 837, 390 × 837 and 900 × 480. Coverage immediately preceded the comparison at every width. During scrolling the supplier draft pinned at 16 px on desktop and stayed in normal order on mobile. Verified draft editing, reachable Copy message control, no panel/findings overlap and no horizontal page overflow. Inspected top, middle and bottom screenshots on desktop and mobile. `npm --prefix frontend run typecheck`, `npm --prefix frontend run build` and `node_modules/.bin/playwright.cmd test tests/review.spec.ts --reporter=line` (from frontend with installed Chrome) passed, including all eight review tests.

Compact report header on the working tree over `8a33503`. The restored three-SKU report heading moved up 92 px at 1280 px and 98 px at 390 px. Opened and closed Replace listing at 320, 390, 768 and 1280 px, verified the current listing details and replacement warning, and checked header bounds, document width and sticky draft overlap during scrolling. Inspected top, middle and bottom screenshots at 1280 and 390 px. Typecheck and build passed via `npm --prefix frontend run typecheck` and `npm --prefix frontend run build`. The focused Playwright check passed via `node_modules/.bin/playwright.cmd test --grep "explains replacement" --reporter=line` from frontend with installed Chrome.

## PR #10 local integration · 2026-11-21

Code state: staged merge of PR #10 head `7c698814da8c9ff1ad8548ecb73194e614d3ede4` and Phase 3 `2652215bc120bc2ed38ab82a2bd64d751df2dec3`, incorporating main `324616b`. The SHA-256 of `git diff --cached --binary HEAD -- backend frontend README.md docs` was `13c5c301bf516172dfea94002e7d7d5f31b8aec1e28ce9be60525c73b85cf756`. All 87 tracked source, contract, and sample files compared byte for byte with the isolated copy used for checks. The staged index had no unmerged entries.

- Replayed “Screen printing is required” followed by “Any finish is acceptable” through the ZIP, issued question, saved answer and report endpoint. The provider's attempt to reintroduce the waived requirement returned `invalid_review`. A canonical response also failed validation when it attached a buyer-decision finding to an already-settled requirement.
- With opposite order instructions for A, the report showed one open decision with both source statements and omitted A from order-specific supplier questions. B and C retained their ready-made requirement. The open decision survived restart and could be replaced by an explicit answer for A. A pre-guard report with the contradiction kept its case, revision, context and selection, withheld the saved draft, and required an explicit regeneration. Recovery made zero provider calls in that test.
- The Chrome walkthrough used the actual sample ZIP, real local HTTP routes and disposable SQLite with scripted providers. At both 1280 × 840 and 390 × 840, it finished two rounds, opened A's evidence and matched its image SHA-256 to the independent sample inventory, and copied an edited draft. Editing round one changed the supplier draft from custom-order wording to ready-made wording at revision 3, removed the dependent second round, and preserved the exact deadline answer. The same report and image reopened after a backend restart in a fresh browser context using only GET requests.
- Inspected the top, middle, and bottom of the import, selection, interview, and report views at both widths. Playwright detected zero horizontal overflow or overlap between the sticky panels and adjacent content. The report retained Proofly's findings and supplier message layout, with saved requirement decisions below the comparison.

Commands and results:

```sh
OPENAI_LIVE_TRANSLATION=0 OPENAI_LIVE_QUESTIONS=0 OPENAI_LIVE_REVIEW=0 .venv/bin/python -m pytest -q backend/tests  # 430 passed
PURCHASE_TEST_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm --prefix frontend test -- --workers=2  # 38 passed
npm --prefix frontend run typecheck  # passed
npm --prefix frontend run build  # passed
git diff --cached --check  # passed
```

The one-off browser harness ran `PURCHASE_BACKEND_URL=http://127.0.0.1:18110 frontend/node_modules/.bin/vite frontend --host 127.0.0.1 --port 5173 --strictPort` and `node .verification/walk.cjs` against Uvicorn with a controlled supplier transport and scripted translation, questions, and review. Its first run stopped at an edit-button locator using the visible text. The rerun used the existing accessible name `Edit round 1` and completed both viewport flows. Live provider validation, integrated T1–T5, and fresh Standards and Spec reviews remain pending.
