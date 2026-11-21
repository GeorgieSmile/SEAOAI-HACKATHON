# รับ ZIP ของหน้าสินค้า TTP Cargo: ข้อตกลง Design

Status: accepted design under spec v0.16; implementation and runtime unverified
Version: 0.5
วันที่: 19 พ.ย. 2569
ต้นทาง: [intent](../brief.md), [spec](../spec.md), [ข้อมูล snapshot/รายงาน](report-contract.md), [รอบคำถาม](question-contract.md)

ยอมรับเป็นส่วนหนึ่งของ [spec v0.15](../spec.md) วันที่ 19 พ.ย. ตาม [บันทึกการยอมรับ](../acceptance.md) การยอมรับ Design ไม่ใช่หลักฐานว่าแอปทำงานแล้วหรือคำสั่ง Build

## Link import on the Phase 2 branch

The default input is an HTTPS product link at `taobao.ttpcargo.com/product` with exactly `provider=1688` and a positive decimal `itemId` of up to 18 digits, in either order. ZIP remains available for offline use and upstream failures. Both inputs return the same snapshot and require explicit SKU selection.

For link imports the backend sends only the provider and item ID with the public product-detail action to the fixed endpoint used by TTP Cargo. It checks the returned product ID, parses the product fragment as static HTML, and retrieves only mapped SKU thumbnails from the supported CDN path. Redirects are disabled. The HTML, image and total download limits are 5, 10 and 50 MiB respectively. Missing or unsupported images remain scoped to their SKU. The ZIP path continues to resolve only local archive assets.

## ผลที่ต้องส่งมอบ

รับไฟล์ ZIP ที่ผู้ซื้อบันทึกจากหน้า TTP Cargo แล้วคืน ListingSnapshot ให้เลือกสินค้าเป็นอังกฤษ พร้อมต้นฉบับและรูปที่ผูกกับตัวเลือกจริง จากนั้นผู้ซื้อยืนยัน 1–3 ตัวเลือกและบริบทก่อนเตรียมคำถาม ไม่เริ่มวิเคราะห์จากค่า checked ที่ติดมากับหน้า

เป็น Module รับหลักฐานเดิม ซ่อนการอ่าน archive/HTML และการเตรียมคำแปลไว้หลัง Interface เดียว ไม่เปิดเว็บ ไม่รัน JavaScript และไม่ติดตั้ง browser เพื่ออ่านไฟล์ รุ่นหลักไม่รับหลายสินค้า/หลายหน้าใน archive เดียว

## ข้อมูลตัวอย่างที่มากับ starter

ใช้ [samples/listing.zip](../../samples/listing.zip) ซึ่งเป็นสำเนา product-only ที่สร้างจากหน้าจริง โดยคง #ttp-product, h1, 12 parent SKU, child 720ml และคู่คุณสมบัติ 40 แถว ตัด executable scripts/admin/debug/User IP และส่วนอื่นนอกสินค้า เปลี่ยนเพียง path ภาพเป็น listing_files/ พร้อมไฟล์ local 45 รูป ภาพ SKU ทั้ง 12 ยังเป็น 150×150 ไม่ได้ fetch ภาพเต็ม

[expected-inventory](../../samples/expected-inventory.json) เป็นผลสำรวจต้นทางสำหรับตรวจ ไม่ใช่คำตอบ AI หรือ source ที่แอปรับแทน ZIP ส่วน [provenance](../../samples/provenance.json) ระบุการตัดข้อมูลและ hashes ชื่อ SKU/คุณสมบัติ/bytes ภาพเทียบกับต้นฉบับแล้ว แต่สำเนานี้ไม่ใช่ full browser save ที่เหมือนเดิมทุก byte การผ่าน sample นี้ยังไม่ยืนยันว่า parser รองรับทุก saved page ของเว็บ

