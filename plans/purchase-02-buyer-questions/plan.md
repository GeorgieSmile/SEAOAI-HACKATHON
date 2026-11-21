# PR-02: ถามตามคำตอบหลายรอบและกู้เคสล่าสุด

**What to build:** แสดงคำถามอังกฤษพร้อมกันทั้งรอบ สูงสุด 3 รอบ รอบละ 5 ข้อ ใช้คำตอบกำหนดรอบถัดไป ผู้ซื้อข้ามหรือดูรายงานได้ และทำเคสเดิมต่อหลัง restart จาก SQLite

**Blocked by:** None (ใช้ Interface ที่ยอมรับแล้ว ตรวจแยกด้วย trusted snapshot fixture ใน test harness ได้)

**Status:** ready-for-agent; execution on competition day after selecting this Build task; implementation not started

**ผู้ประสานหลัก:** A ทำหน้าสนทนาและกู้ UI, B เก็บ SQLite/ออกคำถามผ่าน HTTP และประกอบ ReviewInput, C ทำ prompt/schema/scope สำหรับคำถามหลายรอบ

**Authority:** [spec](../../docs/spec.md) stories 3–6, 14; [question contract](../../docs/contracts/question-contract.md); [question examples](../../docs/contracts/examples/question-contract-examples.json); [report input](../../docs/contracts/report-contract.md); [integration](../../docs/integration-plan.md)

## Acceptance criteria

- [ ] ใช้ snapshot/ตัวเลือก/ลำดับ/context ที่ยืนยันรวมคำตอบสะสม ถามอังกฤษสูงสุด 3 รอบ รอบละไม่เกิน 5 ข้อ ถามข้อที่เป็นอิสระกันในรอบเดียว และไม่ถามเรื่องที่ชัดแล้วหรือข้ามไปแล้วโดยอัตโนมัติ
- [ ] ถามเฉพาะสิ่งที่ผู้ซื้อกำหนดได้ พร้อม why_ask และ scope ที่ถูก ไม่ถามให้เดาสินค้า ไม่บรรยายค่าจากภาพใน prompt/choices และไม่อ้าง source ที่ไม่ได้ส่งจริง
- [ ] ไม่มีคำตอบเชิง requirement เลือกไว้ล่วงหน้า คงคำตอบแต่ละข้อเมื่อเว้นข้ออื่น whitespace-only กลายเป็น null ที่ backend และไม่แก้ข้อความที่มีเนื้อหา
- [ ] ถ้าไม่มีคำถามหรือส่งครบเพดานให้ไปขอรายงาน ข้อมูลพอตั้งแต่ต้นใช้ answers: [] หากกด View purchase review ระหว่างรอบให้ใช้คำตอบที่กรอกไว้และไม่ถามต่อ
- [ ] รับทุก question_id ของรอบที่ออกจริงครั้งเดียว เติม question_text ฝั่ง Backend เก็บ whitespace/null ตาม contract และเมื่อแก้คำตอบให้ทิ้งรอบที่อยู่ถัดจากจุดที่แก้
- [ ] สร้าง ReviewInput ที่ตรง issued plan และ buyer input ทั้งกรณีตอบบางข้อ/ข้ามทั้งหมด คำตอบเป็นข้อมูลผู้ซื้อ ไม่เป็นหลักฐานสินค้า
- [ ] เพิ่ม revision เมื่อเปลี่ยนคำตอบ ไม่เพิ่มเมื่อส่งคำตอบชุดเดิม เปลี่ยน ZIP/options/order/context เริ่มเคสใหม่ ผลเดิมไม่ทับปัจจุบัน และ restart ต้องเปิดเคสล่าสุดพร้อมรูปและคำตอบที่บันทึกแล้ว
- [ ] validation/retry/cache/running duplicate ตาม integration plan ข้อมูลผิดหยุดก่อน call, invalid model output ไม่กลายเป็น questions: [] และ manual retry รักษาข้อมูลเดิม

## งานของแต่ละคนก่อนเปิด PR

