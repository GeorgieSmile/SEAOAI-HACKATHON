# ใบงานรุ่นแข่งและการแบ่ง A/B/C

Status: accepted breakdown; ready-for-agent on competition day; Build not started
Version: 0.4
วันที่: 19 พ.ย. 2569
Authority: [spec ที่ยอมรับแล้ว](spec.md), [บันทึกการยอมรับ](acceptance.md)

ยอมรับ 4 tickets ตามเส้นทางผู้ใช้ ทุกใบครอบคลุมข้อมูล/backend/หน้าเว็บและการตรวจของช่วงนั้น ไม่ใช้ “ทำ frontend ทั้งหมด” หรือ “ทำ AI ทั้งหมด” เป็น ticket เจ้าของ A/B/C ยังคงดูแลส่วนเดิมในทุกใบตาม [integration plan](integration-plan.md#ความรับผิดชอบและจุดส่งต่อ)

| ID / ใบงาน | ผู้ประสานหลัก | Blocked by | ผลที่ส่งมอบ |
| --- | --- | --- | --- |
| [PR-01 นำเข้าและเลือกสินค้า](../plans/purchase-01-import-and-select/plan.md) | B | ไม่มี | ZIP จริงกลายเป็นหน้าสินค้าอังกฤษพร้อมหลักฐานและภาพตรง SKU ให้ยืนยัน 1–3 ตัวเลือก/บริบท |
| [PR-02 สนทนาตามคำตอบหลายรอบ](../plans/purchase-02-buyer-questions/plan.md) | A | ไม่มี | จาก snapshot/selection/context ที่มีอยู่ เป็นคำถามสูงสุด 3 รอบ รอบละ 5 ข้อ กู้เคสจาก SQLite ได้ และสร้าง ReviewInput จาก revision ปัจจุบัน |
| [PR-03 รายงานพร้อมหลักฐาน](../plans/purchase-03-evidence-review/plan.md) | C | ไม่มี | จาก ReviewInput ที่ถูกต้อง เป็นรายงานอังกฤษเปิดหลักฐานได้ พร้อม draft ที่แก้/คัดลอกได้ |
| [PR-04 รวมเส้นทางและพร้อมเดโม](../plans/purchase-04-integrate-and-verify/plan.md) | A | ไม่มี hard start blocker; gate รวมตามด้านล่าง | เริ่มทำ harness/กรณีตรวจและ wiring ได้ระหว่าง 3 ใบแรก ส่วน ZIP จริงต้องผ่านทั้งเส้นทางและ T1–T5 มี code reviews และ restart/rehearsal |

## เหตุผลของ dependencies

PR-01–03 เริ่มพัฒนาขนานกันในวันแข่งได้เพราะ Interface ตกลงแล้ว แต่ละใบตรวจ seam ของตนด้วย linked fixture ใน trusted test harness ไม่รอ implementation อีกใบก่อนเขียนงานของตน C ส่ง schema/function names ภายใน 0:30 และ A ส่ง fixture ที่รหัส/prompt ต่อกันตามแผนเดิม

PR-04 ไม่มี hard start blocker เช่นกัน เพราะมีงานจริงที่เริ่มเองได้: deterministic HTTP/UI harness, test cases/expected checks และ integration wiring ระหว่างที่แต่ละ seam ทยอยส่งมา ไม่เพิ่มคนที่ 4 A เป็นผู้ประสานงานนี้พร้อมทำ frontend ตามตารางเดิม

แยก gate ออกจากการปิด ticket:

1. ส่งต่อเพื่อรวมได้เมื่อแต่ละส่วน implemented + seam-checked ยังไม่ใช่ complete เป้าคือสามส่วนต่อจริง ผ่านอย่างน้อยสองรอบและได้รายงานภายใน 2:30
2. จากนั้นเป็น integrated-review-pending ตรวจ T1–T5 จริงและเก็บหลักฐานจนตรึง integrated state ที่ 4:15
3. คู่ fresh Standards/Spec เดียวที่ A คุมต้องตรวจ fixed integrated state โดย briefs/results/dispositions/ledger อ้าง acceptance และขอบเขตทั้ง PR-01–04 ไม่ใช่รีวิวเฉพาะ PR-04 แล้วอนุมานว่าใบอื่นผ่าน
4. ปิด ticket ได้เมื่อ checks และ review ครอบคลุม final state/เกณฑ์ของใบนั้นแล้วเท่านั้น ในทางปฏิบัติจึงวางให้ทั้ง 4 ใบปิดหลังช่วง review รวม 4:15–5:15 แต่ละใบตรวจแยกก่อนหน้าได้เฉพาะ seam ยังไม่ใช่ผลครบทั้งแอป ข้อใหญ่หรือแก้เกี่ยวพันต้อง full re-review ภายใต้เพดานเดิมของทุกใบที่ได้รับผล ข้อเล็กใช้ focused fresh verification ไม่เริ่มนับรอบใหม่เพราะย้าย defect ไปอีก ticket

นี่เป็นการชี้แจงวิธี shared integration review, no early ticket closure ให้ตรง Build workflow ไม่ยกเว้น required reviews และไม่เพิ่มคู่รีวิวก่อน 2:30 จากตารางเดิม สถานะเหล่านี้ใช้ติดตามงาน ไม่เพิ่ม fields หรือ state ในแอป หาก reviewer/check ใช้ไม่ได้หรือ defect สำคัญยังค้าง ต้องบอก pending ไม่ปิดงานเพื่อให้ทันเดโม

Fixture ใช้เฉพาะ harness ไม่เพิ่ม endpoint/ปุ่มฉีด trusted state และไม่ fallback เป็น sample เมื่อ API ผิด การใช้ fixture ตรวจแยกไม่ทำให้ gate ZIP จริงผ่าน ไม่มี hard start dependency จึงไม่ได้แปลว่าไม่มี integration/acceptance gate

## ใครทำอะไร

| คน | งานที่รับตลอด PR-01–03 | จุดส่งต่อสำคัญ | งานท้ายรอบ |
| --- | --- | --- | --- |
| A | หน้ารับ ZIP/เลือกสินค้า สนทนาหลายรอบ/กู้เคส รายงาน/หลักฐาน copy/error/stale guard และ linked fixture | fixture เชื่อม source/case/issued prompt จริงภายใน 0:30 และต่อ HTTP ตามรหัส ไม่เพิ่ม reasoning เอง | ประสาน PR-04, code-review controller และ restart/rehearsal |
| B | archive/parser/ภาพที่ตรวจแล้ว state/coordinator HTTP และ integration wiring | snapshot และรูป; SQLite/revision/retry; ZIP T4/T5 ภายใน 2:30 | ตรวจ upload/security/state/races และรวม commit บนเครื่องเดโม |
| C | Pydantic schemas, แปล/คำถาม/รายงาน, quote/scope/reference checks, supplier draft | schema/function names ภายใน 0:30; 3 ขั้น AI + ID/scope ภายใน 1:30; quote/requirement/draft ภายใน 2:30 | อ่านผลจริง T1–T5, วัด usage/latency และแก้ AI defects |

ชื่อสมาชิกและเครื่องเดโมยังไม่ระบุ ใช้ A/B/C เตรียมงานได้ แต่ต้องกำหนดจริงก่อนเริ่มวันแข่ง ไม่เปลี่ยนเจ้าของ source/schema/หน้าเว็บจากการมีผู้ประสานหลักในแต่ละ ticket

## วิธีส่ง PR ของแต่ละคน

PR-01–04 เป็นรหัส ticket ไม่ใช่หมายเลข pull request จริง แต่ละ ticket มี PR ของ A/B/C แยกได้ เช่น `PR-01 / A: upload and selection UI` คนหนึ่งรับเฉพาะหัวข้อของตนใน plan และ V01–V04 ของตนใน verification ไม่ต้องตีความ acceptance รวมเพื่อแจกงานใหม่

1. อ่าน plan/verification ของ ticket กับ contract ที่ส่วนตนใช้ เริ่มจาก shared baseline เดียวกันแล้วทำ branch ของตน ตรวจโค้ดที่รวมล่าสุดก่อนต่อ seam ไม่แก้ไฟล์เจ้าของอื่นหรือ schema ที่ใช้ร่วมกันโดยไม่ประสาน
2. ทำ checklist ส่วนตนและรันเกณฑ์ V ของตน รวม type check/build/tests ที่มีจริงและเกี่ยวกับงาน หากอีก seam ยังไม่พร้อมใช้ linked fixture/test adapter ใน harness เท่านั้น ระบุชัดว่าอะไรตรวจด้วย stub และอะไรต่อจริงแล้ว
3. บันทึกผลใน verification ของ ticket ใต้หัวข้อ `ผลจริงของ A`, `ผลจริงของ B` หรือ `ผลจริงของ C` แก้เฉพาะส่วนตนเพื่อให้รวมไฟล์ง่าย ไม่ติ๊ก acceptance รวมแทนอีกคน และไม่เขียนว่า tests passed โดยไม่มีคำสั่ง/ผลจริง
4. เปิด PR ให้ตรวจรวมได้เมื่อส่วนที่อ้างว่าส่งมอบทำครบและ checks ของส่วนนั้นผ่าน ถ้ายังทำไม่ครบหรือ required check ใช้ไม่ได้ เปิด draft พร้อมข้อค้างได้ แต่ห้ามเรียกส่วนนั้น seam-checked การส่ง schema/signature ช่วง 0:30 แยกเป็น PR เล็กได้ ไม่ต้องรอ translation/report ทั้งชุด
5. ผู้ประสานของ ticket ตรวจ diff/interfaces และหลักฐานก่อนรวม B ประสานรวม backend/เครื่องเดโมตาม ownership เดิม อย่ารวมการเปลี่ยน schema แบบเงียบ ๆ หลังรวมให้เจ้าของที่ได้รับผลรัน checks ที่เกี่ยวข้องกับ seam นั้นซ้ำก่อนใช้สถานะ implemented + seam-checked
6. PR ส่วนงานที่ตรวจแยกผ่านรวมเพื่อ integration ได้ก่อน API quality/T1–T5/review/rehearsal ครบ โดยบันทึกสิ่งที่เหลือเป็น pending และห้ามใช้ fixtures ในแอปจริง นี่ไม่ใช่การปิด ticket คู่ fresh integrated Standards/Spec และ required checks ครบตาม gate เดิมจึงปิด ticket ได้

ผู้ประสานของแต่ละ ticket เป็นคนอัปเดต status block ด้านบนของ verification เมื่อมีผลตรวจหรือ branch ที่ใช้ต่อเปลี่ยน ระบุ branch/base และ SHA ที่ตรวจจริง ถ้ายังอยู่บน branch งาน ให้บอกคนรับต่อชัด ๆ ว่าต้องใช้ ref ไหน และให้ใช้ main เมื่อยืนยันแล้วว่ามี commit ที่ตรวจอยู่ใน main คนรับต่อรัน check ของรอยต่อที่ใช้ก่อนพึ่งพาโค้ดจริง ส่วนที่ยังรอให้เริ่มด้วย interface และ linked fixture ได้ตามแผน

สำหรับรีวิวก่อนส่ง seam ให้ A จัดช่วง focused review ประมาณ 10–15 นาที เน้นเรื่องที่ส่งผลต่องานถัดไป เช่น รูปผิด SKU, IDs หรือ payload ผิด contract และข้อมูล private หลุด ถ้าพบ blocker ให้ระบุเฉพาะ seam ที่ใช้ต่อไม่ได้ เจ้าของ และขั้นตรวจซ้ำ หลังแก้ให้ตรวจจุดที่กระทบก่อนเปลี่ยนสถานะ ส่วนงานอื่นเดินต่อและใช้ gate Standards/Spec ของโค้ดรวมตามเดิม

ใน verification และ PR ให้บอกอย่างน้อย:

```text
Ticket / role / checklist ที่ส่ง:
Code state: commit SHA ที่ตรวจ
Changed files และ interface ที่กระทบ:
Checks: คำสั่งหรือการกระทำ → expected → actual
Evidence: ใช้ fixture/stub หรือ ZIP/API จริง พร้อมลิงก์ผลที่ไม่เปิดเผย secret; ระบุ app cache mode ใน live runs
Handoff: ส่งอะไรให้ใคร และต้องรวม dependency ใดก่อนใช้จริง
Pending: อะไรยังไม่ทำ/ไม่ผ่าน และใครรับต่อ
```

### ตัวอย่างคำสั่งให้ coding agent

```text
I am team member A. Read AGENTS.md, README.md, the PR-01 plan and
verification.md, and the contracts relevant to role A.
Implement only role A's PR-01 checklist. Verify V01-A and record the
actual commands, expected/actual results, code state and pending handoffs
under "ผลจริงของ A" in verification.md. Prepare my role PR with that evidence.
Do not implement B/C's work or mark the whole ticket complete.
```

เปลี่ยน role/ticket/V ให้ตรงงานที่รับ ไม่สั่งให้ agent ของแต่ละคนทำทั้ง ticket ไม่มี app commands ตายตัวในชุดเริ่มต้น ให้บันทึกคำสั่งที่ implementation สร้างจริง

v0.16 เปลี่ยนกติกาคำถาม รายงานตามคำตอบ และการกู้เคส ส่วนโครงทีม A/B/C เจ้าของไฟล์ กรอบเวลา และ review gate เดิมยังใช้

## เป้าหมายเวลาและเกณฑ์รับงาน

ยึด [ตาราง 6 ชั่วโมง](integration-plan.md#ตาราง-6-ชั่วโมง) เป็นแหล่งเดียว: import/selection จริงภายใน 1:30, เส้นทางแรกผ่านอย่างน้อยสองรอบถามและถึงรายงานภายใน 2:30, ตรวจจริงและแก้ก่อน freeze สำหรับ review ที่ 4:15, review/fixes ถึง 5:15 แล้วกู้เคสหลัง restart และซ้อมเดโมถึงชั่วโมงที่ 6 เป็นเป้า ไม่ใช่เวลาที่วัดแล้ว

แต่ละ ticket มี verification.md ที่ยังระบุ not run ต้องบันทึกสิ่งที่รันจริงเมื่อพัฒนา ไม่ติ๊กผ่านจาก fixture ที่เขียนขึ้นหรือผลรีวิวเอกสารนี้ และไม่ใช้สถานะ ready ของ JSON แทนผลความหมายที่ถูกต้อง

## การยอมรับ breakdown

ผู้ใช้ตอบ “ประมาณนี้โอเค จะทำไรต่อหลังจากนี้” วันที่ 19 พ.ย. 2569 จึงยอมรับขนาด 4 tickets และจุดส่งต่องานตามที่เสนอ เปลี่ยนเป็น ready-for-agent สำหรับวันแข่ง ไม่มีการเริ่ม Build จากการยอมรับนี้ งานต่อคือระบุ A/B/C และเครื่องเดโม เตรียม environment/บัญชี แล้วเลือกงานใน fresh Build context เมื่อถึงวันพัฒนา

