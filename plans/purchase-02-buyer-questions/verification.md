# PR-02 verification

Status: Phase 2 handoff for issue #2. The internal `ReviewInput` is available to the next phase after the buyer ends the interview. The integrated report and T1–T5 review belong to later phases.

- Branch `phase-2/buyer-questions` from `dcabdf1`, the merge of PR #5 at `547341a`.
- [Local run instructions](../../README.md) include an offline mode and a persistent test database for restarting the app.
- Tested implementation `16cced4`, including the frontend recovery follow-up and mobile history fix. With live question and translation calls disabled, `.venv/bin/python -m pytest -q backend/tests` passed all 99 tests. The frontend typecheck, production build and all 15 Playwright tests passed using the installed Chrome. `git diff --check` passed.
Authority: [plan](plan.md), [question contract](../../docs/contracts/question-contract.md)

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
