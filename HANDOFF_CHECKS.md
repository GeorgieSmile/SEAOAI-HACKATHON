# Handoff checks

Status: v0.16 handoff package checked; application, live report and SQLite restart pending
Snapshot date: 20 พ.ย. 2569

## สรุปความพร้อม

คัดลอกเนื้อหาโฟลเดอร์นี้ไป repo เปล่าและเริ่มงานตาม README/AGENTS ได้ มีเอกสารหลัก ใบงานพร้อมเกณฑ์ ข้อมูลสินค้า รูป และ reference pack ครบ ชุดนี้ไม่ต้องเข้าถึง workspace/บทสนทนา/plugin ของผู้จัดทำ

สิ่งที่พร้อมคือ **จุดเริ่มทำงาน** ไม่ใช่แอปที่รันได้ ยังต้องกำหนดสมาชิก/เครื่องเดโม ตั้ง dependencies ในวันพัฒนา และตรวจบัญชี/เครดิต/โมเดลจริง

## v0.16 คำถามหลายรอบและกู้เคส

- ตรวจตัวอย่างรายงาน 2 ชุดและเส้นทางคำถาม 5 ชุด รวมข้อมูลพอตั้งแต่ต้น ตอบบางข้อ ข้ามทั้งหมด ดำเนินต่อจากรอบสอง และแก้รอบแรกแล้วเปลี่ยนแขนง ผลเดิมพบข้อผิดพลาดครบทั้ง 11 กรณีที่จงใจเปลี่ยนข้อมูล รวมหลักฐานผิด กรณีเกิน 5 ข้อ/เกิน 3 รอบ revision ผิด หรืออ้างรอบที่ถูกแทน
- เกณฑ์เดิมเรื่อง ZIP/รูปตรง SKU, contextual translation, ข้อจำกัดหลักฐาน, ความเป็นส่วนตัวของ draft, T1–T5, required review และหน้าที่ A/B/C ยังตรวจในใบงานเดิม ตรวจ starter โดยแตกจาก ZIP ในโฟลเดอร์งานใหม่
- ตัวอย่าง restart เป็น contract trace การกู้เคสและรายงานจากโปรเซสจริง รวมถึงการอ่านผลโมเดลแบบคู่แขนง ยังต้องตรวจตอน Build ผลรีวิว Opus และผลซ้อมด้านล่างอ้าง snapshot ก่อนแก้คำถามหลายรอบ

## อัปเดตหลังซ้อม PR-01 และ PR-02

- ตรวจ raw fixture แบบ static แยกจากแอปซ้อม พบ 314 entries, ชื่อไฟล์ Unicode แบบ Finder, SKU map ในบริเวณสินค้า 2 ชุดที่ตรงกัน และไม่มีบล็อก curated เทียบ inventory แล้วตรงทั้ง 12 SKU, hash รูป 12 ภาพ, ค่าร่วม 720ml และคุณสมบัติ 40 คู่ ดู [ที่มาและขอบเขตการสแกน](samples/raw-save/PROVENANCE.md)
- ตอนส่งมอบ ผู้จัดทำลองแตก ZIP ในโฟลเดอร์ที่มีช่องว่างและอักษรไทยในชื่อ และตรวจรายชื่อไฟล์กับ hash ครบแล้ว
- ปรับกฎ UI, เกณฑ์ V01–V03 และ status block ประจำ ticket ผลตรวจแอปจริงในชุดแข่งยัง pending ชุดนี้เก็บ fixture และบทเรียนจากการซ้อม ส่วนโค้ดแอปและผลการซ้อมอยู่ใน repo แยก ผล Opus ด้านล่างเป็นของ snapshot ก่อนอัปเดตหลังซ้อม

## บทเรียนจากการปรับ prompt รอบซ้อม

