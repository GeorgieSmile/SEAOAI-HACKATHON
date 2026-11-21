# PR-04: ตรวจเส้นทางจริงและเตรียมเดโม

**What to build:** ผู้ซื้อเริ่มจาก ZIP จริงแล้วไปถึงรายงานได้ครบ แม้เปลี่ยน SKU ข้าม/แก้คำตอบ หรือข้อมูลบางส่วนหาย ทีมมีหลักฐานตรวจจริง code reviews และซ้อมเปิดใหม่บนเครื่องเดโม โดยบอกข้อจำกัดตามจริง

**Blocked by:** None สำหรับเริ่ม harness/กรณีตรวจ/wiring; gate ZIP จริงต้องมี PR-01–03 implemented + seam-checked ตามด้านล่าง

**Status:** ready-for-agent; execution on competition day after selecting this Build task; implementation not started

**ผู้ประสานหลัก:** A; A คุม review/restart/rehearsal, B รวม HTTP/state/ZIP/security, C semantic quality/usage/model checks

**Authority:** [spec](../../docs/spec.md) story 15 และเส้นทางรวมของ stories 1–14; [integration gates/T1–T5](../../docs/integration-plan.md); [AI evaluation](../../docs/ai-execution-plan.md)

## Acceptance criteria

- [ ] ต่อ output จริงจาก PR-01 → PR-02 → PR-03 ไม่ใช้ fixture แทนระหว่างทาง เปิด ZIP แล้วเลือกสินค้า/ตอบหรือข้าม/เปิดหลักฐาน/copy draft ได้ตาม UI อังกฤษ
- [ ] ผ่าน T1–T5 ที่ integration-plan เป็นเจ้าของรายการด้วย actual model และอ่านความหมายเทียบหลักฐาน ตรวจ T3 ที่เปลี่ยนชุด/ลำดับ SKU และ T4/T5 ที่เปลี่ยนเฉพาะหลักฐาน ไม่ส่ง expected checks ให้โมเดล
- [ ] ตรวจหลายรอบ/จบก่อน/0/partial/skip, context ขัดกับคำตอบใหม่, zero-question HTTP, เปลี่ยนข้อมูลระหว่างรอ และ failure/retry/cache/duplicate แม้โมเดลรอบจริงหนึ่งรอบไม่ได้ออก path นั้น ใช้ deterministic HTTP/UI checks เสริมโดยไม่อ้างว่าแทน model quality
- [ ] ตรวจ boundaries ทั้ง upload/source/path/images และ cases ที่หน่วงผลเก่าแล้วเปลี่ยน ZIP/options/context/answers ไม่มีผล/ภาพผิดชุดหรือการแสดงผลล้มเหลวเป็นพร้อมใช้
- [ ] บันทึก actual model, billed usage/reasoning, latency, attempts/incomplete และจำนวน T1–T5/repeats ที่ทำจริง งบ/เวลาเป็นเป้า ไม่แต่งตัวเลข หาก Terra ยังผิดหลังตรวจ prompt/input จึงเทียบ Sol เฉพาะขั้นตามแผนและตรวจเส้นทางที่เปลี่ยน
- [ ] A คุม fresh Standards/Spec code review บน integrated commit/state ที่ตรึง โดย briefs/results/dispositions/ledger ครอบคลุม acceptance PR-01–04 ทุกใบ และมี focused checks ใช้ bounded review procedure เดิม การรีวิวเอกสารไม่แทน code review และไม่อ้างว่าผ่านถ้า required route/check หรือ major finding ยัง pending
- [ ] restart บนเครื่องเดโม กู้เคส SQLite หลังส่งคำตอบ และทดสอบเริ่มใหม่จาก ZIP และซ้อมเรื่องเล่าอังกฤษจริง ไม่มี sample fallback/ผลค้างเสมือนวิเคราะห์สด วิดีโอสำรองถ้ามีต้องมาจากแอปจริงและติดป้าย recording
- [ ] สรุป passed/failed/pending และข้อจำกัดที่ยังมีผล ไม่มีการ deploy สาธารณะหรือประกาศว่าครบทุกความเสี่ยงจากการผ่านเดโม

## งานของแต่ละคนก่อนเปิด PR