ใช้ [วิธีส่งงาน](../../docs/team-plan.md#วิธีส่ง-pr-ของแต่ละคน) และ fixture/schema จาก PR-01 พัฒนาแยกได้โดยไม่รออีกส่วน complete แต่ห้ามอ้างว่าต่อ ZIP จริงแล้วหากตรวจเฉพาะ harness

### A: หน้าคำถามและ submission

- [ ] A1 ต่อ start/continue ตาม integration plan แสดงประวัติและคำถามทั้งรอบพร้อม reason/why_ask, single-select, free text และ Not sure / Skip โดยไม่เลือกคำตอบไว้ก่อน
- [ ] A2 เก็บร่างต่อ question_id ส่งคำตอบทั้งรอบตอน Continue หรือ View purchase review ส่ง null เมื่อว่าง/ข้าม รักษาคำตอบข้ออื่น และส่ง ID/revision ตาม contract โดยไม่ส่ง question_text
- [ ] A3 เชื่อม zero-question และถึงเพดานเข้ารายงานโดยไม่ออกข้อใหม่ ให้ View purchase review ใช้คำตอบในรอบเปิดหรือที่บันทึกแล้วเมื่อคำถามรอบถัดไปล้มเหลว
- [ ] A4 รองรับแก้รอบก่อน Retry และโหลดเคสหลัง restart จาก GET /api/case พร้อมกัน stale response เมื่อเปลี่ยน ZIP/options/order/context หรือ revision ไม่เรียก AI ขณะโหลดเคส

ส่งให้ B: prepare/submission payload จาก UI; ส่งให้ C: ตัวอย่างการแสดงคำถามตาม plan ก่อนเปิด PR ตรวจ V02-A โดยบังคับ 0/partial/skip ผ่าน harness ได้ ไม่รอให้โมเดลบังเอิญสร้างทุก path

### B: Issued plan, binding และ ReviewInput

- [ ] B1 ทำ start/continue และ GET /api/case ตรวจ snapshot/selection/order/context/current revision และ extra fields ก่อนเรียก C ไม่รับ snapshot/sources/image_file จาก client
- [ ] B2 เก็บเคส/คำถาม/คำตอบและงานล่าสุดใน SQLite จำกัด 3 รอบ เขียน transaction ก่อน AI และจองงานตาม case/revision/operation_id ป้องกัน duplicate และผลเก่า พร้อม finally
- [ ] B3 รับ continue/review submission ตรวจ issued round และ question IDs ก่อน call เก็บคำตอบตามลำดับ normalize whitespace-only เป็น null ตัดรอบหลังเมื่อมีการแก้จริง ส่ง ReviewInput จากรอบที่ยังมีผลให้ PR-03
- [ ] B4 กู้เคสและงาน Retry จาก SQLite ให้คำตอบ/รูปตรง snapshot งานที่ค้างหลัง restart กด Retry ได้โดยไม่เพิ่มรอบหรือทำคำตอบซ้ำ ใช้ answers: [] เมื่อไม่มีคำถาม

ส่งให้ C: QuestionInput ที่เชื่อถือได้และ ReviewInput หลังตรวจ; ส่งให้ A: HTTP/results และ error mapping ก่อนเปิด PR ตรวจ V02-B โดยใช้ test adapter ของ C ที่ seam ได้ ไม่สร้าง endpoint ฉีด trusted state

### C: สร้างและตรวจคำถาม

- [ ] C1 ส่ง QuestionInput/ผลถาม/issued round และ submission/ReviewInput schemas/signatures ภายใน 0:30 ให้ A/B ใช้ชุดเดียว รวม ID/revision
- [ ] C2 ทำ prompt จากข้อความของ SKU บริบท คำถาม/คำตอบที่บันทึกและ curated sources ไม่มีภาพในขั้นนี้ คืนเหตุผลและ 0–5 ข้อที่ถามร่วมกันได้ ไม่ถามซ้ำหรือถามให้เดาข้อเท็จจริงสินค้า
- [ ] C3 ตรวจจำนวน ความซ้ำและ scope ของตัวเลือก/ข้อความที่ available และส่งจริง รวมข้อกล่าวอ้างใน prompt/why_ask/choices ปฏิเสธ invalid_questions เมื่อเกิน 5 หรืออ้างนอกแหล่งที่ใช้ โดยไม่ตัดทิ้งหรือคืนผลจบ
- [ ] C4 ใช้ attempts/deadline/error mapping ตาม AI plan เก็บ prompt/input source inventory และผลตรวจที่ไม่เปิดเผย private notes ให้ B เก็บ plan/เติมรหัสตาม input ไม่ให้โมเดลสลับ selection

ส่งให้ B: QuestionResult พร้อม validation และ failure; ส่งให้ A: ตัวอย่าง plan สำหรับ harness ก่อนเปิด PR ตรวจ V02-C ผล API จริงใช้บันทึกร่วมกับ T1–T3 ใน PR-04 ได้ ไม่บังคับเพิ่มชุด benchmark ก่อนรวมส่วนงาน

## ลำดับและจุดส่งต่อ

C ส่ง schemas และชื่อฟังก์ชันภายใน 0:30 A ใช้ linked-skip และ multi-round fixture ใน harness ให้ C ตรวจ B ประกอบ QuestionInput/ReviewInput จาก revision ที่ยังใช้ได้

PR-02 เริ่มทำได้โดยไม่รอ importer เสร็จ แต่ trusted state/fixtures ใช้เฉพาะ harness การทดสอบ ไม่มี endpoint ฉีดข้อมูลหรือ sample fallback ในแอป การต่อจาก ZIP จริงต้องผ่าน PR-04 ไม่ใช้ผล fixture ประกาศว่าทั้งเส้นทางเสร็จ

## ตรวจและสิ่งที่ไม่รวม

ใช้ [verification](verification.md) ตรวจ adaptive rounds/0/partial/skip/invalid/stale และ SQLite recovery ขอบเขตนอกงานคือ free-form chat การรับคำตอบผู้ขาย และการเลือกโมเดลใน UI งานรายงานอยู่ PR-03 การยืนยัน end-to-end จริงอยู่ PR-04


## Review gate ก่อนปิดงาน

ส่งต่อ seam ที่ implemented + seam-checked ให้รวมได้ก่อน แต่ ticket ยังเป็น integrated-review-pending จนคู่ Standards/Spec บน fixed integrated state ที่ครอบคลุม PR-01–04 และ required checks ครอบคลุม final state ของใบนั้น ตาม [วิธี shared integration review](../../docs/team-plan.md#เหตุผลของ-dependencies) ห้ามปิดใบนี้เพียงเพราะ fixture/seam checks ผ่าน หากรีวิวหรือข้อสำคัญยังค้างให้ pending ไม่ข้าม review
