# PR-01: นำเข้า ZIP และยืนยันสินค้าที่จะซื้อ

**What to build:** ผู้ซื้อส่ง ZIP ของ TTP Cargo แล้วเห็นชื่อ/ตัวเลือกเป็นอังกฤษคู่ต้นฉบับจีน รูปตรงตัวเลือกและค่าร่วม จากนั้นยืนยัน 1–3 ตัวเลือกกับบริบทได้ โดยไม่รันหน้าเว็บหรือเลือกแทนผู้ซื้อ

**Blocked by:** None (เริ่มได้ในวันแข่งเมื่อ breakdown ยอมรับแล้ว)

**Status:** Phase 1 implemented on the review branch. See [verification](verification.md) for tested scope and remaining gates.

**ผู้ประสานหลัก:** B; A ทำหน้าเว็บ, B รับหลักฐาน/HTTP/state, C schema/translation

**Authority:** [spec](../../docs/spec.md) stories 1–3, 8, 13–14; [intake](../../docs/contracts/intake-contract.md); [report: ListingSnapshot/Source](../../docs/contracts/report-contract.md); [AI ขั้นแปล](../../docs/ai-execution-plan.md); [integration](../../docs/integration-plan.md)

## Acceptance criteria

- [ ] ทั้ง curated ZIP และ raw browser save ใน starter แสดง 12 ตัวเลือก ภาพตรง SKU และค่าร่วม 720ml จากผู้ขาย ไม่ hardcode จำนวน/item ID ไม่ใช้ค่า checked ของเว็บเป็นการเลือกของผู้ซื้อ
- [ ] แสดง Product title/ชื่อ SKU และหลักฐานที่แปล พร้อมต้นฉบับ ชื่อ SKU ในหน้าเลือกใช้คำแปล source เดียวกับแผงหลักฐาน เปลี่ยนตัวเลือกแล้วรูปเปลี่ยนตาม mapping ต้นทาง
- [ ] ยืนยันได้ 1–3 รหัสที่มีจริง เก็บลำดับและบริบทที่ว่างได้ ไม่สร้าง requirement จากค่าร่วม ผู้ซื้อยังไม่ยืนยันต้องไปเตรียมคำถามไม่ได้
- [ ] รูป missing/unreadable/ไม่มีรูปเฉพาะแยกกันตาม contract ไม่โหลดรูป remote หรือยืมของ SKU อื่น เก็บ source identity เมื่อทราบ mapping และบอกข้อจำกัด thumbnail
- [ ] ตรวจ upload body/archive/path/type/resource limits ตาม intake/integration ไม่รัน/serve raw HTML ไม่รับ path จาก client ภาพที่เปิดใน UI มาจาก bytes ที่ตรวจแล้วและ lookup ด้วยรหัส
- [ ] ขั้นแปลใช้ต้นฉบับจริง จำกัด attempts/deadline ตาม AI plan ปัญหาชั่วคราวลดเป็น null+limitation ได้ ส่วน config/สิทธิ์/โควตาหยุด import ไม่มี sample fallback
- [ ] อัปโหลดใหม่ทำให้ selection/plan/report เก่าใช้ไม่ได้ ผล import ช้าที่มาทีหลังไม่ publish ทับชุดใหม่ และ server ไม่รับ snapshot/source ที่ client แก้กลับมา
- [ ] ส่งต่อ snapshot ที่ backend เก็บจริงพร้อม selected IDs/context ให้ PR-02 ได้ ตรวจ PR-01 แยกผ่าน upload→selection จริง ไม่อ้างว่ารายงานครบแล้ว

## งานของแต่ละคนก่อนเปิด PR

