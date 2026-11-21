# PR-03: Purchase review พร้อมหลักฐานและคำถามผู้ขาย

**What to build:** จาก ReviewInput ที่ถูกต้อง ผู้ซื้อได้รับรายงานอังกฤษซึ่งแยกเรื่องที่ต้องตัดสินใจ/ถามผู้ขาย/ขอหลักฐาน เปิดต้นฉบับและคำแปลตรง SKU ได้ และแก้/คัดลอก draft ไปถามต่อเองได้

**Blocked by:** None (ใช้ Interface ที่ยอมรับแล้ว ตรวจแยกด้วย ReviewInput/issued plan fixture ฝั่ง test harness ได้)

**Status:** Phase 3 implemented for issue #3. See the [verification record](verification.md) for execution results and remaining integrated acceptance.

**ผู้ประสานหลัก:** C; C เนื้อหา/validation/draft, A รายงาน/หลักฐาน/copy UI, B ReviewInput/state/HTTP/image access

**Authority:** [spec](../../docs/spec.md) stories 4, 7–14; [report contract](../../docs/contracts/report-contract.md); [AI plan](../../docs/ai-execution-plan.md); [integration](../../docs/integration-plan.md)

## Acceptance criteria

- [ ] ใช้ buyer requirements จาก context/คำตอบจริง มี input ref และ quote คำตอบล่าสุดใน scope เดียวกันแทนเงื่อนไขเดิมได้ ข้อมูลที่ข้ามหรือขัดกันยังเปิดอยู่ ไม่กลายเป็น requirement ที่โมเดลเดา
- [ ] แสดง comparisons ครบตัวเลือกที่ยืนยัน พร้อม findings ที่เรียงตามผลต่อกรณีซื้อและเหตุผล ประเด็นร่วมแสดงครั้งเดียว ไม่บังคับ 3 findings และไม่ hardcode เรื่องลายให้มาก่อนเสมอ
- [ ] ครบ coverage 3 กลุ่ม รวมเมื่อไม่มี findings แยกต้นฉบับ/คำแปล/คำกล่าวอ้าง/ข้ออนุมาน/unknowns ไม่ตีความ reviewed เป็นผ่าน ไม่จัดคะแนนหรือรับรองซื้อได้/ปลอดภัย/นำเข้าได้
- [ ] ส่งเฉพาะข้อความที่เกี่ยวข้องและภาพตัวเลือกที่มีจริงสูงสุด 3 รูป inventory แยกแนบจริงกับ metadata ชัด ตรวจ citations/comparisons ตาม source/quote/scope/availability/ชุดที่ส่งจริง และคง limitation ว่าไม่ได้ตรวจรูปสินค้ารายละเอียด
- [ ] ไม่ใช้เมนู customization รับรอง SKU ไม่เดา finish จากรูป ไม่ใช้คำว่าตัวอักษรยืนยันว่าสกรีน ไม่เปลี่ยน “ผู้ขายระบุว่าไม่มีรายงาน” ให้เท่ากับไม่ปลอดภัย หรือ “ไม่พบ” ให้เท่ากับไม่มีจริง
- [ ] แสดง requested evidence และข้อจำกัดของหลักฐาน ให้ผู้ซื้อเปิดต้นฉบับ/ภาพที่ปลอดภัยได้ ไม่เพิ่มข้อกล่าวอ้างฝั่ง UI และไม่แสดงรูปผิดตัวเลือก
- [ ] Backend ประกอบ supplier draft ตาม findings/ชื่อ SKU/อักษร A/B/C จากลำดับเลือก ไม่คัดลอก context หรือ margin ทั้งก้อน ไม่มีคำถามให้ draft ว่างและซ่อนปุ่ม copy ข้อความที่แก้คัดลอกได้โดยไม่ย้อนแก้ findings
- [ ] ผล failed/refusal/incomplete/invalid_review ไม่แสดงเป็น ready ไม่ fallback เป็น sample รับ ReviewInput ของ revision ปัจจุบันจากรอบที่ยังใช้ได้ ผลเก่าไม่ทับข้อมูลใหม่และ retry ไม่ทำให้คำตอบหาย