ตรวจ [raw browser save ที่ลบข้อมูลส่วนตัวแล้ว](../../samples/raw-save/raw-save-redacted.zip) ควบคู่กับ product-only ตั้งแต่ PR-01 ไฟล์มาจาก Save Page As แบบ complete แล้ว ZIP ด้วย Finder [ที่มาและรายละเอียด](../../samples/raw-save/PROVENANCE.md) ใช้ inventory เดียวกันเทียบชื่อ SKU ลำดับ ความสัมพันธ์ และ hash รูป เพราะ path ในสอง ZIP ต่างกัน

หลักฐานความสัมพันธ์คงอยู่ใน JSON บล็อก `script[type="application/json"]#source-sku-map` ที่มี `{skuMap: ...}` คัดจาก AliProductInfo.skuMap ต้นฉบับโดยตรง ให้ parse JSON และเทียบ keys ที่ decode &gt; กับ parent/child ของ DOM ก่อนจัด Shared selector value ถ้าไม่ตรงให้ unsupported_layout ไม่ใช้ inventory หรือการมี child ค่าเดียวเป็นข้อพิสูจน์ และไม่รัน JavaScript

## รูปแบบ archive ที่รองรับก่อน

- ZIP มีหน้าสินค้า HTML หลักหนึ่งไฟล์กับไฟล์ประกอบที่อ้างผ่าน path ภายใน archive รองรับโฟลเดอร์ครอบหนึ่งชั้น เช่นไฟล์ที่ ZIP จาก Finder ไม่กำหนดชื่อ HTML หรือชื่อภาษาจีนตายตัว
- เลือก main HTML จากไฟล์นอกโฟลเดอร์ assets ของ saved page เช่น *_files ไม่เลือก iframe HTML ใน assets หรือ __MACOSX เป็นหน้าสินค้า ถ้ามีหลาย main HTML หรือระบุไม่ได้ ให้หยุดและบอกให้ ZIP หน้าเดียว
- รองรับ UTF-8 HTML ตามตัวอย่างและชื่อไฟล์ที่อ่านเป็น Unicode ได้ หากอ่านชื่อ/encoding ไม่ได้ชัดเจน ให้แจ้ง unsupported แทนการเดาจนผูกภาพผิด
- รองรับชื่อไฟล์แบบ Finder ที่เป็น UTF-8 แต่ไม่ได้ตั้ง UTF-8 flag โดยรับเมื่อ bytes ถอดเป็น UTF-8 ได้อย่างเคร่งครัด แล้วตรวจ path และชื่อชนหลัง normalize ตามกฎเดิม นับ Finder metadata รวมในเพดาน archive แม้จะข้ามตอนอ่านข้อมูลสินค้า
- ข้อจำกัดที่เสนอสำหรับ 6 ชั่วโมง: ตัวเลือกหนึ่งแกนหลัก และแกนรองที่มีค่าร่วมเดียวแบบตัวอย่าง หากมีหลายค่าของแกนรองหรือ mapping ชุด SKU กำกวม ให้หยุด unsupported_layout ไม่เอาค่า checked มาตัดสินแทน ผู้ซื้อเห็นค่าร่วม เช่น 720ml ก่อนยืนยันตัวเลือก ไม่ถือค่านั้นเป็น requirement ว่าผู้ซื้อต้องการ 720 ml
- รองรับโครงสร้างหน้าแบบที่ตรวจพบ ไม่ผูกกับ item ID หรือชื่อกระติกนี้ หากโครงสร้างเว็บเปลี่ยนให้รายงาน unsupported_layout ไม่คืนคำตอบจาก sample เดิม

ข้อจำกัดแกน SKU นี้เป็นขอบเขต importer ที่ยอมรับเพื่อคุมเวลางานภายใต้ spec v0.16 ไม่อ้างว่ารองรับสินค้าทุกแบบของเว็บ

## การอ่านและจัดหลักฐาน

