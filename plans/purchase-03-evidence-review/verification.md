# PR-03 verification

Status: not run; no app implementation

- Branch/base และ tested SHA: pending
- ส่งต่อได้แล้ว: ยังไม่มีผลจากโค้ดจริง เริ่มงานจาก interface/fixture ได้
- Blocker และเจ้าของ: pending ให้ผู้ประสาน ticket อัปเดตเมื่อพบ
- รอตรวจเพื่อส่งต่อ: PR-04 ต่อ report runner และหลักฐานจาก snapshot ที่ผ่าน V03
- Gate รวม: pending ตามเกณฑ์ด้านล่าง
Authority: [plan](plan.md), [report contract](../../docs/contracts/report-contract.md)

## ตรวจส่วนของตนก่อนเปิด PR

ทุกรายการยัง not run บันทึกผลแยก A/B/C ตาม [รูปแบบส่งงาน](../../docs/team-plan.md#วิธีส่ง-pr-ของแต่ละคน) output ที่เขียนขึ้นใช้ทดสอบ rendering/validation เท่านั้น ไม่ยืนยันการค้นพบของโมเดล

| ID / เจ้าของ | วิธีตรวจและผลที่ต้องได้ | หลักฐานก่อนส่ง |
| --- | --- | --- |
| V03-A / A | รัน frontend type check/build และ checks ของ report ลอง shared/per-option findings, coverage ครบ 3 กลุ่มและ zero findings; เปิด original/translation/ภาพตรง source; แก้/copy draft ไม่เปลี่ยน findings; draft ว่างซ่อน copy; failed/retry/edit/stale ไม่แสดงผลผิด case/revision | คำสั่ง/ผล, actions/expected/actual และ report/source IDs ระบุ harness/HTTP จริง รวม copy success/failure ตามที่ตรวจได้ |
| V03-B / B | รัน report HTTP/state checks ด้วย adapter ที่หน่วง/failed/exception ได้: จับ input/image bytes ก่อน await; เปลี่ยน snapshot/selection/context/answers แล้วผลเก่า commit ไม่ได้; running=409, ready ใช้ cache, failed retry เริ่ม run ใหม่และปล่อย reservation; images คนละ snapshot เปิดไม่ได้ | คำสั่ง/ผล, binding/case/revision/generation, call counts และ error mapping ระบุ adapter ไม่อ้างว่าเป็น API จริง |
| V03-C / C | รัน validator/draft checks กับ bad quote/source/option/requirement refs, missing/unreadable/unsent-image citations, unknown scope, comparison/coverage และ null requirement; ตรวจ draft ตาม order/A–B/private margin/empty; จำกัดส่งภาพจริง ≤3 และไม่ใส่ expected answers จากนั้นอ่านรายงาน API เทียบหลักฐานใน T1–T5 | คำสั่ง/ผล rejected mutations และ draft checks, prompt version/actual source-image inventory ผล API จริงอ้าง PR-04 ร่วมได้; ระบุ semantic checks ที่ยัง pending ไม่ใช้ schema pass แทน |

ก่อนต่อ report runner จริง ให้ B/C ใช้ ReviewInput จาก PR-02 ที่มีหลายรอบและไม่มีคำตอบจากแขนงเก่า และตรวจว่า bytes รูปมาจาก snapshot เดียวกับ selected SKU ตาม source IDs ลองกรณีรูปหายและ snapshot เปลี่ยนระหว่างรอ แล้วตรวจว่าไม่ยืมรูปหรือแสดงผลเก่า

ทดสอบคู่รายงานใน T1 ด้วย ZIP/SKU เดิม แต่สลับคำตอบว่าซื้อเฉพาะสำเร็จรูปหรือยอมสั่งทำ ลำดับ/ทางเลือกและคำถามผู้ขายต้องสะท้อนเงื่อนไข หลักฐานและสิ่งที่ยังต้องยืนยันของ SKU เดิมคงเดิม

## เกณฑ์รวมของ ticket

| กลุ่ม | สิ่งที่ต้องตรวจจริง | หลักฐานที่ต้องเก็บ |
| --- | --- | --- |
| References | quotes/requirement refs/option scopes/coverage/comparison rows | ผล schema และ cross-reference checks รวมตัวอย่างผิดที่ถูกปฏิเสธ |
| ข้อมูลขาด | source removed/missing/unreadable/not-sent เทียบกับ seller states no report | รายงานและชุดที่ส่งจริง ไม่อ้างเพียง availability |
| ความต้องการ | skip/เปลี่ยนใจ/บริบทขัดคำตอบ | requirement/open preference ที่ตาม input จริง ไม่มี requirement จาก null |
| Draft/UI | ลำดับคำถาม/ชื่อ SKU/ไม่มี margin, copy แก้ได้และไม่ย้อนแก้ findings | ข้อความ draft กับ UI จริงและผลเมื่อไม่มีคำถาม |
| Failure/state | invalid/refusal/incomplete/timeout/retry/stale | failed result, retained answers, calls/IDs ไม่ใช้ sample fallback |

กฎ quote/ID ถูกไม่ได้พิสูจน์ความหมาย ต้องอ่าน output เทียบหลักฐานด้วยใน PR-04 ยังไม่อ้างคุณภาพโมเดลจาก expected report หรือ fixture เติม actual commands/code state/results เมื่อพัฒนา


## ผลจริงของ A

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว

## ผลจริงของ B

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว

## ผลจริงของ C

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว

## Review รวมก่อนปิด ticket

Required review: pending implementation/integrated state คู่ Standards/Spec ต้องตรวจ acceptance ของใบนั้นอย่างชัดเจนก่อน complete บันทึก base/target/state, reviewers, findings/dispositions/final checks และ full-round ledger ต่อ ticket การส่งต่อเพื่อรวมยังไม่นับว่าปิดงาน