แต่ละคนเปิด PR เฉพาะส่วนของตนตาม [วิธีส่งงาน](../../docs/team-plan.md#วิธีส่ง-pr-ของแต่ละคน) เกณฑ์ด้านล่างเป็นเกณฑ์ส่งส่วนงาน ไม่ใช่การปิด ticket รวม

### A: หน้ารับไฟล์และเลือกสินค้า

- [ ] A1 สร้างหน้า upload และสถานะ loading/failed/ready จาก IntakeResult แสดงชื่อสินค้าและ SKU อังกฤษคู่จีน รวม fallback เมื่อคำแปลเป็น null โดยไม่แปลหรือวิเคราะห์เพิ่มใน UI
- [ ] A2 วางชื่อ SKU ภาพ และค่าร่วมจากผู้ขายไว้ในส่วนเลือกสินค้า พับรายละเอียดอื่นของผู้ขายไว้ให้เปิดดูได้ แสดงรูป 150×150 เป็น thumbnail โดยไม่ขยายเกินขนาดต้นฉบับ ผูกภาพกับ source ID แยก missing/unreadable/ไม่มีรูปเฉพาะ ไม่ใช้ภาพอีก SKU แทน และไม่ใช้ค่า checked จาก saved page เป็น selection
- [ ] A3 ให้เลือก 1–3 ตัวเลือก เก็บลำดับและ buyer_context ที่ว่างได้ ตรวจความยาวตาม integration plan ให้ยืนยันก่อนส่งต่อ `{operation_id, snapshot_id, previous_case, selected_option_ids, buyer_context}` ไม่ส่ง snapshot/source/path กลับให้ backend
- [ ] A4 ล้าง selection/plan/report เมื่อเริ่ม upload ใหม่และป้องกัน response เก่าทับหน้าใหม่ สร้าง linked fixture สำหรับ harness ที่ `frontend/src/fixtures/integration-flow.json` ให้ C ตรวจ schema ไม่เพิ่มปุ่ม/endpoint ฉีด fixture ในแอป

ส่งให้ B/C: หน้าเว็บและ fixture ที่รหัสเชื่อมกัน พร้อม HTTP payload ที่ใช้จริง ภายใน 0:30 ส่ง fixture ก่อนเปิด PR ตรวจ V01-A รวมการดูหน้าจริงตาม AGENTS และเขียน smoke steps ให้เพื่อนลอง upload → เลือก SKU → ยืนยัน การต่อ ZIP จริงร่วมกับ B/C ต้องเสร็จก่อน gate import/selection 1:30

### B: รับ ZIP หลักฐาน ภาพ และ state

- [ ] B1 เตรียม Git baseline/เครื่องเดโมและ `POST /api/imports` ตรวจ body/archive/path/type และเพดานงาน parser ตาม intake contract ก่อนแปลหรือ publish จำกัด parse ครั้งละหนึ่งงาน อ่าน HTML/JSON แบบข้อมูลเท่านั้น ไม่รัน/serve raw HTML ไม่ fetch remote และไม่ส่ง debug/tracking ให้ C ลงคำสั่งเปิดแอปจาก repo root และ URL เดโมใน README เมื่อเปิดได้
- [ ] B2 ตรวจ curated และ raw ZIP ตั้งแต่รอบ importer แรกด้วย stub translator ดึง product title, parent/child options, ความสัมพันธ์ SKU และ attributes ตาม intake contract ไม่ hardcode 12 options/720ml/item ID ผูก text/image sources และเก็บ source identity ของรูปที่หาย พร้อม limitation ของ thumbnails ส่งผลเทียบ inventory ให้ A/C
- [ ] B3 เก็บ snapshot และ encoded image bytes ที่ตรวจแล้วใน SQLite ตาม integration plan ทำ image GET ด้วย snapshot/source IDs, MIME/nosniff และ 404 ที่ถูกต้อง ไม่รับ client path ไม่เก็บ raw ZIP/HTML เป็น history
- [ ] B4 ส่งข้อความ/รหัสเดิมให้ C แปล ตรวจผลแล้ว publish snapshot ของ import ปัจจุบันเท่านั้น ใช้ generation/finally ป้องกันงานเก่าทับและปล่อย references เมื่อเลิกใช้ ส่ง snapshot จริงให้ A
- [ ] B5 เตรียมสำเนา ZIP T4/T5 พร้อมรายการแก้โดยไม่เปลี่ยนต้นฉบับ ส่งให้ C ภายใน 2:30 ตรวจทุกแหล่งที่ parser รองรับเพื่อไม่เหลือ report text ซ้ำใน T4

ส่งให้ A: import/image HTTP และ snapshot ส่วนที่ส่งให้ C คือ ข้อความต้นฉบับกับ source/option IDs ก่อนเปิด PR ตรวจ V01-B ชุด T4/T5 ส่งแยกตามเวลาได้ ไม่หน่วง PR parser ที่ตรวจแล้วเพื่อรอชุดทดสอบท้ายรอบ

### C: Schema และขั้นแปล

- [ ] C1 ส่ง Pydantic schema ของ snapshot/source/intake และชื่อฟังก์ชันที่ B เรียกภายใน 0:30 พร้อมตรวจ linked fixture ของ A เจ้าของ schema คือ C; B/A แจ้งก่อนเปลี่ยน field ที่ใช้ร่วมกัน
- [ ] C2 ตรวจ API/model access แล้วทำ prompt v1 และ call แปล Product title/ชื่อ SKU/ข้อความที่อนุญาต เก็บ prompt ที่ใช้จริง แยกจาก expected examples ใช้ schema และ source IDs โดยไม่แก้ต้นฉบับ/สร้าง product facts
- [ ] C3 ตรวจรหัส/จำนวนรายการแปลให้ตรง input คืน text_en/name_en จากคำแปล source เดียวกัน ส่งผลให้ B ประกอบ snapshot ไม่แก้ state/สร้าง HTTP response เอง
- [ ] C4 ทำ attempts/deadline และ failure mapping ตาม AI/intake plan: ชั่วคราวตามเงื่อนไขเป็น null+limitation; config/auth/quota เป็น intake_failed ปิด SDK retry ซ้อน ไม่ใช้ sample fallback และเก็บ key ที่ backend เท่านั้น

ส่งให้ B: schema, signature และผลแปล/ผลล้มเหลวที่ทดสอบได้ ส่วนที่ส่งให้ A คือ fixture validation ก่อนเปิด PR ตรวจ V01-C และระบุผล API จริงแยกจาก stub หาก API ยังใช้ไม่ได้ให้ส่ง draft/pending ไม่อ้างว่าการแปลจริงผ่าน

## ลำดับและจุดส่งต่อในวันแข่ง

ช่วงแรก B จัด baseline Git/เครื่องเดโมและ upload/state, C ส่ง schemas/ตรวจ API access แล้วต่อ translation, A ตั้ง UI และ linked fixture ตาม integration plan เป้าคือหน้าเลือกใช้ข้อมูลจริงภายใน 1:30 งานคำถาม/รายงานเตรียมขนานได้ตาม contracts

B เตรียม ZIP สำเนา T4/T5 ส่งภายใน 2:30 ในงานนี้ ก่อน gate ZIP จริงของ PR-04 เก็บต้นฉบับและคำอธิบายสิ่งที่เปลี่ยน ห้ามส่งข้อมูลติดตาม/debug ทั้งหน้าไปโมเดล

## ตรวจและสิ่งที่ไม่รวม

ใช้ [verification](verification.md) ตรวจ seam ของ import และหน้าเลือก ไม่เพิ่มเว็บอื่น, live fetch, detail-image selection, persistence หรือ deploy รายละเอียด implementation ของงานนี้ให้จัดใน fresh Build context วันแข่งก่อนลงมือ


## Review gate ก่อนปิดงาน

ส่งต่อ seam ที่ implemented + seam-checked ให้รวมได้ก่อน แต่ ticket ยังเป็น integrated-review-pending จนคู่ Standards/Spec บน fixed integrated state ที่ครอบคลุม PR-01–04 และ required checks ครอบคลุม final state ของใบนั้น ตาม [วิธี shared integration review](../../docs/team-plan.md#เหตุผลของ-dependencies) ห้ามปิดใบนี้เพียงเพราะ fixture/seam checks ผ่าน หากรีวิวหรือข้อสำคัญยังค้างให้ pending ไม่ข้าม review