1. ตรวจ archive และเพดานทรัพยากรก่อน อ่าน entry โดยไม่ extract ทั้งชุดลง directory ที่ผู้ใช้กำหนด ไม่รันหรือ serve archive HTML/JS/CSS กลับไปยัง browser
2. อ่านชื่อสินค้า ตัวเลือก และคู่คุณสมบัติจากบริเวณสินค้าที่รู้จัก เก็บชื่อสินค้าเป็น title และ listing_text source label `Product title` พร้อม original_text เดียวกัน เพื่อใส่ text_en สำหรับหน้าอังกฤษ รักษาชื่อจีน/ข้อความเดิมไว้ เก็บ SKU scope ของแต่ละชิ้น ไม่ให้รายละเอียดทั่วไปยืนยันคุณสมบัติเฉพาะ SKU
หากมีค่ารองร่วม เช่น 720ml ต้องมีหลักฐานความสัมพันธ์ว่าค่านั้นใช้กับตัวเลือกใดจริง จัดเป็น listing_text source label คงที่ `Shared selector value` และ option_ids ครบทุกรหัสที่ค่านั้นผูกไว้ Frontend แสดง source นี้ก่อนยืนยัน ไม่ค้นหาความจุจากคำแปล/label ทั่วไป หากระบุความสัมพันธ์ไม่ได้ให้ unsupported_layout ไม่ถือเพียงมี input รองหนึ่งตัวว่าเป็นข้อพิสูจน์ทุกชุด

3. อ่านเฉพาะข้อมูลที่อนุญาตจาก structured JSON ที่ระบุบริเวณได้และ parse เป็นข้อมูลได้โดยตรง หากจำเป็น หากใช้ JSON ให้รหัส/ความสัมพันธ์สอดคล้องกับ DOM มิฉะนั้น unsupported_layout ไม่ eval script/JavaScript assignment และไม่ส่ง admin/debug block ทั้งก้อน ข้อมูลผู้ใช้ โค้ดติดตาม และคำสั่งที่แทรกในหน้าไม่ใช่คำสั่งระบบ

   สำหรับ raw save ที่ไม่มีบล็อก `#source-sku-map` ให้อ่านเฉพาะ JSON object ของ `skuMap` ในข้อความ script ภายใน `#ttp-product` แบบ static ถ้ามีหลายสำเนาให้ใช้ได้เมื่อข้อมูลตรงกันและเทียบกับ DOM ได้ ถ้าอ่านไม่ได้ ขัดกัน หรือจับคู่ไม่ชัด ให้คืน `unsupported_layout` โดยเก็บ debug และ script นอก payload แปล
4. จับคู่รูปจากความสัมพันธ์ SKU กับ img ใน DOM แล้ว resolve path เทียบกับ main HTML ใช้ URL decoding อย่างระวังและตรวจ containment หลัง normalize อีกรอบ ไม่เดารูปจากสี ตำแหน่งในรายชื่อ หรือชื่อรูปที่คล้ายกัน
5. ใช้เฉพาะไฟล์ local ที่มีใน archive URL ภายนอกและ data placeholder ไม่แทนรูปที่หาย ไม่ fetch เว็บเพิ่ม หากต้นทางระบุรูปเฉพาะแต่ไฟล์ไม่อยู่ใน archive ให้คง image_source_id ที่ชี้ source สถานะ missing และ image_file: null ส่วนรูปที่มีแต่ไม่ผ่านตรวจให้ unreadable โดยไม่เสิร์ฟ bytes ใช้ image_source_id: null เฉพาะเมื่อไม่พบความสัมพันธ์กับรูปเฉพาะเลย พร้อมบอกข้อจำกัด รูปสินค้ารวมใช้ได้เมื่อมีป้ายชัดว่าเป็นภาพรวม
ภาพตัวเลือกของเคสปัจจุบันเป็น 150×150 thumbnail ต้องใส่ limitation เรื่องความละเอียด แม้จับคู่ไฟล์ได้ถูกต้อง รูปไม่ยืนยันกระบวนการตกแต่งหรือความทนทานไม่ว่าความละเอียดเท่าใด ข้อความหรือค่าจากภาพใช้ได้เฉพาะเมื่ออ่านได้ชัดในภาพนั้นจริงและระบุว่าเป็นข้อความในภาพ ไม่เท่ากับผลวัดสินค้าจริง หากอ่านไม่ชัดให้คง unknown ไม่ขยายข้อสรุปไปทุกตัวเลือก

