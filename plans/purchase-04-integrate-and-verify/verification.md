# PR-04 verification

Status: not run; no app implementation

- Branch/base และ tested SHA: pending
- ส่งต่อได้แล้ว: ยังไม่มีผลจากโค้ดจริง เริ่มงานจาก interface/fixture ได้
- Blocker และเจ้าของ: pending ให้ผู้ประสาน ticket อัปเดตเมื่อพบ
- รอตรวจเพื่อส่งต่อ: ทีมเดโมใช้ SHA ที่ผ่าน gate รวมและ restart/rehearsal ตามเกณฑ์ด้านล่าง
- Gate รวม: pending ตามเกณฑ์ด้านล่าง
Authority: [plan](plan.md), [T1–T5 และ integration gates](../../docs/integration-plan.md#ชุดกรณีตรวจที่ใช้ร่วมกัน)

## ตรวจส่วนของตนก่อนเปิด PR

ทุกรายการยัง not run ใช้ [รูปแบบส่งงาน](../../docs/team-plan.md#วิธีส่ง-pr-ของแต่ละคน) ส่ง harness/wiring ที่ตรวจแยกแล้วได้ก่อน และระบุ gate จริง/review/rehearsal ที่ยังไม่รันเป็น pending ส่วนการปิด ticket ต้องครบตามตารางรวม

| ID / เจ้าของ | วิธีตรวจและผลที่ต้องได้ | หลักฐานก่อนส่งและก่อนปิดงาน |
| --- | --- | --- |
| V04-A / A | รัน frontend type check/build และ deterministic UI flow ที่เพิ่ม ตรวจ UI จริง ZIP→เลือก→คำถามปรับตามคำตอบ/0/partial/skip/edit→report→source/copy รวมผลเก่า/failed; ประสานรีวิว integrated commit แล้ว restart/rehearse กับ B | ก่อนส่ง harness: คำสั่ง/ผลและสิ่งที่ยัง stub; ก่อนปิด: UI actions จริง, review briefs/results/dispositions/ledger ครบ 4 ใบ และ restart/rehearsal record |
| V04-B / B | รัน HTTP/security/state ชุดรวมบน integrated state ตรวจ malicious/oversize/client edits/duplicate/cache/retry/stale และ ZIP T4/T5; ร่วม A เปิดจาก process ใหม่ด้วย run instructions จริง กู้เคสเดิมจาก SQLite พร้อมรูป/คำตอบ งานค้าง Retry ได้ เมื่อเปลี่ยน ZIP เคสและรูปเดิมใช้ไม่ได้ | ก่อนส่ง wiring: คำสั่ง/ผล boundary/state และ ZIP mutation provenance; ก่อนปิด: integrated commit/run commands/เครื่องเดโมและผล restart จริง ไม่มี fixture fallback |
| V04-C / C | รัน T1–T5 ผ่าน API จริงและอ่านความหมายเทียบ source/requirements เก็บ usage/time/attempts/incomplete และ cases ที่แก้แล้วตรวจใหม่ ทำ repeat เมื่อ gate ผ่านและมีเวลา | ก่อนส่ง evaluation: run IDs/code state/prompt version/actual model, expected/actual และ defects; ก่อนปิด: T1–T5 แต่ละกรณี passed หรือข้อค้างที่ทำให้ปิดไม่ได้ พร้อมจำนวน repeat จริงและข้อจำกัด |

## เกณฑ์รวมของ ticket

| Gate | สถานะปัจจุบัน | หลักฐานที่ต้องเติมเมื่อรัน |
| --- | --- | --- |
| Real ZIP → real model → report | not run | snapshot/case/code state, actions และผลจริง ไม่ใช้ sample |
| T1 sparse/skip/edit | not run | พบประเด็นเอง คู่แขนงให้คำถามและรายงานต่างกันตามคำตอบ เก็บ revision และข้อมูลที่ข้าม |
| T2 explicit/private note | not run | ไม่ถามซ้ำ/ไม่รับรอง SKU จาก menu/ไม่มี margin ใน draft |
| T3 changed SKU/order | not run | ชุดใหม่และ A/B ใหม่ ไม่มีคำ/ภาพปนจาก SKU ที่ไม่ได้เลือก |
| T4 removed report + untrusted instruction | not run | mutation provenance, actual model exposure, ไม่อ้างข้อความที่ลบ/ไม่ทำตามคำสั่ง |
| T5 missing image | not run | missing state, source scope/limitations และข้อมูลที่ยังใช้ได้ |
| UI/HTTP/security/state failures | not run | คำขอ/การกระทำ/code state/expected/actual รวม zero/partial/stale/retry |
| Standards + Spec code review | pending implementation | fixed base/target, briefs/results ครอบคลุม PR-01–04, reviewer route, findings/dispositions/final checks และ review-round ledger ต่อทุกใบที่ได้รับผล |
| SQLite restart + English rehearsal | not run | process ใหม่, คง SKU/รูป/คำตอบที่ commit, Retry งานค้างได้, เปิด ZIP ใหม่แล้วเคสเก่าใช้ไม่ได้ และซ้อมเดโมอังกฤษ |
| Usage/latency/repeats | not measured | actual model/tokens/time/attempts และจำนวนรอบที่ทำจริง |

บันทึก secret และบันทึกธุรกิจส่วนตัวให้น้อยที่สุด ไม่ใช้ชื่อโมเดลที่ตั้งไว้แทน actual model ที่รัน ไม่กล่าวว่า accuracy สูงกว่าหรือประหยัดกว่า ChatGPT หากยังไม่มี benchmark



## ผลจริงของ A

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว

## ผลจริงของ B

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว

## ผลจริงของ C

Status: not run ใช้รูปแบบหลักฐานใน team plan เมื่อรันตรวจแล้ว

