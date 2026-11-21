# Purchase review — competition starter

ชุดเริ่มงานสำหรับทีม 3 คน: ผู้ซื้อส่ง ZIP หน้าสินค้าจีน เลือก 1–3 SKU ตอบคำถามที่ปรับตามคำตอบสูงสุด 3 รอบ รอบละ 5 ข้อ หรือไปดูรายงานได้เลย แล้วได้รายงานอังกฤษพร้อมหลักฐานและข้อความถามผู้ขายที่คัดลอกได้ กู้เคสล่าสุดพร้อมหลักฐานหลัง restart ด้วย SQLite

Current work: ใช้ spec v0.16 สำหรับวันแข่ง รวมคำถามหลายรอบ รายงานตามคำตอบ และการกู้เคส SQLite ชุดนี้มีเอกสารและข้อมูลทดสอบสำหรับเริ่ม repo ใหม่ งานแอปยังไม่เริ่ม

## เริ่มในโฟลเดอร์งานที่เลือกแล้ว

ใช้โฟลเดอร์ที่มี README นี้เป็น root ของโปรเจกต์ ให้ B บันทึก baseline commit ตามข้อตกลงทีมก่อนแยก branch งาน

## 30 นาทีแรก

- **B** เตรียม repo และเครื่องเดโม จากนั้นเป็นเจ้าของคำสั่งเปิด frontend/backend จาก repo root และระบุ URL เดโมให้ตรงกับ Origin ที่ backend รับ คือ `http://127.0.0.1:5173` ลง README ของ repo แข่งทันทีที่เปิดแอปได้ ใช้ backend หนึ่ง worker แล้วลองเปิดจาก process ใหม่ร่วมกับ A
- **A** เริ่ม UI จาก linked fixture และเขียน smoke steps ว่าต้องเลือกไฟล์ไหน กดอะไร และคาดว่าจะเห็นอะไร เริ่มจาก upload → เลือก SKU → ยืนยัน
- **C** ส่ง schema/signatures และตรวจสิทธิ์ API ตามแผน ระบุในคำสั่งตรวจให้ชัดว่าเป็น **deterministic** (fixture/stub ไม่มี API call) หรือ **live** (ใช้เครดิต API) รันชุด deterministic ได้ระหว่างรอ key

## อ่านแค่นี้ก่อน

1. [brief](docs/brief.md): ปัญหา เป้าหมาย และขอบเขต
2. [ทีมและใบงาน](docs/team-plan.md): A/B/C และงาน 4 ใบ จากนั้นเปิด plan ของงานที่รับ
3. [ตาราง 6 ชั่วโมง](docs/integration-plan.md#ตาราง-6-ชั่วโมง): จุดส่งต่อและจุดตรวจ

เมื่อลงมือให้อ่าน [spec](docs/spec.md) ส่วน AI coding agents เริ่มจาก [AGENTS.md](AGENTS.md) เรื่องที่ยังต้องกำหนดคือสมาชิก A/B/C กับเครื่องเดโม

| รับบท | อ่านรายละเอียดหลัก | เริ่มทำช่วงแรก |
| --- | --- | --- |
| A: หน้าเว็บ/เดโม | [report](docs/contracts/report-contract.md), [questions](docs/contracts/question-contract.md) | โครงหน้าและ linked fixture ที่ใช้ IDs/prompt ชุดเดียวกัน |
| B: ZIP/backend/integration | [intake](docs/contracts/intake-contract.md), [integration](docs/integration-plan.md) | ZIP จริง/state/routes/image serving และ baseline repo |
| C: schemas/AI/คุณภาพ | [AI plan](docs/ai-execution-plan.md), [report](docs/contracts/report-contract.md), [questions](docs/contracts/question-contract.md) | schema/function names และตรวจ API access/structured output |

## รับงานและส่ง PR

แต่ละ ticket แยก checklist ของ A/B/C และเกณฑ์ V ของแต่ละคนไว้แล้ว เช่น A เริ่ม [งาน A ของ PR-01](plans/purchase-01-import-and-select/plan.md#งานของแต่ละคนก่อนเปิด-pr) แล้วตรวจ [V01-A](plans/purchase-01-import-and-select/verification.md#ตรวจส่วนของตนก่อนเปิด-pr) ก่อนส่งส่วนงาน ไม่ต้องรับทั้ง ticket

ใช้ [ตัวอย่างคำสั่งให้ coding agent](docs/team-plan.md#ตัวอย่างคำสั่งให้-coding-agent) โดยเปลี่ยน role/ticket/V ให้ตรงงาน อ่าน [วิธีส่ง PR และรูปแบบหลักฐาน](docs/team-plan.md#วิธีส่ง-pr-ของแต่ละคน) ก่อนเปิด PR การรวมส่วนงานเพื่อ integration ยังไม่ใช่การปิด ticket

## ของที่ต้องใช้มีอยู่ที่ไหน

- [samples/listing.zip](samples/listing.zip) และ [raw-save-redacted.zip](samples/raw-save/raw-save-redacted.zip): ZIP ของสินค้าเดียวกัน ใช้ตรวจ PR-01 ทั้งคู่ ไฟล์หลังเก็บโครง browser save และชื่อไฟล์แบบ Finder [วิธีใช้และข้อจำกัด](samples/README.md)
- [official-sources.json](samples/official-sources.json) และ [reference pack](docs/reference-pack.md): excerpt ทางการที่คัดไว้สำหรับตั้งคำถาม ไม่ใช่คำรับรองสินค้า
- [ตัวอย่าง report](docs/contracts/examples/report-contract-example.json) / [ตัวอย่างคำถาม](docs/contracts/examples/question-contract-examples.json): ตัวอย่างที่เขียนขึ้นสำหรับ frontend/tests ไม่ส่ง expected answers เข้า prompt
- [mock-reference.html](references/mock-reference.html): อ้างอิงหน้าจอและวิธีเปิดหลักฐาน ZIP/AI จำลองไว้และหน้าคำถามเป็นแบบเดิมหนึ่งรอบ ให้ยึดสเปก v0.16 สำหรับ flow จริง ไม่ย้าย fake analysis มาเป็น fallback
- [toolchain](docs/toolchain.md): candidate dependencies และสิ่งที่ยังไม่ได้ทดสอบบนเครื่องทีม
- [HANDOFF_CHECKS.md](HANDOFF_CHECKS.md): ผลตรวจชุดที่ส่งมอบและข้อจำกัด

## ยังไม่ได้พิสูจน์อะไร

ยังไม่ตรวจบัญชี/เครดิต/API ของทีม ไม่วัด latency/ต้นทุน/คุณภาพรายงานจริง และยังไม่มี application tests ที่ผ่าน T1–T5 การมีไฟล์ครบไม่แปลว่าแอปพร้อมเดโม เป้าคือเชื่อม ZIP ผ่านอย่างน้อยสองรอบถาม → รายงานจริงครั้งแรกภายใน 2:30 ชั่วโมง แล้วตรวจ/รีวิว/ซ้อมตามแผน

ราคาหรือชื่อโมเดลใน AI plan เป็น baseline ที่บันทึกวันที่ออกแบบ ให้ยืนยันการเข้าถึงและ config จริงก่อนใช้ ไม่สลับเป็น sample เมื่อ API ผิด
