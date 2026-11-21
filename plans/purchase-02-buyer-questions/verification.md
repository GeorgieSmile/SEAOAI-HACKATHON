# PR-02 verification

Status: not run; no app implementation

- Branch/base และ tested SHA: pending
- ส่งต่อได้แล้ว: ยังไม่มีผลจากโค้ดจริง เริ่มงานจาก interface/fixture ได้
- Blocker และเจ้าของ: pending ให้ผู้ประสาน ticket อัปเดตเมื่อพบ
- รอตรวจเพื่อส่งต่อ: PR-03 รับ ReviewInput จากรอบที่ออกจริงใน revision ปัจจุบัน ตรวจ zero/partial/skip, แขนง และการกู้เคส
- Gate รวม: pending ตามเกณฑ์ด้านล่าง
Authority: [plan](plan.md), [question contract](../../docs/contracts/question-contract.md)

## ตรวจส่วนของตนก่อนเปิด PR

ทุกรายการยัง not run บันทึกผลแยก A/B/C ตาม [รูปแบบส่งงาน](../../docs/team-plan.md#วิธีส่ง-pr-ของแต่ละคน) deterministic harness ตรวจโครงสร้าง/flow; ผล API จริงต้องบันทึกแยก

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

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว

## ผลจริงของ B

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว

## ผลจริงของ C

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว

## Review รวมก่อนปิด ticket

Required review: pending implementation/integrated state คู่ Standards/Spec ต้องตรวจ acceptance ของใบนั้นอย่างชัดเจนก่อน complete บันทึก base/target/state, reviewers, findings/dispositions/final checks และ full-round ledger ต่อ ticket การส่งต่อเพื่อรวมยังไม่นับว่าปิดงาน