ใช้ [วิธีส่งงาน](../../docs/team-plan.md#วิธีส่ง-pr-ของแต่ละคน) PR ส่วน harness/wiring เริ่มส่งได้ระหว่าง PR-01–03 ส่วน gate ZIP จริงและการปิด ticket ยังต้องผ่านเกณฑ์รวมตามเดิม

### A: ตรวจ UI รวม คุมรีวิว และซ้อมเดโม

- [ ] A1 เตรียม UI/HTTP harness และเส้นทางตรวจ 0/partial/skip/edit/retry/stale ตั้งแต่ช่วงแรก เมื่อ API ต่อครบให้รันผ่าน UI จริงและเปิด source/ภาพ/copy draft ที่ตรงข้อมูล
- [ ] A2 ประสานผล V01–V03 และผล UI ใน T1–T5 กันไม่ให้แอปจริงใช้ fixture เก็บ actual actions/expected/actual พร้อม code state และ defects ที่ส่ง B/C แก้
- [ ] A3 ตรึง integrated commit เป้า 4:15 เตรียม briefs ให้ fresh Standards/Spec reviewers ครอบคลุม PR-01–04 เก็บผล/dispositions/rechecks/ledger ตาม bounded review เดิม ไม่ยก local PR checks เป็น code review
- [ ] A4 ร่วม B restart เพื่อกู้เคสเดิมและทดสอบ ZIP ใหม่ช่วงท้าย ซ้อมอังกฤษและสรุป passed/failed/pending/known limits ตามผลจริง ไม่เล่นผลค้างเสมือนวิเคราะห์สด

ส่งให้ B/C: defect ที่ระบุวิธีทำซ้ำและหลักฐาน; ส่งทีม: review/restart/rehearsal record ก่อนเปิด PR ส่วนงานตรวจ V04-A เฉพาะงานที่อ้างว่าส่งแล้ว งาน review/rehearsal ที่ยังไม่ถึงเวลาระบุ pending ไม่บังคับรอก่อน merge harness ช่วงต้น

### B: เชื่อม backend และตรวจ boundaries รวม

- [ ] B1 รวม seam ที่ implemented + seam-checked จาก PR-01–03 บนเครื่องเดโม เชื่อม upload→questions→reviews→images ด้วย state จริง ไม่มี fixture injection/fallback และตั้ง run instructions ตาม URL/one-worker เดิม
- [ ] B2 ส่ง ZIP T4/T5 และ mutation provenance ให้ C ก่อน gate 2:30 ร่วมตรวจว่า importer อ่านแหล่งที่ถูกแก้จริงและภาพหายยังมี source identity ไม่แก้ ZIP ต้นฉบับ
- [ ] B3 รัน HTTP/security/state checks รวม body/path/client fields, duplicate/cache/retry, exception/cancellation และ stale เมื่อเปลี่ยน ZIP/options/context/answers เก็บผลก่อน/หลังแก้ที่ทำซ้ำได้
- [ ] B4 ร่วม A restart ตรวจว่าเคส/ภาพ/คำตอบที่ commit แล้วกลับมาครบ งานค้าง Retry ได้ และเคสเก่าใช้ไม่ได้เมื่อเริ่ม ZIP ใหม่ และคำสั่งรันที่บันทึกใช้ได้จริงบนเครื่องเดโม

ส่งให้ A: integrated commit, run commands และผล boundary/state; ส่งให้ C: mutation ZIP/actual input ก่อนเปิด PR ส่วน wiring ตรวจ V04-B ส่วน restart เก็บผลภายหลังใน verification เดียวกัน ไม่อ้างว่า gate รวมผ่านจาก HTTP stub

### C: ตรวจคุณภาพจริงและวัดการเรียกโมเดล

- [ ] C1 ใช้ชุด T1–T5 ใน integration plan เป็นรายการเดียว รันโมเดลจริงเมื่อเส้นทางพร้อม เก็บ prompt/version, actual model และ input source/image inventory แยก expected checks ออกจาก model input
- [ ] C2 อ่านผลเทียบต้นฉบับ/ความต้องการทุกกรณี โดยเฉพาะ discovery ที่ไม่ใบ้, requirement ที่เปลี่ยน, ลำดับ SKU ใหม่, report text ที่ถูกลบ และภาพหาย แยกสิ่งที่ validator ตรวจได้ออกจากความหมายที่คนต้องอ่าน
- [ ] C3 วัด billed usage/reasoning, latency, attempts/incomplete และต้นทุนตาม usage ที่ได้จริง ทำ T1–T5 อย่างละรอบก่อน; repeat T1–T3 เมื่อผ่านและเวลาพอ หากยังผิดตรวจ prompt/input ก่อนเทียบ Sol เฉพาะขั้นที่มีปัญหา
- [ ] C4 ส่ง defect พร้อม output/source/input refs ให้ B/A แก้ตามเจ้าของ ตรวจกรณีที่ได้รับผลใหม่ และสรุปจำนวน runs/pass/fail/pending โดยไม่ใช้ตัวอย่างน้อยนี้อ้าง accuracy หรือความเหนือกว่า ChatGPT

ส่งให้ A/B: ผล semantic/usage และรายการข้อค้าง ก่อนเปิด PR ส่วน evaluation ตรวจ V04-C ตามกรณีที่รันจริง ถ้า API/โมเดลหรือ review ใช้ไม่ได้ให้ pending ไม่ใช้รายงานตัวอย่างแทน gate

## ลำดับและจุดส่งต่อ

เริ่มทำ deterministic HTTP/UI harness, กรณีตรวจ และ integration wiring ได้ระหว่าง PR-01–03 ทำงาน ไม่ต้องรอให้สามใบนั้นปิด แต่การตรวจ ZIP จริงครบเส้นทางมี gate ว่า 01–03 implemented + seam-checked แล้ว เป้าต่อจริงครั้งแรก 2:30 ตามแผนเดิม B ส่ง ZIP T4/T5 ให้พร้อมก่อน gate นี้ ไม่ประกาศ gate ผ่านจากการเตรียม harness หรือ fixture

2:30–4:15 ตรวจจริง/แก้และตรึง state; 4:15–5:15 review/fixes; 5:15–6:00 restart/rehearsal ยึดตารางเดิม ไม่เพิ่มจำนวนเคสเพื่อให้ครบเอกสาร ไม่ตัด requirements ที่ตกลงเพื่ออ้างว่ารุ่นหลักเสร็จ

PR-01–03 ส่งต่อเป็น implemented + seam-checked → integrated-review-pending ได้ แต่ยังไม่ complete จน checks/review ครอบคลุม final state ของใบนั้น คู่ integrated review ไม่ใช่ข้อยกเว้นให้ใบอื่นข้าม review นับ full rounds ต่อทุก ticket ที่ได้รับผลตามเพดานเดิม ไม่ย้ายข้อผิดไป PR-04 เพื่อ reset budget

## ตรวจและสิ่งที่ไม่รวม

บันทึกใน [verification](verification.md) ไม่รวม deploy, seller replies, live scrape/search, login/history หรือการเพิ่ม prototype ทุก defect ที่แก้ต้องตรวจตามผลกระทบจริง ไม่เพิ่ม review loops ไม่จำกัดเพื่อให้ผ่าน