6. ภาพรองรับ JPEG/PNG/WebP ที่ decoder ตรวจได้จริง ตรวจชนิดจากข้อมูลและขนาด pixels ก่อน decode เต็ม ไม่เชื่อ extension อย่างเดียว SVG, GIF/animation และชนิดอื่นไม่รันหรือแสดง raw ให้ระบุ Source.availability เป็น unreadable และ limitation ว่าไม่รองรับรูปชนิดนั้น ไม่ทำให้ชื่อ SKU ที่อ่านได้กลายเป็นเดาไม่ได้ทันที
7. เตรียม text_en ให้ข้อมูลที่ใช้เลือก/แสดงหลักฐาน แล้วใช้คำแปล source ชื่อ SKU เต็มเดียวกันเป็น name_en ตาม report-contract โดยรักษาขอบเขตต้นฉบับ ไม่แปล 贴画 เป็น screen printing หรือเปลี่ยน customization menu เป็นคุณสมบัติ SKU หากแปลไม่ได้ใช้ null กับ limitation ตาม report-contract ไม่แต่งคำแปล และไม่เริ่มถามก่อนผู้ซื้อยืนยันตัวเลือก
8. เพิ่มเอกสารทางการที่ระบบคัดไว้เป็น official_guidance แยกจากไฟล์ listing ผู้ซื้อ ไม่ใช้ CSS/JS/HTML ที่มากับ assets เป็นเอกสารอ้างอิงภายนอก
9. สร้าง snapshot_id ใหม่ต่อชุดนำเข้าที่สำเร็จ รหัส option/source คงที่ใน snapshot เดียวกัน ห้ามย้าย ID จาก sample มาแทนของใหม่ ตัวเลือกหนึ่งรหัสแทนชุดที่จะซื้อที่ระบุได้แน่ชัด ไม่เปลี่ยน identity ตามคำแปล

available ของ Source หมายถึงอ่านข้อมูลข้อความได้หรือเปิดภาพด้วย decoder ได้ ไม่ได้หมายความว่าขั้นเตรียมคำถามหรือรายงานอ่านภาพแล้ว Backend ต้องตรวจว่าการอ้างอิงอยู่ในชุดที่ส่งให้ขั้นวิเคราะห์นั้นจริง การส่งไฟล์ไม่พิสูจน์ว่าตีความถูก ยังต้องตรวจความหมาย และในขั้นคำถามห้ามบรรยายข้อมูลจากภาพตาม question-contract ไม่เพิ่ม field verified ให้ snapshot

## เพดานที่เสนอ

เป็นค่าเริ่มต้นด้านทรัพยากรจากขนาดตัวอย่าง ยังไม่ใช่ผลทดสอบเวลา/โหลด และปรับลงได้ก่อน Build หากสภาพเครื่องไม่พอ

| รายการ | เพดานเริ่มต้น |
| --- | --- |
| ZIP upload | 20 MiB |
| จำนวน entries ทั้งไฟล์และ directory | 1,000 |
| ผลรวมขนาดคลาย archive | 50 MiB |
| main HTML | 5 MiB |
| ภาพต่อไฟล์ที่อ่าน | 10 MiB และ 25 ล้าน pixels |

เพดานภาพรายไฟล์/จำนวน pixels ที่เกินทำให้รูปนั้น unreadable พร้อม limitation ไม่ทำให้ import ทั้งชุด failed หากยังระบุสินค้าและตัวเลือกได้ ส่วนเพดาน ZIP, entries, ผลรวมคลาย และ HTML ทำให้ failed/archive_limit แม้สาเหตุคือไฟล์รูป

