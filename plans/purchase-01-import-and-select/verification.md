# PR-01 verification

Status: Phase 1 implemented and verified for handoff. Awaiting PR review.

- Branch `phase-1/import-and-select` from `55bbc64`. Tested implementation commit `a79ec3e98533be78d2b67988f0b4c57acb612402`.
- Open the app using the [root README](../../README.md). Phase 1 confirms the ordered selection against the active server snapshot through `POST /api/selections`. Starting questions or persisting a buyer interview belongs to Phase 2.
- The real-ZIP browser walkthrough used the no-network translator. The separately authorized live translation used the raw ZIP parser and production translation adapter. Both paths are recorded below.
- Phase 2 and the integrated T1–T5, Standards/Spec review, and demo gates remain pending.

Authority: [plan](plan.md), [ชุดเกณฑ์รับหลักฐาน](../../docs/contracts/intake-contract.md#กรณีที่ต้องยืนยันตอน-build)

## PR #5 review fixes after `9df0627`

- The new regressions failed on the reviewed baseline. Swapped image keys produced a ready snapshot with incorrect photos. A single SKU with the same display text as its shared option failed to import. A failed restore request left the browser showing an empty upload screen.
- `.venv/bin/python -m pytest -q backend/tests` passed 52 tests after the compatibility and document-wide ID follow-ups. The importer rejected conflicting image and label ownership while retaining all 12 verified photos when labels explicitly link cards to radio controls outside them. A duplicate label target across SKU groups was rejected before publication. The single-SKU test imported the listing over HTTP and verified distinct option and shared-value translations using a mock provider.
- The frontend typecheck and production build passed. Playwright passed all 6 tests, including read-only recovery, deliberate replacement, and a delayed restore response. Browser tests used `PURCHASE_TEST_BROWSER` to select the installed Chrome.
- In a separate Chrome walkthrough, the unmodified user-supplied `Website Test.zip` matched an independent inventory from its saved HTML and assets for 12 SKU names and image hashes, the shared 720ml value, and 40 seller attributes. Ordered selection succeeded. Stopping the backend produced a visible restore error. Restarting it with the same SQLite file and clicking **Retry saved listing** recovered the same snapshot and all 12 photos.
- A disposable replacement ZIP changed two photos, the title, and one seller attribute. The UI showed the replacements, all 12 obsolete photo URLs returned 404, and the old confirmation returned 409. Another backend restart recovered the replacement. Re-uploading the original restored its evidence. The walkthrough passed again after the compatibility follow-up. It used offline translation, checked the UI at 390 and 1280 px, and made no external browser requests.

## ตรวจส่วนของตนก่อนเปิด PR

บันทึกผลแยก A/B/C ตาม [รูปแบบส่งงาน](../../docs/team-plan.md#วิธีส่ง-pr-ของแต่ละคน) ใช้คำสั่ง app ที่สร้างจริง

| ID / เจ้าของ | วิธีตรวจและผลที่ต้องได้ | หลักฐานก่อนส่ง |
| --- | --- | --- |
| V01-A / A | รัน frontend type check/build และ harness เดิมสำหรับ 0/1/3/4 ตัวเลือก ลำดับ/context รูปตาม SKU, null translation/ภาพขาด และผล upload เก่า เปิดแอปด้วย raw ZIP ดูบน/กลาง/ล่างที่ 1280px และ 390px ก่อนและหลังเปิดรายละเอียดผู้ขาย ชื่อ SKU รูป และค่าร่วมเห็นก่อนยืนยัน รายละเอียดอื่นพับไว้ ภาพไม่ขยายเกินต้นฉบับ ถ้าใช้ sticky ให้รัน overlap check ระหว่างเลื่อน | คำสั่ง ผล smoke steps และภาพที่เปิดดู ระบุ harness/HTTP จริง ตรวจเส้นทาง ZIP จริงร่วมกับ B/C ก่อน gate 1:30 |
| V01-B / B | รัน parser/HTTP กับ curated และ raw ZIP ด้วย stub ก่อน เทียบ title, 12 SKU ตามลำดับ, 720ml จากผู้ขาย และรูปตรง SKU ด้วย hash จาก inventory ทดสอบ unsafe/limit/ambiguous และภาพ missing/unreadable รวม image GET และผล import เก่า เปิดรูปจาก snapshot ที่กู้หลัง restart และตรวจเคสเกินเพดานงาน parser ว่าจบด้วย error ก่อนแปลและไม่ publish แล้วตรวจว่า import ที่ตามมายังใช้ได้ | คำสั่ง ผลเทียบ inventory และคำขอที่ถูกปฏิเสธ บันทึกเพดาน เวลา parse และ peak memory แยกจาก API บนเครื่องเดโม T4/T5 แนบ mutation provenance ภายใน 2:30 |
| V01-C / C | รัน schema/translation checks สำหรับ IDs/duplicates, คำแปลชื่อใช้ source เดียวกันและต้นฉบับไม่เปลี่ยน รวม timeout/validation/429/5xx เทียบ auth/quota แล้วแปล raw ZIP ผ่าน API จริงอย่างน้อยหนึ่งครั้ง อ่านคำแปลทั้ง 12 SKU คู่ต้นฉบับ ต้องเข้าใจง่ายและคงวัสดุ ตัวเลข หน่วย และคำที่กำกวม เช่น ไม่แปล `贴画` ให้กลายเป็นงานสกรีนที่ต้นฉบับไม่ได้ยืนยัน ตรวจว่า payload ไม่มี admin/debug/tracking | แยก deterministic checks จาก live run ระบุ prompt version, model และ cache mode ปิด app cache สำหรับผลวัดสด ถ้า API ใช้ไม่ได้ให้ลง pending |

## เกณฑ์รวมของ ticket

| กลุ่ม | สิ่งที่ต้องตรวจจริง | หลักฐานที่ต้องเก็บ |
| --- | --- | --- |
| เส้นทางผู้ซื้อ | ZIP จริง → 12 options/ค่าร่วม/คำแปล → เลือก 1–3 พร้อมรูปตรง SKU | ผล UI/HTTP และการจับคู่ต้นฉบับ โดยไม่ hardcode IDs |
| ไฟล์ไม่ครบ | รูปหาย/อ่านไม่ได้/remote และโครงสร้างไม่รองรับ | สถานะ source/limitation หรือ failed ที่ตรงสาเหตุ |
| Trust boundary | unsafe paths, normalization collisions, limits, client source/path fields | คำขอถูกปฏิเสธก่อนนำข้อมูลไปใช้ ไม่ extract/serve HTML |
| Translation | null fallback เทียบกับ config/auth/quota failure และ name_en เท่ากับ source text_en | ผลที่แสดงและ call count/attempts จริง |
| ผลเก่า | หน่วง import เก่าแล้ว upload ใหม่ | IDs/generation และสิ่งที่ UI แสดง |

เมื่อพัฒนาแล้วให้เติม actual commands, code-state reference, ผลจริง/ผลที่คาด และสิ่งที่ยังค้าง ไม่บันทึก secret/raw buyer context


## ผลจริงของ A

- `npm --prefix frontend run typecheck` and `npm --prefix frontend run build` passed. The Playwright suite passed all 4 cases against deterministic API responses. It exercised 0/1/3/4 selections, preserved order and Unicode context, distinct image states, native image sizing, failed imports, and stale responses.
- Real HTTP walkthrough on the demo Mac: upload the redacted raw ZIP, open seller details, choose three SKUs in a different order, enter context, and confirm. The browser matched the 12 names and each served image hash to the independent inventory. A curated ZIP upload then cleared the prior draft. The shared seller value was 720ml and initial selection count was zero.
- Checked the top, middle, and bottom of the page at 1280 and 390 px with seller details closed and open. A separate translated fixture exercised bilingual wrapping. No horizontal overflow or overlapping controls were observed. Screenshots with the real ZIP used the offline translation path.
- Port 5173 and port 8000 were occupied by another project. The browser retained the required origin `http://127.0.0.1:5173` while Chromium routed HTTP to a Vite preview on 5175. Vite proxied to the offline backend on 8015. The browser checked the returned Origin header.

## ผลจริงของ B

- `.venv/bin/python -m pytest -q backend/tests` passed 40 tests on the implementation commit, including 19 intake/HTTP tests. Both real ZIPs matched the product title, all 12 ordered SKU names and image hashes, the shared 720ml relation, and all 40 attributes in the independent inventory.
- Checked shape, Origin, body/archive/parser limits, ambiguous input, missing/unreadable/unmapped image handling, stale imports, and recovery on the next valid import. A separate two-process check recovered the original snapshot ID and all 12 image hashes from SQLite, then confirmed two options in the requested order. Stored snapshots and images were read back without calling the model.
- Resource evidence on macOS ARM / Python 3.15.2, one fresh process per ZIP. Curated parse took 0.0162 s with 44.5 MiB peak process RSS. Raw parse took 0.1037 s with 59.1 MiB. These numbers exclude translation. The ZIP central-directory scan now enforces 1,000 entries before constructing the Python ZIP inventory. The importer accepts bounded, single-volume ZIP files and rejects ZIP64 end records.
- HTML limits include 20,000 tags/comments, 32,768 characters per counted start tag/comment, 128 nesting levels and 20,000 entity references. Listing processing caps are 128 options, 160 attributes, 4,096 characters per source text part, and 8 JSON candidates of up to 65,536 characters and depth 24.
- Generated T4/T5 with [make_variants.py](../../samples/make_variants.py) and parsed both. T4 retained 12 images and delivered the seller marker in the title to the mock transport with the report attribute absent. T5 retained the 12 options with exactly the seventh image missing and the other 11 intact. Original ZIPs were unchanged.

## ผลจริงของ C

- The 21 translation tests used the SDK with a mock transport. They covered exact source IDs, unchanged originals, SKU names sharing the source translation, authorization/quota failures, bounded retries, refusal/incomplete output and deadline behavior. The linked frontend fixture was checked against the snapshot and the two contract traces.
- A raw ZIP integration check exercised the normal import route, parser, adapter, and SQLite with a mock provider. Success published 12 translated options. Two transient failures produced a limitation and null translations. Quota or disabled live translation produced intake failure with no published snapshot.
- One authorized live run used `samples/raw-save/raw-save-redacted.zip`, the production parser, prompt `translate-listing-v1`, and `gpt-6.1-terra` with medium reasoning. No app cache was used. The request contained 54 allowlisted text sources. **1 provider request, 20.27 s, 3,091 input tokens and 1,451 output tokens.** No retry was needed.
- Read all 12 translated names alongside their Chinese originals. All retained 316, the correct six colors or color pairs in each series, and the distinction between 贴画 (“Decal”) and 字母 (“Letters”). For example, 暮色316-贴画粉蓝 became “Twilight 316 - Decal Pink/Blue”. No name asserted a screen-print process. This run measures one listing and does not establish general model quality.

## Review รวมก่อนปิด ticket

Required review: pending implementation/integrated state คู่ Standards/Spec ต้องตรวจ acceptance ของใบนั้นอย่างชัดเจนก่อน complete บันทึก base/target/state, reviewers, findings/dispositions/final checks และ full-round ledger ต่อ ticket การส่งต่อเพื่อรวมยังไม่นับว่าปิดงาน

## TTP Cargo link import, 21 November 2026

Tested the uncommitted working tree on `codex/ttp-link-import`, based on Phase 2 commit `799e028`. The link and ZIP paths use the same SKU and image validation, translation seam and SQLite publication. Live checks used the offline translation and question adapters with a temporary database.

| Real app check | Observed result |
| --- | --- |
| Paste the [sample link](https://taobao.ttpcargo.com/product?provider=1688&itemId=1035537244639), select a SKU and confirm | Product title and all 12 ordered Chinese SKU names matched the saved inventory. SHA-256 of the bytes served for each of the 12 SKU images matched its saved thumbnail. Round 1 opened. |
| Paste a [second product](https://taobao.ttpcargo.com/product?provider=1688&itemId=950217987281), select a SKU and confirm | Compared against a separate signed-out Chrome page on TTP Cargo. The title, eight ordered SKU names and all eight original thumbnail hashes matched the app. Round 1 opened. |
| Choose the raw browser-save ZIP and reload | All 12 names and image hashes matched the saved inventory. The listing restored after a page reload. |
| Browser layout | Inspected the form, SKU grid and confirmation area at the top, middle and bottom at 1280×820 and 390×844. Controls and Chinese labels were readable. No horizontal overflow or overlapping controls observed. |

Commands from the worktree root, using the existing project virtual environment for Python and local Google Chrome for Playwright:

- `python -m pytest -q -p no:cacheprovider backend/tests` passed 138 tests, including link validation, wrong product identity, thumbnail redirects and a slow link request superseded by a ZIP.
- `npm --prefix frontend run typecheck` and `npm --prefix frontend run build` passed.
- `PURCHASE_TEST_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm --prefix frontend test` passed 23 tests, including recovery from a link failure using ZIP.
- `python -m backend.tests.offline_server --port 8057` and `PURCHASE_BACKEND_URL=http://127.0.0.1:8057 npm --prefix frontend run dev -- --port 5183` served the real UI checks. A temporary Chrome host rule mapped the normal app origin on port 5173 to port 5183 so the existing demo could keep running. The Playwright smoke check fetched the images through each rendered option and compared their bytes to the expected source.
- An exploratory product-detail request for item `1098255410083` returned HTTP 502. Live availability depends on TTP Cargo. Existing supported SKU layouts and the CDN thumbnail allowlist still apply.

Paid AI translation and model quality were outside this import check. Fresh independent Standards/Spec review of the integrated release remains pending. Review should check remote resource limits, product identity binding, and handling of failed replacement imports.

### Pre-commit review and fixes, 21 November 2026

Base `799e028`. The first independent Standards/Spec review identified four findings. The fixes and checks below apply to the uncommitted branch. The SHA-256 of the reviewed backend, frontend source and test diff, including the untracked link importer, is `33f6a8d4bb12452fa5a5d787b1dd783f4d6814895265e8a4a456aac416cfa570`.

| Finding | Regression result |
| --- | --- |
| Delayed link request could restore a listing after Start over | A successful reset now advances the import ordering marker. A delayed link returned HTTP 409 and the workspace stayed empty. The existing delayed-link versus newer-ZIP case also passed. |
| Import could clear an interview without explaining the consequence | A saved interview with submitted answers showed the consequence before either import action. Both buttons had the same accessible description. Restoring the interview issued zero writes. When the import POST failed to reach the backend, both controls retained the warning before retry. |
| Waiting for upstream headers could hold the ZIP parser slot past the deadline | Product and thumbnail requests share a cancellable 75-second network deadline. With a shortened 300 ms deadline and a stalled headers response, the upstream wait was cancelled, the superseded link returned 409 and the queued ZIP published the expected listing. |
| Server reference-pack errors could blame the buyer’s ZIP | A valid sample with a missing pack, missing required fields, an invalid limitations field, empty guidance or limitations, a seller-source kind, or missing translation or URL returned HTTP 500 with `intake_failed`. No snapshot was published. |

- `python -m pytest -q -p no:cacheprovider backend/tests` passed 148 tests. `npm --prefix frontend run typecheck` and `npm --prefix frontend run build` passed. `PURCHASE_TEST_BROWSER='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm --prefix frontend test -- --output=/private/tmp/ttp-fixes-frontend-suite` passed 24 tests.
- Ran the real browser flow on the temporary backend and frontend described above with `node /private/tmp/ttp-fixes-qa/check.cjs`. For item `1035537244639`, all 12 names and thumbnail hashes matched the saved inventory. For `950217987281`, all eight names and thumbnail hashes matched a separately opened live listing. Both reached round one. Raw ZIP fallback matched all 12 saved thumbnails and restored after reload. Translation and questions used the offline adapters.
- Inspected selection and interview screenshots at the top, middle and bottom at 1280×820 and 390×844. The replacement warning was visible before the import controls on both screens. The labels wrapped within the mobile viewport, with no overlapping controls or horizontal overflow observed.
- Independent Standards and Spec reviewers re-examined all four findings on candidate `33f6a8d4`. During review, Spec found two additional cases within the original findings: loss of the warning after an undelivered POST, and acceptance of an empty or mislabeled guidance pack. Both were fixed and covered by regressions. Each reviewer independently ran all 42 backend intake tests and reported all four findings resolved with no additional blockers in the reviewed scope. The final integrated release review across PR-01–04 remains a separate gate.

## Proofly redesign · 2026-11-21

Chrome imported `samples/raw-save/raw-save-redacted.zip` using the browser file input at both viewport sizes. The listing showed 12 options. Selected two SKUs and entered buyer context. The 390 px selection shortcut focused the confirmation heading. Live TTP link retrieval was outside this run. See [integrated UI verification](../purchase-04-integrate-and-verify/verification.md#proofly-redesign--2026-11-21) for code state, commands and screenshot coverage.

The seller-claims panel now starts collapsed in the Proofly working tree over `8a33503`. On the restored 12-SKU listing in Chrome at 1280 and 390 px, a click expanded the claim tiles and Enter collapsed them. Top, middle and bottom screenshots showed no overlap or horizontal overflow. `npm --prefix frontend run typecheck`, `npm --prefix frontend run build` and `node_modules/.bin/playwright.cmd test tests/intake.spec.ts --workers=3 --reporter=line` (from frontend, installed Chrome) passed, including all seven intake tests.

Checked the sticky selection panel and highlighted listing status in the Proofly working tree over `8a33503`. Playwright opened the restored 12-SKU listing, toggled Replace, selected three SKUs and scrolled through the top, middle and bottom. At 1280 × 837 and 900 × 480 the panel stayed in its column and pinned 16 px from the viewport top during scrolling. At 390 × 837 it followed the grid in normal document order. The confirmation button remained reachable in all cases. Geometry checks found no panel/grid overlap or horizontal page overflow. Visually inspected top, middle and bottom screenshots at 1280 and 390 px. `npm --prefix frontend run typecheck`, `npm --prefix frontend run build` and `node_modules/.bin/playwright.cmd test tests/intake.spec.ts --reporter=line` (from frontend, installed Chrome) passed, including seven intake tests.
