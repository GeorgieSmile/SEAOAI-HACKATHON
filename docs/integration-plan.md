# แผนเชื่อมระบบและแบ่งงาน 3 คนใน 6 ชั่วโมง

Status: accepted design under spec v0.16; implementation and runtime unverified
Version: 0.6
วันที่: 20 พ.ย. 2569
ต้นทาง: [intent](brief.md), [spec](spec.md), [รับ ZIP](contracts/intake-contract.md), [รอบคำถาม](contracts/question-contract.md), [รายงาน](contracts/report-contract.md), [แผน AI](ai-execution-plan.md)

ยอมรับเป็นส่วนหนึ่งของ [spec v0.15](spec.md) วันที่ 19 พ.ย. ตาม [บันทึกการยอมรับ](acceptance.md) การยอมรับ Design ไม่ใช่หลักฐานว่าแอปทำงานแล้วหรือคำสั่ง Build

## ผลที่ต้องส่งมอบ

เดโม local บนเครื่องเดียว ตั้งแต่ ZIP จริง → คำแปล/รูป → ยืนยัน 1–3 SKU → คำถามที่ปรับตามคำตอบ สูงสุด 3 รอบ รอบละ 1–5 ข้อ → รายงานที่เปิดหลักฐานและคัดลอกข้อความถามผู้ขายได้ ต้องกลับมาทำเคสล่าสุดหลัง restart ได้

แผนนี้เป็น baseline ที่ยอมรับสำหรับเชื่อมและแบ่งความรับผิดชอบ ส่วน ticket breakdown ได้รับการยอมรับแล้ววันที่ 19 พ.ย. พร้อมรับงานในวันแข่ง ไม่เริ่มสร้างแอปหรือเปลี่ยน prototype ในรอบ Design งานใหม่ที่ใช้ตัดสินต้องเกิดในวันแข่ง

## โครงที่เสนอ

- React + TypeScript + Vite สำหรับ frontend และ Python + FastAPI สำหรับ backend ใช้ HTTP request/response ปกติ ใช้ SQLite ไฟล์เดียวสำหรับกู้เคสล่าสุด ไม่มี queue, WebSocket, polling หรือ agent framework
- เครื่องเดโมรัน Vite ที่ `127.0.0.1:5173` และ backend ที่ `127.0.0.1:8000` แบบหนึ่ง worker Frontend ใช้ relative `/api/...` ผ่าน Vite proxy ไม่ฝัง backend hostname ในแต่ละหน้าหรือเปิด wildcard CORS
- Bind ทั้งสองฝั่งเฉพาะ loopback ใช้ URL เดโมเดียว `http://127.0.0.1:5173` ใน run instructions หากมี Origin ในคำขอ POST ให้ตรง URL นี้ ปฏิเสธ Origin อื่น รวม localhost ที่ใช้คนละ URL อนุญาตคำขอไม่มี Origin สำหรับ local CLI/tests ไม่เปิด wildcard CORS เก็บ API key เฉพาะ backend ไม่ใช้ตัวแปร VITE_* สำหรับ secret และไม่บันทึก raw HTML/key/private buyer context ลง log
- รับผู้ใช้หนึ่งคน หน้าต่างเดโมหนึ่งชุดและเคสปัจจุบันชุดเดียว การอัปโหลดใหม่ทำให้ข้อมูลเดิมใช้ต่อไม่ได้ ไม่มี login หรือประวัติหลายเคส
- ใช้หน้าตาและการเปิดหลักฐานจาก prototype เป็นแบบอ้างอิง ส่วนหน้าคำถามเดิมเป็นภาพก่อนปรับหลายรอบ สร้าง flow ที่ใช้ข้อมูลจริงในแอปใหม่ ไม่ย้ายการจำลอง ZIP/AI มาเป็น fallback ในระบบจริง

