# PR-01 verification

Status: not run; no app implementation

- Branch/base และ tested SHA: pending
- ส่งต่อได้แล้ว: ยังไม่มีผลจากโค้ดจริง เริ่มงานจาก interface/fixture ได้
- Blocker และเจ้าของ: pending ให้ผู้ประสาน ticket อัปเดตเมื่อพบ
- รอตรวจเพื่อส่งต่อ: PR-02 รับ snapshot และ selected IDs ที่ผ่าน V01 แล้ว ชุด T4/T5 ส่งตามเวลาแยกได้
- Gate รวม: pending ตามเกณฑ์ด้านล่าง
Authority: [plan](plan.md), [ชุดเกณฑ์รับหลักฐาน](../../docs/contracts/intake-contract.md#กรณีที่ต้องยืนยันตอน-build)

## ตรวจส่วนของตนก่อนเปิด PR

ทุกรายการยัง not run บันทึกผลแยก A/B/C ตาม [รูปแบบส่งงาน](../../docs/team-plan.md#วิธีส่ง-pr-ของแต่ละคน) ใช้คำสั่ง app ที่สร้างจริง

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

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว

## ผลจริงของ B

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว

## ผลจริงของ C

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว

## Review รวมก่อนปิด ticket

Required review: pending implementation/integrated state คู่ Standards/Spec ต้องตรวจ acceptance ของใบนั้นอย่างชัดเจนก่อน complete บันทึก base/target/state, reviewers, findings/dispositions/final checks และ full-round ledger ต่อ ticket การส่งต่อเพื่อรวมยังไม่นับว่าปิดงาน
