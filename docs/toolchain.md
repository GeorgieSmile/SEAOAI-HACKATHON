# Toolchain และการเริ่มงาน

Phase 1 ใช้ React/TypeScript/Vite และ Python/FastAPI ตาม [integration plan](integration-plan.md) ดูคำสั่งติดตั้งและเปิดแอปจาก root ใน [README](../README.md)

เวอร์ชันที่ติดตั้งใน venv ของ Phase 1 บน macOS ARM/Python 3.15.2

| Package | Version |
| --- | --- |
| fastapi | 0.163.2 |
| uvicorn | 0.54.0 |
| openai | 3.55.0 |
| pydantic | 2.14.3 |
| beautifulsoup4 | 4.15.0 |
| pillow | 13.0.0 |
| python-multipart | 0.0.34 |
| httpx | 0.28.1 |
| pytest | 9.2.0 |

Node ที่รายงานในเครื่องต้นทางคือ 26.13.1/npm 11.20.0 ไม่ใช่การกำหนดว่าทุกคนต้องใช้เวอร์ชันนี้ ใช้ package-lock.json ของ frontend เมื่อติดตั้งบนเครื่องอื่น

## 30 นาทีแรกในวันพัฒนา

1. B กำหนดเครื่องเดโม/เริ่ม Git baseline และตั้ง backend loopback ตาม integration plan
2. C ส่ง schema/function names ตรวจบัญชี/เครดิต/model access กับ text/image/structured output จริง และบันทึก model/config/usage ที่ใช้
3. A ตั้งหน้าเว็บและสร้าง linked fixture ที่รหัส/prompt เชื่อมกัน ให้ C ตรวจเมื่อ schema พร้อม

ทีมจัด API key ใน environment ของ backend ที่เลือกใช้เมื่อสร้างแอป ตัวแปรมาตรฐานคือ OPENAI_API_KEY ไม่ใส่ key ใน frontend/fixtures/log/repo

ตรึง dependency versions ใน repo ใหม่เมื่อทดสอบกับเครื่องเดโมแล้ว ผลติดตั้งเดิมไม่รับรองว่า API access/latency/ต้นทุน/คุณภาพรายงานผ่าน