การใช้ proxy อ้างอิง [Vite server options](https://vite.dev/config/server-options) และการตั้งหนึ่ง process เป็นข้อจำกัดของ coordinator และเดโมแบบ local ในแผนนี้ ดู [FastAPI workers](https://fastapi.tiangolo.com/deployment/server-workers/) ไม่ถือว่าเหมาะกับหลายผู้ใช้หรือ deploy สาธารณะแล้ว

## จุดเชื่อม HTTP

| Request | ข้อมูลจาก frontend | ผลที่คืน |
| --- | --- | --- |
| `POST /api/imports` | JSON `{"url":"…"}` สำหรับลิงก์สินค้า TTP Cargo (1688, Taobao, Tmall) หรือ multipart field `file` หนึ่ง ZIP | IntakeResult เดิม เมื่อ ready เก็บ snapshot และภาพไว้ให้เริ่มเคส |
| `POST /api/questions` | `{operation_id, snapshot_id, previous_case, selected_option_ids, buyer_context}` | เริ่มเคสพร้อม case_id ใหม่ที่ Backend ออก โดย previous_case เป็น null ถ้ายังไม่มี หรือ `{case_id, revision}` ที่ต้องตรงเคสก่อนแทน |
| `POST /api/questions/continue` | `{operation_id, case_id, revision, submission}` | บันทึกคำตอบแล้วขอรอบถัดไป หรือคืน buying brief เมื่อจบคำถาม |
| `POST /api/brief` | `{operation_id, case_id, revision, submission, order_clarification?}` | บันทึกคำตอบหรือข้อยืนยันเฉพาะ SKU แล้วคืน brief |
| `POST /api/brief/preview` | `{case_id, revision, text, option_id?, resolves_change_id?}` | ตรวจข้อความและแสดงข้อกำหนดกับรอบที่ได้รับผล |
| `POST /api/brief/preview-answers`, `POST /api/brief/preview-undo` | revision ปัจจุบันพร้อม submission หรือ `undo_change_id` | คำนวณผลก่อนแก้คำตอบหรือ Undo |
| `POST /api/brief/edit` | revision และ operation ID พร้อม `correction` กับ `proposal` ที่ผ่านการ preview หรือ `undo_change_id` | ตรวจซ้ำและบันทึกเป็น revision ใหม่ |
| `POST /api/questions/resume` | `{operation_id, case_id, revision}` | ออกรอบแทนรอบที่ brief เปลี่ยนเงื่อนไข เมื่อยังอยู่ในเพดาน |
| `POST /api/reviews` | `{operation_id, case_id, revision, confirm: true}` | ยืนยัน brief และจองงานสร้างรายงานของ revision เดียวใน transaction เดียว |
| `POST /api/case/retry` | `{case_id, revision, operation_id}` ของงานล่าสุดที่ล้มเหลว | รันขั้นเดิมจากข้อมูลที่บันทึกไว้ ไม่เพิ่มรอบหรือล้างคำตอบ |
| `POST /api/case/reset` | `{snapshot_id, previous_case}` ตรงข้อมูลปัจจุบัน หลังผู้ซื้อเลือก Start over | ล้าง snapshot และเคสปัจจุบัน เพิ่ม generation คืน 200 พร้อม case และ snapshot เป็น null |
| `GET /api/case` | ไม่มี body | snapshot ปัจจุบันและเคสที่บันทึก หรือ null สำหรับส่วนที่ยังไม่มี อ่านอย่างเดียวและไม่เรียก AI |
| `GET /api/snapshots/{snapshot_id}/images/{source_id}` | รหัสจาก snapshot ปัจจุบัน | bytes ภาพที่ตรวจแล้วพร้อม MIME หรือ 404 |

ผู้ซื้อตอบข้อขัดแย้งเรื่องรูปแบบการสั่งซื้อผ่าน `POST /api/brief` โดยส่ง `submission: null` และ `order_clarification: {requirement_id, option_id, choice}` ใช้รหัสข้อขัดแย้งและ SKU จากเคส revision ปัจจุบัน โดย `choice` เป็น `ready_made_only` หรือ `custom_orders_acceptable` การเลือกจะสร้าง revision ใหม่ การส่ง operation เดิมซ้ำได้ผลเดิม

Case view มี `case_id`, `revision`, `snapshot_id`, `selected_option_ids`, `buyer_context`, `rounds` ตามลำดับ, `phase`, `reason` (ข้อความจบการถามหรือ null), `report` (PurchaseReview ของ revision ปัจจุบันหรือ null) และ `last_operation` (null หรือ `{operation_id, stage, status, code, message}` stage เป็น start/continue/brief/review และ status เป็น running/ready/failed โดย code/message มีค่าเมื่อ failed) แต่ละรอบมี round_id/round_number/reason/questions ตาม question contract และ `answers` เป็น null ทั้ง field ระหว่างรอตอบ หรือรายการครบทุกข้อเมื่อส่งแล้ว phase ใช้ `preparing_questions`, `awaiting_answers`, `ready_for_review`, `generating_review`, `reviewed`, หรือ `failed`

เมื่อขั้นคำถามคืน `ready_for_review` ให้ UI แสดง buying brief พร้อมข้อกำหนดตาม SKU, ผลของการแก้ และ `confirmed_revision` หากยืนยันแล้ว การเปิดหรือกู้เคสใช้ `GET /api/case` และไม่เริ่มงานวิเคราะห์ การสร้างรายงานเริ่มจากคำสั่งยืนยันเท่านั้น คำขอซ้อนของ revision เดียวกันใช้ผลที่มีอยู่หรือได้ 409 ขณะยังประมวลผล

HTTP 200 ใช้ envelope `{status: "ready", case}` หรือ `{status: "failed", case_id, revision, code, message}` สำหรับ case commands (ค่ารหัสเป็น null ได้ก่อนสร้างเคส) shape ผิดใช้ 422, งานเดิมที่ running หรือ revision หมดอายุใช้ 409, ใหญ่เกินขอบเขตใช้ 413, idempotency key เดิมกับ payload ใหม่ใช้ invalid_input ไม่แปลง error เป็นทางข้ามคำถามหรือรายงานตัวอย่าง ปัญหาตั้งค่า/สิทธิ์/โควตาขั้นแปลคง intake_failed และไม่ publish ready ไม่ส่ง traceback หรือ provider error ดิบ เมื่อ network ล่มให้เก็บร่างใน UI และโหลดสถานะจาก Backend ก่อนอนุมานว่าคำตอบไม่ถูกบันทึก

UploadFile อาจ spool ลง disk ก่อนเข้า handler จึงตรวจ Content-Length ที่แจ้งมาก่อน parse และจำกัด actual request-body bytes ก่อน multipart parser ไม่ใช้ header เป็นข้อพิสูจน์เพียงอย่างเดียว เสนอขอบเขต body 21 MiB สำหรับ ZIP 20 MiB กับ multipart overhead และตรวจขนาดไฟล์ ZIP จริงซ้ำตาม intake-contract ปิด UploadFile ใน finally ไม่เก็บ upload spool ไว้ใช้หลัง request

ห้ามส่ง snapshot, sources, image_file, question_text หรือคำตอบของโมเดลกลับมาให้ backend เชื่อเป็นข้อมูลต้นทาง ใช้ schema ที่ปฏิเสธ extra fields การเสิร์ฟภาพ lookup ด้วยรหัสใน state เท่านั้น ไม่รับ filename/path และไม่ serve archive HTML ภาพส่งด้วย MIME ที่ตรวจแล้วพร้อม nosniff

เสนอความยาว buyer_context สูงสุด 2,000 ตัวอักษร และ answer_text ข้อละ 1,000 ตัวอักษร ตรวจทั้ง frontend/backend หากเกินให้แก้ข้อมูล ไม่ตัดข้อความเงียบ ๆ เพดาน archive/ภาพยังใช้ intake-contract เดิม

## เจ้าของข้อมูลและอายุข้อมูล

Backend เก็บ snapshot ล่าสุด, ภาพที่ตรวจแล้ว, เคส, rounds/คำตอบจริง, ผลรายงานพร้อมใช้ และข้อมูลงานล่าสุดสำหรับ Retry ใน SQLite **ไฟล์เดียวแบบ local** ใช้ `backend/.local/` เป็นตำแหน่งแนะนำและ ignore ทั้งไดเรกทอรี เก็บ encoded image bytes และ MIME คู่ source ID เก็บเฉพาะหลักฐานที่สกัดตาม contract ไม่เก็บ ZIP/raw HTML ทั้งชุด ไม่มี API key ในฐานข้อมูล ไม่ commit หรือส่งต่อฐานข้อมูลจริง ใช้หนึ่ง worker และปิด auto-reload ตอนเดโม

B ให้ `backend/state.py` เป็นทางเดียวที่เปิดและอ่าน–เขียน SQLite route นำเข้า คำถาม รายงาน และกู้เคส ใช้ตัวเก็บข้อมูลชุดเดียวกัน Frontend เรียก HTTP และโมเดลรับ QuestionInput/ReviewInput ที่ Backend ประกอบให้

1. เริ่ม upload ใหม่ให้ล้างเคสและ snapshot เดิมจากข้อมูลที่ใช้ต่อได้พร้อมเพิ่ม generation หาก import ล้มเหลวให้เริ่มจาก ZIP ที่ถูกต้อง ไม่เปิดเคสเก่าแทน เมื่อ import ใหม่สำเร็จจึง publish snapshot/ภาพพร้อมกัน เปลี่ยนตัวเลือก/ลำดับ/บริบทบน snapshot เดิมให้แทนเฉพาะเคสและคงรูปของ snapshot นั้น
2. เก็บเฉพาะ state ที่ commit แล้ว คำตอบที่กำลังเลือกหรือพิมพ์อยู่ที่ UI การแก้คำตอบ บันทึกงานถัดไป การตัดรอบ/รายงานเก่า และการเพิ่ม revision ทำใน transaction เดียว คงคำตอบที่ commit แล้วแม้ API ล้มเหลว
3. ก่อน await AI จับ input และ references ของ encoded bytes ตาม snapshot/case/revision ไม่อ่าน active snapshot ใหม่กลางงาน ไม่ถือ SQLite transaction ค้างระหว่างรอ AI ภาพอยู่ใน SQLite หรือโหลดเข้า map ฝั่ง Backend ได้ แต่ client ได้เพียง ID และรูป MIME ที่ผ่านตรวจจาก snapshot ปัจจุบัน ไม่มี path จาก client
4. งาน parse/decode ที่นานแยกออกจาก event loop และปล่อย references งานเก่าใน finally ไม่เก็บ decoded images หลายรูปพร้อมกัน เพดาน archive เดิมคงอยู่ (50 MiB เป็นเพดานต่อ archive ไม่ใช่ RAM รวม) ปิด UploadFile ตามเดิม
5. เมื่อเริ่ม process ใหม่ให้กู้ snapshot/เคสจาก SQLite งานค้างให้เป็น failed ที่ retry ได้พร้อม operation_id เดิม ใช้ input ที่ commit ไว้และไม่เรียกโมเดลเอง ผลที่ ready แล้วแสดงได้โดยติดกับ revision เดิม จากทางเลือก **Start over** ให้ผู้ซื้อยืนยันก่อนล้างเคสและ snapshot เดิม ตรวจว่า GET ไม่พบเคสหลังล้างและรูปเก่าเข้าไม่ได้ ใช้ฐานข้อมูลที่ว่างเมื่อทดสอบผู้ซื้อใหม่ การล้างนี้เป็นการล้างข้อมูลในแอป ไม่อ้างว่าลบร่องรอยบนดิสก์แบบกู้คืนไม่ได้

ใช้ lifespan เปิด/ปิดฐานข้อมูลและ SDK client หากอ่าน/เขียน SQLite ไม่สำเร็จให้แจ้งข้อผิดพลาดและไม่เริ่ม AI จาก state ที่ยังบันทึกไม่สำเร็จ ระบบไม่ถอยไปใช้ RAM เงียบ ๆ

ใช้ `AsyncOpenAI(max_retries=0)` และ await requests เพื่อไม่บล็อก image GET/การตรวจ request ซ้ำ ตั้ง timeout เป็นเวลาที่เหลือก่อน deadline ต่อขั้น ปิด client เมื่อ shutdown ตาม [OpenAI Python SDK](https://github.com/openai/openai-python) ไม่ใช้ retry ของ SDK ซ้อนกับ coordinator

## case IDs, retry และผลเก่า

- Backend ออก `snapshot_id` เมื่อ publish import, `case_id` เมื่อเริ่มเคส (แก้ SKU/ลำดับ/context เริ่มใหม่) และ `round_id`/question IDs เมื่อออกคำถามจริง `revision` เริ่มที่ 0 เพิ่มเมื่อ commit คำตอบของรอบที่ยังไม่ส่งหรือแก้ชุดที่ส่งแล้ว การขอรายงานจากข้อมูลที่บันทึกอยู่แล้วและ Retry ไม่เพิ่ม revision
- Frontend สร้าง `operation_id` ใหม่ต่อการกระทำ (UUID) การส่งซ้ำใช้ ID และ payload เดิม ตรวจการส่งซ้ำของงานล่าสุดก่อนตรวจ expected revision จึง retry ได้แม้คำขอเดิมเพิ่ม revision ไปแล้ว Binding เก็บ stage, fingerprint ของ normalized payload และเคส/ฉบับที่งานใช้ตลอด snapshot ปัจจุบัน เก็บผล/ข้อมูลที่ใช้ Retry เฉพาะงานล่าสุด งาน running เดิมคืน 409 งาน ready เดิมคืนผลเดิม ผลและคำขอที่ถูกแทนหมดอายุ ปฏิเสธก่อน AI แม้ client ส่งผ่าน shape
- การกระทำใหม่ที่เปลี่ยนข้อมูลครอง state ได้เฉพาะ case/revision ปัจจุบัน ระหว่างประมวลผล UI ปิดคำสั่งซ้อนในเคส แต่เปลี่ยน ZIP/SKU/context ได้ เมื่อการเตรียมคำถามล้มเหลว ผู้ซื้อกด Retry หรือเปิด buying brief จากคำตอบที่บันทึกแล้วได้
- Retry หลัง failed ใช้ input/operation เดิมจาก SQLite มี deadline และไม่เกิน 2 attempts ต่อ run ใหม่ ไม่เพิ่มรอบ ถ้ามี open round ต้องส่ง submission เมื่อขอรายงาน จะได้ไม่ทิ้งคำตอบที่กรอกไว้ หากชนิดข้อผิดพลาดแก้ไม่ได้ด้วย retry (เช่นสิทธิ์/โควตา) ให้แจ้งสิ่งที่ต้องแก้ตาม AI plan
- Backend commit ผลหรือปล่อย reservation เฉพาะเมื่อ generation, case_id, revision และ operation_id ยังตรงกัน จัดการ exception/cancellation ใน finally ไม่ปล่อยผลเก่าทับของใหม่ Frontend ใช้ request generation เพิ่มจากรหัสในผลเพื่อกันคำขอเก่ากลับมาทับ เมื่อแก้คำตอบให้ล้างผลเก่าจากหน้าปัจจุบัน

## ความรับผิดชอบและจุดส่งต่อ

เรียกเจ้าของงานชั่วคราวเป็น A/B/C ยังไม่ใช่ชื่อสมาชิกจริง ทุกคนอ่าน intent/contracts ชุดเดียวกันก่อนเริ่ม ไม่เปลี่ยน field คนเดียวโดยไม่แจ้งอีกสองคน

| เจ้าของ | ส่งมอบหลัก | ไฟล์ที่เป็นเจ้าของในข้อเสนอ | ต้องส่งต่อให้อีกคน |
| --- | --- | --- | --- |
| A: Frontend / demo / review controller | upload, เลือก 1–3 SKU พร้อมภาพและคำแปล, คำถามหลายรอบ/skip/กู้หน้าเดิม, รายงาน/หลักฐาน/copy, loading/error/stale guard, linked fixture, คุมรีวิวและซ้อม restart | `frontend/` รวม fixture | ใช้ HTTP/IDs ตามตาราง ไม่ตีความข้อเท็จจริงหรือสร้าง supplier draft ใหม่ ส่ง fixture ชุดเดียวให้ B/C |
| B: Intake / integration | ตรวจ ZIP, static parse, 12 option mappings ของไฟล์จริง, Shared selector value, ภาพปลอดภัย, SQLite/state, route wiring และ ZIP สำเนาสองแบบสำหรับตรวจข้อมูลขาด | `backend/main.py` / `backend/intake.py` / `backend/state.py` พร้อมชุดทดสอบส่วนนี้ | snapshot/ภาพจาก input จริงให้ A และ immutable input/image bytes ให้ C รวมงานบนเครื่องเดโม |
| C: AI / output quality | Pydantic contracts, แปล/คำถาม/รายงาน, reference/source validation, source inventory ที่ส่งจริง, supplier draft และ API quality checks | `backend/contracts.py` / `backend/ai.py` / `backend/official_sources.json` พร้อมชุดทดสอบส่วนนี้ | คืน JSON ตาม contracts ให้ B เป็นผู้ประกอบ HTTP ไม่ให้ B/A สร้าง reasoning ทดแทนเมื่อ AI failed |

จุดเรียกภายในที่ต้องตกลงช่วงเริ่มงาน: B อ่าน archive เป็นข้อมูลดิบและ image map → C เตรียมเฉพาะ name_en/text_en โดยไม่เปลี่ยนรหัส/ต้นฉบับ → B publish snapshot จากนั้น B สร้าง QuestionInput/ReviewInput จาก state → C คืน QuestionResult/ReviewResult โดย B ตรวจ state ก่อนตอบ client ไม่มี framework สำหรับ dispatch เพิ่ม

ภาพที่ส่ง Responses API มาจาก bytes ใน private map เป็น base64 data URL พร้อมข้อความผูก source_id ไม่ใช้ URL ของ localhost เป็นรูปที่คาดว่า OpenAI จะดึงได้ ส่วน URL `/api/.../images/...` มีไว้ให้ browser เดโมเปิดหลักฐานเท่านั้น

C ส่งโครง schema และชื่อฟังก์ชันที่เรียกตรงกันภายใน 30 นาทีแรก A เป็นเจ้าของ linked fixture สร้างช่วง 0:00–0:30 จากตัวอย่าง linked-skip และ multi-round กับ snapshot ที่อ้างอยู่ ใช้ IDs/revision/issued prompts ที่เชื่อมกันในตัวอย่างและเก็บใน `frontend/src/fixtures/integration-flow.json` C ตรวจด้วย Pydantic เมื่อ schema พร้อมภายใน 0:30 ให้ frontend และ backend tests ใช้ไฟล์เดียว ไม่ใส่ fixture เข้า model prompt

ก่อน fixture/schema พร้อม A ทำโครงหน้าจอที่ไม่พึ่งข้อมูลได้ ไม่ตั้งสมมติฐานว่ามี fixture พร้อมตั้งแต่ 0:00 ชุด fixture มี 3 options ตามแหล่งเดิม ใช้ตรวจรูปร่าง/รหัสที่เชื่อมกัน ไม่ใช้พิสูจน์ semantic quality หรือการแสดง 12 options ซึ่งต้องใช้ ZIP จริง การตรวจ TS ยังต้องผ่าน type check และเส้นทางจริง ไม่ถือว่า Pydantic ตรวจ fixture แล้วพิสูจน์ TS ทั้งหมด

เริ่มวันงานด้วย Git repo เดียวและ baseline commit ของของที่มีอยู่ แยกงาน A/B/C จากจุดนั้นตามเจ้าของไฟล์ รวม schema ก่อนแล้ว intake/AI/frontend เป็นช่วงสั้น ผู้รวมงาน B รวมโค้ด ส่วน A คุม review controller ต้อง commit integrated state ที่ review และบันทึก SHA ของ base/target พร้อม staged/unstaged/untracked state ตาม workflow ไม่รีวิวเพียงชื่อ branch ที่ยังเปลี่ยน ไม่มีการสร้าง repo/push/PR ในรอบ Design นี้

ชุดอ้างอิงที่เสนอให้รุ่นแรกใช้เฉพาะ DMSC food-contact guidance ที่มี excerpt/ข้อจำกัดอยู่แล้วใน fixture ผู้จัดทำเปิดแหล่งทางการตรวจข้อความซ้ำวันที่ 18 พ.ย. แล้ว ดู [reference pack](reference-pack.md) C ตรวจ mapping/การส่งข้อมูลในวันงาน ไม่ต้องทำ research นี้ใหม่ในชั่วโมงแรก ไม่เพิ่มสรุปข้อบังคับ มอก. ที่ขอบข่ายยังไม่ยืนยัน หากแหล่งที่จำเป็นยังไม่ตรวจให้ coverage/limitation บอกตรง ๆ ไม่เติมกฎจากความจำโมเดล

## ตาราง 6 ชั่วโมง

เวลานับจากเริ่ม hacking การซ้อมแยกถึง PR-02 ยังไม่พิสูจน์เวลา build ทั้งเส้นทาง หากเริ่ม 10:00 ตาม [กำหนดการผู้จัด](https://luma.com/39zve8ph) ชั่วโมงที่ 6 คือ 16:00 และ code freeze จริงคือ 17:00 แผนหลักยังใช้ 6 ชั่วโมงตามที่ผู้ใช้กำหนด ชั่วโมงที่เหลือเป็นบัฟเฟอร์ ไม่ใช้ตั้งเป้าเพิ่มฟีเจอร์

| เวลา | A: Frontend / demo | B: Intake / integration | C: AI / quality | จุดที่ต้องเห็น |
| --- | --- | --- | --- | --- |
| 0:00–0:30 | ตั้ง frontend/types และสร้าง linked fixture เริ่มโครงหน้าจอระหว่างรอ schema | เริ่ม Git baseline/repo, ตั้ง backend/routes/state, เตรียม ZIP จริงและเครื่องเดโม | ส่ง schema/function names, ตรวจ fixture ของ A, ตรวจ key/model access + text/image/structured output | ทุกคนมี fixture/schema ที่เชื่อมกันและทราบว่า API ใช้บัญชีจริงได้หรือมี blocker |
| 0:30–1:30 | ต่อ relative API และตรวจหน้าเลือกจริงสองขนาด เขียน smoke steps | ตรวจ curated และ raw ZIP ตั้งแต่รอบ importer แรก ต่อ translation/ภาพ ลงคำสั่งเปิดแอป เริ่มทำ ZIP สำเนา T4/T5 | ทำ 3 ขั้นของ AI, schema และ ID/scope checks ก่อน ตรวจคำแปล raw ZIP จริง งาน quote/requirement/draft ต่อช่วงถัดไป | ทั้งสอง ZIP ได้ 12 SKU และภาพตรงกัน หน้าเลือกจาก raw ZIP มีคำแปลและค่าร่วม โดยไม่ preselect |
| 1:30–2:30 | ต่อคำถามหลายรอบ/review จริง รวม zero-question และ skip | ต่อ SQLite/revision/retry/stale guards รวมงาน และส่ง ZIP สำเนา T4/T5 ให้ C | เพิ่ม quote/requirement checks, supplier draft อ่านผลแรกแก้ prompt ทั่วไป | ZIP จริงผ่านอย่างน้อย 2 รอบถามถึงรายงานภายใน 2:30 และ ZIP ทดสอบข้อมูลขาดพร้อม ไม่ใช้ sample แทน AI |
| 2:30–3:30 | ตรวจ readability/evidence/copy ช่วยอ่านผล T1–T5 และแก้คำตอบ | ทดสอบ ZIP/path/image/input และ request เก่า | ตรวจ T1–T5 จริงอย่างละรอบก่อน ถ้าผ่านค่อยเพิ่ม repeat T1–T3 | ผล T1–T5 ต่างกันตามหลักฐาน/ความต้องการโดยไม่แต่งข้อมูล |
| 3:30–4:15 | ตรวจ 1–3 SKU/error และเตรียมเรื่องเล่าอังกฤษ จัด review brief/หลักฐาน | รัน seam/HTTP checks รวมและ commit state ให้ A ตรึง review | แก้ความผิดเชิงหลักฐาน Sol เฉพาะขั้นที่ยังผิด | 4:15 หยุดเพิ่มฟีเจอร์ มี integrated commit และ known failures สำหรับรีวิว |
| 4:15–5:15 | คุม Standards/Spec review controller เก็บ findings และประสานการตรวจแก้ | แก้ supported defects ส่วน backend และตรวจเฉพาะจุดที่กระทบ | แก้ supported defects ส่วน AI และตรวจความหมายที่กระทบ | รอบแรกและการแก้มีกรอบเวลา ข้อใหญ่ต้องมีเวลา re-review หรือระบุ pending ไม่อ้างว่าผ่าน |
| 5:15–6:00 | เป็นเจ้าของ restart run และซ้อมเดโมอังกฤษพร้อมหลักฐาน/ข้อจำกัด | ช่วยแก้ blocker ของเครื่องเดโม/ZIP/state และตรวจคำสั่งเปิด | ตรวจ API/semantic gates รอบท้าย เก็บ usage/time จริง | restart แล้วกู้เคสเดิม จากนั้นซ้อมเปิดจาก ZIP ใหม่ ไม่มีข้อผิดสำคัญที่ซ่อนไว้ |

ก่อนจบ 2:30 ถ้ายังเชื่อมครบไม่ได้ ให้หยุดงานแต่ง UI ที่ไม่จำเป็นและรวบรวมแก้เส้นทางที่ขาดก่อน ไม่ลดตัวเลือกจาก 1–3 เหลือ 1 ไม่ลดการเปิดหลักฐาน หรือแทนผลจริงด้วย sample โดยไม่ตกลงเปลี่ยน scope

## ชุดกรณีตรวจที่ใช้ร่วมกัน

รายการ T1–T5 นี้เป็นแหล่งเดียวของชื่อกรณีที่ใช้ใน integration gates และแผน AI ไม่ส่ง expected checks เหล่านี้ให้โมเดล

| รหัส | Input/การกระทำ | สิ่งที่ต้องได้ |
| --- | --- | --- |
| T1: sparse, skip และแตกแขนง | ZIP เดิม เลือก 3 SKU เช่น [v0, v1, v6] บริบทเพียงนำมาขายในไทย ลองจบก่อน/ข้าม แล้วตอบต้องสกรีนกับรับภาพแปะจากจุดเดิม ในแขนงสกรีนเปลี่ยนคำตอบรอบหลังจากสำเร็จรูปเท่านั้นเป็นยอมสั่งทำ | ค้นพบประเด็นลายเอง ไม่มี requirement จาก skip เห็นรอบถัดไปเปลี่ยนตามคำตอบอย่างน้อยหนึ่งครั้ง รายงาน/ข้อความผู้ขายเปลี่ยนตามเงื่อนไข โดยใช้หลักฐาน SKU เดิม ตัดรอบที่ถูกแทน |
| T2: explicit requirement | ZIP เดิม ต้องการสกรีน/720 ml/เครื่องดื่มร้อน ระบุใน context พร้อมบันทึก margin สมมติที่ห้ามส่งให้ผู้ขาย | ไม่ถามซ้ำข้อมูลที่ชัด ทดสอบ zero-question path เมื่อไม่มีรอบถาม ไม่ใช้เมนูปรับแต่งรับรอง SKU และไม่ใส่ margin ใน supplier questions |
| T3: changed options / accepted finish / cold use | ใช้ ZIP เดิม แต่เปลี่ยนชุดและลำดับจาก 3 SKU ใน T1 เป็น 2 SKU เช่น [v6, v1] ยอมรับภาพแปะหรือสกรีน ใช้กับเครื่องดื่มเย็น ไม่ต้องการความจุแน่นอน | A=v6 และ B=v1 ตามลำดับใหม่ ไม่ยืมคำ/ภาพของ v0 ที่ไม่ได้เลือก ไม่ขยายคำ 贴画 ของ B ไปยืนยัน A ไม่คง requirement สกรีนจากกรณีเก่า ไม่ถามเรื่องที่ตอบแล้วหรือถือว่าการยอมรับลายทำให้ทุกอย่างผ่าน |
| T4: report text removed / untrusted instruction | สำเนา ZIP ลบข้อมูลรายงานทดสอบออกจากทุกส่วนที่ importer รองรับจริง และใส่คำสั่งแฝงในข้อความสินค้าส่วนที่ importer อ่านอยู่แล้ว ใช้บริบท T3 | ไม่อ้างว่าผู้ขายระบุว่าไม่มีรายงานเมื่อข้อความนั้นไม่อยู่ในชุดที่อ่าน ระบุว่าไม่พบข้อมูลตามขอบเขตจริง และไม่ทำตามคำสั่งแฝงหรือเปลี่ยนเป็นคำรับรองสินค้า |
| T5: option image removed | สำเนา ZIP ลบเฉพาะไฟล์ภาพของ SKU หนึ่งที่เลือก แต่เก็บ option/ชื่อ/URI ไว้ ใช้บริบท T3 | รูปเป็น missing ยังใช้ข้อความต่อได้ ไม่อ้างว่าเห็นภาพที่หาย ไม่ยืมภาพ SKU อื่น และแจ้งข้อจำกัด |

v0/v1/v6 ในตารางเป็นชื่ออ้างอิงของตัวเลือกตาม fixture ให้จับคู่ชื่อจีนกับรหัสที่ importer สร้างจริง ไม่ hardcode IDs เหล่านี้ในแอป T4/T5 ใช้ชุดตัวเลือกและลำดับเดียวกับ T3 เพื่อเปรียบเทียบผลเมื่อเปลี่ยนเฉพาะหลักฐาน ไม่เพิ่มจำนวนเคสหรือ model calls จากการระบุ T3 ให้ชัดขึ้น

B เป็นเจ้าของ ZIP ต้นทางและสำเนา T4/T5 ต้องส่งให้ C ภายใน 2:30 โดยไม่แก้ไฟล์ต้นฉบับ เก็บคำอธิบายสิ่งที่เปลี่ยนและตรวจว่า parser ไม่มีแหล่งซ้ำใน structured JSON ที่ทำให้ข้อมูลรายงานยังอยู่ อย่าลบแค่ข้อความแสดงผลแต่เหลือหลักฐานอีกชุดแล้วคาดว่าโมเดลต้องบอกไม่พบ ใน T4 ต้องตรวจว่าข้อความคำสั่งแฝงอยู่ในชุดที่ส่งให้โมเดลจริง หาก importer ตัดออกจะพิสูจน์ได้เพียงการกรองข้อมูล ไม่ใช่การต้านคำสั่งแฝงของโมเดล

T1–T5 เป็น gate ขั้นต้น รันจริงอย่างละหนึ่งเส้นทาง และตรวจคู่แขนงใน T1 ตามตาราง ส่วนการ repeat T1–T3 เพิ่มอีกอย่างละ 2 เป็นเป้าตรวจความสม่ำเสมอเมื่อ gate แรกผ่านและเวลาพอ ต้องบันทึกจำนวนที่รันจริง แยกรอบซ้ำจากจำนวนแขนงของ T1 และไม่ใช้ตัวอย่างน้อยนี้อ้าง accuracy การตรวจ pure HTTP ของ zero-question/partial answers/error ยังต้องทำ แม้โมเดลรอบหนึ่งจะไม่ได้สร้าง path นั้น

## ตรวจอย่างไรจึงนับว่าเสร็จ

| ผู้รับผิดชอบ | หลักฐานที่ต้องได้ |
| --- | --- |
| B | สร้าง ZIP จาก saved HTML/assets จริงโดยไม่แก้ต้นฉบับ รับได้ 12 options/mappings/ค่าร่วมตามข้อมูล ไม่ hardcode จำนวนเป็นเงื่อนไข parser ปฏิเสธ unsafe paths/limits/ambiguous layout และแยกรูป missing/unreadable |
| C | แปลตรงบริบท คำถามสูงสุด 3 รอบ รอบละ 5 ข้อ ไม่ถามซ้ำข้อมูลที่ชัดและไม่ถาม buyer ให้เดาข้อเท็จจริง quote/ID/scope/unknowns/requirement refs/coverage/draft privacy ถูก ไม่มี safe/buy verdict |
| A | เปิดภาพตาม SKU จริง เลือกสูงสุด 3, skip/partial/zero questions ใช้ได้, อังกฤษอ่านรู้เรื่อง, copy ไม่เปลี่ยน findings, แก้คำตอบไม่เรียก translation ใหม่และใช้กติกาเพดาน/การตัดรอบ |
| A+B | หน่วงผลเก่าแล้วเปลี่ยน ZIP/options/context/answers ผลเก่าทับไม่ได้ body ที่ส่ง snapshot/image path หรือใช้ operation_id เดิมกับ payload ต่างกันถูกปฏิเสธ |
| B+C | Duplicate งานที่ running ไม่ทำ paid call ซ้ำ ready retry ไม่เรียกใหม่ timeout/quota/invalid output ไม่กลายเป็นผลพร้อมใช้ คำตอบผู้ซื้อยังอยู่สำหรับ retry |
| A+B | Restart หลังส่งหนึ่งรอบและเมื่อ AI ค้าง กู้เคส/คำตอบ/รูปตรง SKU แล้ว continue หรือ Retry ได้ ไม่ยิง AI ตอนโหลด เมื่อเริ่ม ZIP ใหม่ เคสและรูปเดิมใช้ไม่ได้ |
| ทั้งทีม | ผ่าน T1–T5 ตามรายการเดียวกันด้วยผลจริงที่อ่านเทียบหลักฐาน ไม่เพียง fixture/JSON parser ผ่าน และบอกจำนวน repeat ที่ทำได้จริง |

ใช้ fixture จำลองเฉพาะช่วงพัฒนา/ทดสอบโครงสร้างและติดป้ายให้ชัด ต้องทำ fixture ที่ source/case/question wording เชื่อมกันจริง ไม่ต่อไฟล์ตัวอย่างอิสระของแต่ละ contract แล้วถือว่าเป็น flow เดียว ห้ามส่ง expected report หรือ contract prose เฉลยเข้า model prompt และห้าม fallback เป็น fixture เมื่อ API จริงผิด

วิธีติดตาม ticket ใช้ shared integration review, no early ticket closure ตาม [breakdown](team-plan.md): PR-01–03 ส่งต่อเมื่อ implemented + seam-checked ได้ก่อน แต่ยังไม่ complete ส่วน PR-04 เริ่ม harness/กรณีตรวจ/wiring ได้เองและมี gate รวมจากสามส่วนที่ส่งแล้วภายใน 2:30 ไม่รอปิดสาม ticket จนวนกับ review ที่อยู่ใน PR-04

ก่อนพร้อมส่งงาน A ใช้ code-review controller เดียวให้ fresh Standards และ Spec reviewers ตรวจ integrated commit ที่ตรึงเวลา 4:15 ตาม workflow Build โดย briefs/results/dispositions/ledger ครอบคลุม acceptance และ final state ของ PR-01–04 ทุกใบ ยึด reviewer/model route ที่กำหนด การรีวิวเอกสารไม่แทนการรีวิวโค้ดจริง รอบแรกแก้ข้อเล็กแล้วตรวจเฉพาะจุด ข้อใหญ่/เกี่ยวพันหลายส่วนต้อง full re-review ได้อีกหนึ่งรอบถ้าจบก่อน 5:15 ภายใต้เพดานสองรอบเดิมของทุก ticket ที่ได้รับผล ไม่เริ่มนับใหม่เพราะย้าย defect ไปอีกใบ

ปิดแต่ละ ticket ได้เมื่อ checks/review ครอบคลุม final state และเกณฑ์ของใบนั้น ไม่ปิด PR-01–03 จาก seam checks เพียงอย่างเดียว และไม่ถือว่ารีวิว PR-04 อย่างเดียวเท่ากับตรวจใบอื่นแล้ว

กรอบเวลานี้ไม่ใช่ข้อยกเว้นให้ข้าม required review/check หาก route ใช้ไม่ได้หรือข้อใหญ่ยังไม่ผ่านเมื่อหมดเวลาให้ระบุ pending และเส้นทางที่ยังไม่พร้อม ไม่อ้างว่า core ผ่านครบเพื่อให้ทันเดโม ชั่วโมงที่ 6 จบก่อน code freeze ของผู้จัด แต่ไม่ใช้เวลานอกแผนเป็นข้ออ้างเพิ่มรอบรีวิวไม่จำกัด

## สิ่งที่ตัดก่อนเพื่อรักษางานหลัก

ตัด deploy, seller-reply loop, live search/scrape, detail-image selection, login/ประวัติหลายเคส, free-form chat, scoring, animations และ multi-model routing อัตโนมัติก่อน งานที่ตัดไม่ได้คือ trust-boundary validation, 1–3 SKU, contextual translation, คำถามปรับตามคำตอบและกู้เคสล่าสุด, หลักฐานตรง SKU, unknowns และการไม่แสดงผลเก่า/ผลล้มเหลวเป็นสำเร็จ

ถ้าก่อนหมดเวลายังมี known defect ที่ทำให้แสดงหลักฐานผิด SKU หรือสรุปสิ่งที่ไม่มีหลักฐาน ให้แก้หรือรายงานว่าเส้นทางนั้นยังไม่พร้อม ไม่อ้างว่ารุ่นหลักสมบูรณ์ หากเตรียมวิดีโอสำรอง ให้บันทึกจากแอปที่รันจริงในวันงานและบอกว่าเป็นวิดีโอ ไม่เล่นผลค้างภายในแอปเสมือนเป็นการวิเคราะห์สด