## งานของแต่ละคนก่อนเปิด PR

ใช้ [วิธีส่งงาน](../../docs/team-plan.md#วิธีส่ง-pr-ของแต่ละคน) และ ReviewInput/issued plan ที่เชื่อมกันใน harness ระหว่างรอ HTTP จริง งานแต่ละคนต้องตรวจตาม V03 ของตนก่อนส่งรวม

### A: รายงาน หลักฐาน และ draft

- [ ] A1 แสดง Before you order ให้เห็น buyer answer → SKU evidence → next action ตามข้อมูลจาก Backend แล้วแสดง comparisons ของทุกตัวเลือกตามลำดับ findings/เหตุผลตามผลที่ backend คืน ประเด็นร่วมครั้งเดียว และ coverage 3 กลุ่มรวมกรณีไม่มี findings ไม่จัดอันดับหรือเพิ่มข้อสรุปฝั่ง UI
- [ ] A2 เปิด original/text_en/ภาพของ source ที่อ้าง แยก seller claim, buyer requirement, inference และ unknown แสดงข้อจำกัด/สิ่งที่ต้องยืนยัน ไม่ใช้สีหรือป้ายให้ reviewed แปลว่าปลอดภัย/ผ่าน
- [ ] A3 ใช้ supplier_draft จาก backend ให้แก้/คัดลอกได้โดยไม่ย้อนแก้ findings ซ่อน copy เมื่อ draft ว่าง แสดง copy failure ตามจริง ไม่ประกอบคำถามใหม่หรือส่งข้อความจริง
- [ ] A4 จัด loading/failed/retry และการแก้คำตอบโดยรักษา plan/คำตอบที่ใช้ได้ ล้าง report ทันทีเมื่อ input เปลี่ยน ตรวจ current case/revision/generation ก่อนแสดงผล และไม่แสดง sample เมื่อ API ล้มเหลว

ส่งให้ B/C: หน้า report/evidence/draft ที่ใช้ JSON ตาม contract ก่อนเปิด PR ตรวจ V03-A ด้วย report harness ที่มีทั้ง ready/empty/failed และทดสอบภาพตาม source IDs จริงเมื่อ B พร้อม

### B: Report HTTP และ state ที่ตรงกรณีซื้อ

- [ ] B1 ต่อ `POST /api/reviews` หลัง validation/submission assembly จาก PR-02 จับ immutable ReviewInput และ references ของ image bytes จาก snapshot/case เดียวก่อน await C ไม่อ่าน active snapshot ใหม่กลางงาน
- [ ] B2 ส่ง text/image inventory และ encoded bytes ให้ C ด้วย internal seam ไม่ส่ง path จาก client หรือ localhost image URL ให้ OpenAI fetch ส่วน browser เปิดภาพผ่าน image GET ของ PR-01
- [ ] B3 จัด binding/cache/reservation ของ review ตาม case/revision/operation_id ส่งซ้ำขณะ running เป็น 409, ready ข้อมูลเดิมใช้ผลเดิม, manual retry หลัง failed เป็น run ใหม่ด้วย payload เดิมและปล่อย reservation ใน finally
- [ ] B4 เมื่อ C คืนรายงานที่ผ่าน validation ให้บันทึกผ่าน `backend/state.py` ใน SQLite เดียวกับเคส ตรวจ generation, case_id, revision และ operation_id ใน transaction เดียวกับการบันทึก ถ้าไม่ตรงให้ทิ้งผลนั้นเพื่อไม่ให้ upload/options/context/answers ใหม่รับผลเก่า คืน domain/transport errors ตาม integration plan โดยไม่ส่ง traceback/key และไม่ล้างคำตอบที่ยัง retry ได้

ส่งให้ A: ReviewResult/HTTP ที่มี IDs ถูกต้อง; ส่งให้ C: immutable input/image map ก่อนเปิด PR ตรวจ V03-B ด้วย AI adapter ที่หน่วง/ล้มเหลวได้ รวมภาพข้าม snapshot และการเปลี่ยนข้อมูลระหว่างรอ

### C: Prompt รายงาน ตัวตรวจ และข้อความผู้ขาย

- [ ] C1 ทำ report schema/prompt v1 แยกจาก expected examples ใช้ input/คำตอบจริงและ DMSC excerpt ตาม scope จัด findings ตามกรณีซื้อพร้อมเหตุผล ไม่ hardcode เฉลย/ลำดับของกระติก
- [ ] C2 ส่งภาพเฉพาะ SKU ที่ยืนยันสูงสุด 3 รูปจาก bytes ที่ B ให้ ระบุ inventory ว่าอะไรแนบจริง/ไม่ได้แนบ รักษา missing/unreadable/not_reviewed และ limitation เรื่องรูปสินค้ารายละเอียด ไม่เดาวิธีทำลายหรือความทนจากภาพ
- [ ] C3 ตรวจ schema, IDs/quotes, requirement refs จาก buyer input, open preferences, coverage, comparisons และ source/option scope รวม unknowns ปฏิเสธ image citation นอกชุดที่ส่งจริง และไม่รับ missing/unreadable เป็นหลักฐานยืนยัน
- [ ] C4 ประกอบ supplier draft ด้วยโค้ดจากลำดับ findings/ชื่อ SKU และ A/B/C ตามลำดับเลือก ไม่คัดลอก context/private margin ทั้งก้อน ไม่มีคำถามให้ draft ว่าง; รักษา failure/refusal/incomplete เป็น failed ตาม AI plan

ส่งให้ B: ReviewResult ที่ตรวจแล้วและ supplier draft; ส่งให้ A: JSON ที่ใช้แสดงโดยไม่ต้องตีความใหม่ ก่อนเปิด PR ตรวจ V03-C แยก deterministic validator/draft checks ออกจากการอ่านผล API จริง ใช้ผลจริง T1–T5 ใน PR-04 เป็นหลักฐานร่วมได้แต่ห้ามอ้างว่าครบก่อนรัน

## ลำดับและจุดส่งต่อ

ใช้ schema/linked fixture ชุดเดียวกับ PR-01/02 เพื่อทำงานขนาน A ใช้ fixture ที่ติดป้ายใน harness จัดหน้าได้ C แยก schema/กฎทั่วไปออกจาก expected report และ contract prose ก่อนส่งโมเดล ห้ามส่ง golden answer เป็น prompt

B ส่ง immutable input/image bytes และ mapping ที่ตรง snapshot ให้ C ไม่ใช้ localhost URL ให้ OpenAI fetch C เป็นเจ้าของ supplier draft/validation ตามแผนเดิม งาน schema/3 ขั้น AI/ID scope มาก่อน quote/requirement/draft ตามช่วง 0:30–2:30 ไม่ย้ายงานตรวจสำคัญออกจากความรับผิดชอบใบนี้

## ตรวจและสิ่งที่ไม่รวม

ใช้ [verification](verification.md) ตรวจ seam และการแสดงผล PR-04 ต้องพิสูจน์กับ ZIP จริงต่อครบ ไม่เพิ่ม live search, detail-image selection, safety scores, auto model routing, ส่งข้อความจริง หรือ seller-reply loop


## Review gate ก่อนปิดงาน

ส่งต่อ seam ที่ implemented + seam-checked ให้รวมได้ก่อน แต่ ticket ยังเป็น integrated-review-pending จนคู่ Standards/Spec บน fixed integrated state ที่ครอบคลุม PR-01–04 และ required checks ครอบคลุม final state ของใบนั้น ตาม [วิธี shared integration review](../../docs/team-plan.md#เหตุผลของ-dependencies) ห้ามปิดใบนี้เพียงเพราะ fixture/seam checks ผ่าน หากรีวิวหรือข้อสำคัญยังค้างให้ pending ไม่ข้าม review