เพิ่มแนวทาง [AI plan](docs/ai-execution-plan.md#แนวทางสำหรับ-prompt-แปลและคำถาม) และเกณฑ์ V01-C/V02-C ให้ตรวจอังกฤษที่อ่านง่ายโดยคงความหมายเดิม และให้น้ำหนักกับการใช้งานที่เปลี่ยนสิ่งที่ต้องตรวจใน T1/T2 เดิม

ทีมซ้อมรายงานว่าการแก้ prompt ผ่านชุดทดสอบ translation 93 และ questions 203 รายการ ยังไม่ได้เรียก Terra ด้วย prompt ฉบับแก้ ผลทดสอบจาก API ของฉบับนี้จึง pending ชุดส่งต่อเก็บแนวทางและเกณฑ์ตรวจไว้ให้ C ใช้ในวันงาน ส่วนไฟล์ prompt ของแอปอยู่ใน repo ซ้อม

## การแจกแจงงานก่อนเปิด PR

เพิ่ม checklist และจุดส่งต่อของ A/B/C ใน plan ทั้ง 4 ใบ พร้อม V01–V04 แยกตามเจ้าของใน verification และวิธีบันทึกคำสั่ง/ผลจริงก่อนส่ง PR ใน team-plan แยกการรวมส่วนงานที่ตรวจ seam แล้วออกจากการปิด ticket หลัง integrated review ตาม gate เดิม

รีวิวอิสระ Opus สองแกนตรวจ diff `9c729f8...6bfa231` บน snapshot ที่ตรึง และยืนยัน fingerprint ไม่เปลี่ยนก่อน/หลัง ผลตรวจในรอบนั้นผ่าน 158 links, 4 fixture negatives และ 5 package negatives

- Standards: ไม่พบ hard breach พบ Low เรื่องไม่มีหัวข้อบันทึกผลที่สั่งให้ใช้ และข้อเสนอ Low ให้รวมตัวอย่างคำสั่ง agent ไว้จุดเดียว แก้ทั้งคู่แล้วโดยเตรียม `ผลจริงของ A/B/C` สถานะ not run ในทุก verification และให้ README ลิงก์ไป template ใน team-plan
- Spec: ไม่พบ blocking/Medium พบ Low เรื่องหัวข้อบันทึกผลเช่นเดียวกัน และงาน A ของ PR-02 ไม่ระบุ Not sure / Skip ชัด แก้โดยเติม control ต่อคำถามและเกณฑ์ V02-A ที่ต้องส่ง null โดยไม่ล้างข้ออื่น

ผู้จัดทำตรวจเฉพาะจุดหลังแก้ Low ทั้งสามเรื่อง ไม่ได้เปิด full review รอบใหม่ ตัวอย่าง/schema/product contracts/ข้อมูลสินค้าไม่เปลี่ยน ตรวจชุดสุดท้ายและ ZIP round trip ซ้ำหลังแก้ สถานะ app verification ทุกใบยัง not run ไม่มี app/API tests

metadata ยืนยัน executedModel เป็น claude-opus-5-5 ทั้งคู่ medium เป็นค่าที่ dispatch แต่ actual effort ไม่เปิดให้ตรวจ Standards อ่าน diff ทั้งหมดและจุด ownership ที่เกี่ยวข้อง Spec อ่าน diff กับส่วน HTTP/state/ownership/timing และ contract ที่เกี่ยวข้อง ไม่ได้อ่าน spec/acceptance/report contract ทุกบรรทัด ผลนี้ครอบคลุมการแจกแจงงาน ไม่ใช่การรับรองแอปหรือความหมายรายงานจากโมเดล ส่วนผลด้านล่างเป็นการตรวจ starter ก่อนแจกแจงงาน

## Standards

Opus ตรวจ snapshot `556eb55` เทียบ empty-repo baseline ไม่พบ hard violation พบข้อ Medium เรื่องข้อความ hash/file-list error ไม่บอกชื่อไฟล์

ผู้จัดทำแก้ให้ข้าม .DS_Store และระบุชื่อไฟล์ที่ขาดหรือเกิน พร้อมเพิ่มกรณีทดสอบสำหรับ Finder metadata และลิงก์ไปยังหัวข้อในไฟล์เดียวกัน

Opus ตรวจ fixed target ก่อน/หลังตรงกัน ข้อจำกัด: ไม่ได้รันบน Python 3.10 จริงหรือทดสอบบนเครื่องเพื่อน ผลนี้ตรวจมาตรฐานชุดส่งต่อ ไม่ยืนยันแอป/API/model quality

## Spec

Opus ไม่พบ blocking finding ตรวจ HTML ภายใน ZIP ด้วย parser ที่เขียนขึ้นใหม่ พบ title/12 SKU/40 properties ครบ รูป local 45 ไฟล์ resolve ได้ มี source-sku-map แบบ JSON ที่ไม่รัน code 1 บล็อก จำนวน 12 keys และ specAttrs ตรง parent/child720ml จริงโดยไม่พึ่ง sidecar inventory ภาพ fixture ทั้ง 3 เท่ากับภาพใน ZIP และค่า checked เดิมยังอยู่เพื่อให้ทดสอบว่าแอปไม่เลือกแทนผู้ซื้อ

พบ Low เรื่องข้อความอ้าง screening/research records ที่ไม่รวมในชุด และถ้อยคำจากบริบทรีวิวเดิม ผู้จัดทำระบุให้ชัดว่าไม่มี records เหล่านั้นใน starter และใช้คำทั่วไปว่า document review ไม่แทน code review แล้ว

Opus อ่านโครงสร้าง cases ของ JSON examples แต่ไม่ได้อ่าน mock ทั้งไฟล์หรือตรวจความหมายของ expected reports ทุกข้อ คุณภาพรายงานจากโมเดลยังต้องประเมินตอน Build

## ผลตรวจโดยผู้จัดทำ

- เทียบ source โดยตรงตอน export: h1, 12 SKU labels, 40 property pairs, original skuMap JSON และ bytes ภาพ คงต้นฉบับไว้ไม่แก้ ดู [provenance](samples/provenance.json)
- ตัด off-product/admin/debug/User IP และ executable scripts เก็บเฉพาะ SKU JSON ต้นทางเป็นบล็อก application/json ไม่สร้างหลักฐานความสัมพันธ์ขึ้นเอง
- เปิด/verify ภาพ 45 ไฟล์ด้วย Pillow ตอน export ภาพ SKU ทั้ง 12 ยังเป็น 150×150 ไม่ได้ fetch full-resolution
- ตอนส่งมอบ ผู้จัดทำตรวจ hash ของไฟล์ ลิงก์และหัวข้อปลายทาง ความสัมพันธ์ระหว่าง sample กับหลักฐาน และ fixtures แล้ว
- Fixture checks: 2 reports, 3 question cases, 4 rejected mutations ส่วน package checks มี 5 rejected mutations: missing spec, extra file, outside link, changed SKU, missing relation block พร้อม positive checks สำหรับ .DS_Store และ same-file heading
- คัดลอกไป Git repo เปล่าจริงใน path ที่มีช่องว่าง/ภาษาไทย และ ZIP→extract ซ้ำก่อนส่งชุดสุดท้าย .gitignore ถูกคัดลอกและ sample ZIP ไม่ถูก ignore
- ตรวจ SHA ของหลักฐานโมเดล/prototype ใน workspace ต้นทางว่ายังไม่เปลี่ยน ไม่มีการส่ง secret หรือบัญชีส่วนตัวมาในชุด

## ขอบเขตรีวิวและสถานะฉบับแก้

ผู้ใช้ให้เปลี่ยนเป็น Opus จึงหยุดรีวิว Codex และใช้ Opus แยก Standards/Spec บน fixed snapshot จริงทั้งสองแกน metadata ยืนยัน executedModel เป็น claude-opus-5-5 ทั้งคู่ ส่วน medium เป็นค่าที่ dispatch ไม่มี actual-effort metadata ให้ตรวจซ้ำ

ทั้งสองอ่าน Ponytail/Thai-writing เต็มไฟล์ แต่ไม่ได้รัน Thai context helper ผู้จัดทำใช้ guidance เดิมและตรวจภาษาในไฟล์ใหม่แยกต่างหาก

หลังรีวิวแก้เฉพาะข้อเล็กด้าน diagnostics/checks/ถ้อยคำ แล้วตรวจเฉพาะจุดและคัดลอก/แตก ZIP ซ้ำ ไม่ได้ full independent re-review ของการแก้เล็กเหล่านี้ hash ของ `556eb55` เป็น snapshot ที่ใช้รีวิวใน repo ชั่วคราว ไม่ใช่ commit ที่ผู้รับต้องมีใน repo ใหม่

## สิ่งที่ยังไม่พิสูจน์

- ไม่มี frontend/backend ของแอปจริง ยังไม่ผ่าน T1–T5 ของแอป ไม่มีผล API access/latency/cost/คุณภาพรายงาน
- มีทั้ง product-only derived save และ raw browser save ที่ redacted แล้วจากสินค้าเดียวกัน การตรวจ fixture สองไฟล์ยังไม่ยืนยันว่า importer ที่ทีมจะเขียนรองรับทุก saved page ดู [samples/README](samples/README.md)
- ทดสอบเครื่องต้นทาง Python 3.15.2/macOS ARM เท่านั้น การระบุ Python 3.10+ อิง API/syntax ที่ใช้ ยังไม่ได้รัน interpreter/OS ทุกเวอร์ชัน
- ยังไม่ได้ทดลองพัฒนาจบภายใน 6 ชั่วโมงหรือรีวิวทั้งแอปจริงในเวลาที่วางไว้
