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