ตรวจทั้ง metadata และจำนวน bytes ที่อ่านจริงในขณะคลาย ไม่อาศัยค่า uncompressed size ใน header เพียงอย่างเดียว ไม่อ่าน nested archive และไม่โหลดทุก asset เข้าโมเดลเพราะอยู่ใน ZIP งบจำนวนรูป/ขนาดที่ส่งโมเดลเป็น Implementation ของการวิเคราะห์ซึ่งยังต้องวัด

ขนาดไฟล์อย่างเดียวคุมเวลาและหน่วยความจำของ parser ไม่พอ ให้ B กำหนดและบันทึกเพดานงานต่อครั้งสำหรับโครงสร้าง HTML (รวมขนาด tag), จำนวนตัวเลือก/แถวที่ประมวลผล และจำนวน/ขนาดหรือความลึกของ JSON candidates เลี่ยงการวนอ่าน subtree หรือข้อมูลเดิมซ้ำต่อ SKU และจำกัดการ parse ให้ทำครั้งละหนึ่งงานแม้ request ถูกยกเลิก ตรวจเพดานก่อนงานที่ใช้ทรัพยากร และวัดเวลา/peak memory บนเครื่องเดโมแยกจากการรอ API เมื่อเกินเพดานงานให้หยุดด้วย `archive_limit` ก่อนแปลหรือ publish ผล

ปฏิเสธ archive ที่เข้ารหัส เสียหาย ใช้วิธีบีบอัดนอกชุดที่รองรับ หรือมี path ไม่ปลอดภัย เช่น absolute path, drive path, .., symlink หรือชื่อชนกันหลัง normalize ไม่แก้โดยเขียนทับไฟล์ entry อื่น อนุญาต ZIP_STORED/ZIP_DEFLATED เป็นขอบเขตเริ่มต้น ตรวจชื่อก่อนข้ามไฟล์ประกอบที่ไม่ใช้ เช่น .DS_Store/__MACOSX

[Python zipfile documentation](https://docs.python.org/3/library/zipfile.html#decompression-pitfalls) ระบุความเสี่ยงจากการคลายไฟล์และการใช้ทรัพยากร ข้อมูลนี้สนับสนุนการตรวจ archive โดยยังไม่มีโค้ด importer ให้พิสูจน์ความปลอดภัย

## Interface และสถานะ

- รับ: bytes ของ ZIP กับชื่อไฟล์สำหรับแสดงผล ไม่ใช้ชื่อ upload เป็น path บนเครื่อง
- สำเร็จ: `{status: "ready", snapshot: ListingSnapshot}` ตาม report-contract แม้มีข้อจำกัดบางส่วนได้ แต่ต้องมีชื่อ/identity สินค้าและตัวเลือกที่อ่าน/แยกได้อย่างน้อยหนึ่งแบบ
- ไม่ได้ชุดข้อมูลที่ใช้ได้: `{status: "failed", code, message}` ไม่มี snapshot สำหรับเลือกสินค้า code เป็น invalid_archive, archive_limit, unsupported_layout, product_unreadable หรือ intake_failed
- Backend เป็นเจ้าของ snapshot ต้นฉบับของเคสปัจจุบัน Frontend ให้เลือกจาก options ของ snapshot นี้เท่านั้น แล้วส่ง snapshot_id, selected_option_ids และ buyer_context เพื่อเริ่มเคส Backend โหลด snapshot ของตนเอง ไม่รับ snapshot/sources/image_file ที่ client ส่งกลับมาเป็นข้อมูลต้นทาง ไม่อ้างว่าผล intake คือรายงานตรวจความเสี่ยง

unknowns ของรายงานใช้ Source ที่ missing/unreadable อธิบายข้อจำกัดได้ แต่ citations และตารางเปรียบเทียบห้ามใช้แหล่งเหล่านั้นยืนยันเนื้อหา ตาม report-contract หาก reference ภายนอกชี้รูปที่ไม่ได้บันทึกและไม่มี Source ที่อ่านได้ ให้ limitation ระบุว่ารูปนั้นไม่มากับ archive ไม่อ้างว่าอ่านแล้ว

หากแปลล้มเหลวจาก timeout/output/validation หรือ rate limit ชั่วคราว/5xx ที่ retry ครบหรือ deadline หมดแล้ว ภายในเพดาน attempts ที่อนุญาต ให้ snapshot พร้อมใช้ด้วย name_en/text_en: null และ limitation แต่คงต้นฉบับ หากเป็นข้อผิดพลาดตั้งค่า/สิทธิ์/โควตาที่ต้องหยุดตาม ai-execution-plan ให้ failed/intake_failed ไม่ publish snapshot เสมือนบริการพร้อมใช้

เก็บเฉพาะข้อความสินค้าและภาพที่จำเป็นในพื้นที่ SQLite ของเคสล่าสุดที่ Backend ควบคุม ไม่ส่ง absolute path ของเครื่องให้ Frontend หรือโมเดล image_file เป็น opaque locator ภายใน snapshot และเสิร์ฟเฉพาะภาพที่ตรวจชนิดแล้ว ไม่เปิด directory browsing หรือ raw archive HTML วิธีอายุข้อมูลชั่วคราว/เสิร์ฟภาพอยู่ใน [integration-plan](../integration-plan.md) ไม่เก็บ archive ดิบหรือประวัติหลายเคส

## กรณีที่ต้องยืนยันตอน Build

| กรณี | ผลที่ต้องได้ |
| --- | --- |
| ไฟล์ตัวอย่างครบ | แสดง 12 ตัวเลือกพร้อมชื่อจีน/คำแปลและรูปตรงกัน แสดงค่าร่วม 720ml แต่ไม่เลือกแทนผู้ซื้อ |
| เปลี่ยนชื่อ HTML/มีโฟลเดอร์ครอบ | อ่านได้หาก local references ยัง resolve ถูก ไม่ค้นตามชื่อไฟล์เคสเดโม |
| รูปตัวเลือกหายหรือเป็น remote URL | ยังเลือกจากข้อความได้โดยแจ้งรูปหาย ไม่โหลดภายนอกหรือยืมรูป SKU อื่น |
| รูปมีอยู่แต่ decode ไม่ได้/ชนิดไม่รองรับ/เกินเพดานภาพ | รูปนั้น unreadable พร้อม limitation ใช้ข้อมูลที่เหลือต่อได้หากไม่เกินเพดานรวม archive |
| รูปตัวเลือกเป็น thumbnail 150×150 | แสดงข้อจำกัด ไม่สรุปวิธีผลิตลาย ข้อความที่อ่านไม่ชัดยังเป็น unknown และไม่ถือข้อความความจุในภาพเป็นผลวัดจริง |
| ลบข้อความรายงานทดสอบออกจากสำเนา | ไม่สร้าง source ว่าผู้ขายระบุว่าไม่มีรายงานจากผลเก่า |
| มีคำสั่งแฝงในหน้า | ถือเป็นข้อมูลที่ไม่ควรปฏิบัติตาม ไม่รันหรือเปลี่ยนกฎระบบ |
| มีหลาย HTML หลัก/หลายค่ารองที่ระบุชุดไม่ได้ | unsupported_layout ไม่เดา item/SKU |
| ZIP ใหญ่/เกินเพดาน/path อันตราย/ไฟล์เสีย | failed ไม่มี snapshot บางส่วนที่แสดงเป็นสำเร็จ |
| เปลี่ยน ZIP ระหว่างวิเคราะห์ | snapshot/case ใหม่ ผลและรูปของชุดเก่าไม่แทนชุดใหม่ |

