# ANC Broker — MySQL Database Dictionary

เอกสารนี้อธิบาย **4 ตาราง / 73 คอลัมน์** จากคำสั่ง `CREATE TABLE IF NOT EXISTS` ใน `Database.initialize()` ของ `anc_collector.py` (v4.1) บน Repository `firmeen/anc-broker` ไม่ได้อ้างว่า Schema ของ MySQL ที่เคยสร้างไว้บนทุกเครื่องต้องเหมือนกันเสมอไป เพราะ `CREATE TABLE IF NOT EXISTS` **ไม่เปลี่ยนตารางเก่าที่มีอยู่แล้ว** ตรวจ Schema ที่ใช้จริงด้วย `SHOW CREATE TABLE` หรือ `SHOW COLUMNS` บนฐานข้อมูลนั้น

**Database:** MySQL (`DB_NAME` ใน `.env`; ตัวอย่าง `tqm`, fallback ใน Python `dbd`) **Engine:** `InnoDB`, **Charset:** `utf8mb4`, **Collation:** `utf8mb4_unicode_ci`

## สรุปตาราง

| Table | Columns | ข้อมูล |
|---|---:|---|
| `anc_competitor_insurance_v4` | 42 | ข้อเสนอประกัน/Detail/Raw |
| `anc_competitor_target_v4` | 12 | สถานะจังหวัดและรถยนต์เป้าหมาย |
| `anc_competitor_branch_v4` | 13 | สถานะรุ่นย่อยและประเภทซ่อม |
| `anc_competitor_package_checkpoint_v4` | 6 | Checkpoint แพ็กเกจ |

**วิธีอ่าน Data Dictionary:** `YES` = รับ `NULL` ได้, `NO` = ต้องมีค่า; `—` ใน Default = ไม่มี SQL `DEFAULT` ที่ระบุไว้; `NULL (implicit)` = ฟิลด์ที่รับ NULL โดยไม่ได้กำหนดค่าเริ่มต้นอื่น; `INDEX ... (1/2/3)` = ตำแหน่งคอลัมน์ใน Composite Index; การอธิบายคำว่า PRIMARY/UNIQUE/INDEX อยู่ท้ายเอกสาร

## 1. `anc_competitor_insurance_v4` — 42 คอลัมน์

**หน้าที่:** ตารางหลักของข้อเสนอประกันภัยแต่ละแพ็กเกจที่เก็บจาก Listing และรายละเอียดที่เข้าถึงได้

**ใน Workflow:** แถวหนึ่งคือข้อเสนอที่ระบบระบุด้วย `record_key`; รายละเอียดอาจยังรอหรือไม่พร้อมให้เรียก

| Column | MySQL Data Type | Nullable | Default | Key / Index | Description |
|---|---|---|---|---|---|
| `id` | `BIGINT UNSIGNED` | NO | AUTO_INCREMENT | PRIMARY | เลขลำดับอัตโนมัติของแถว |
| `record_key` | `CHAR(64)` | NO | — | UNIQUE uq_record_key | รหัสเอกลักษณ์ของรายการข้อเสนอ ใช้คู่กับ Checkpoint |
| `branch_key` | `CHAR(64)` | NO | — | INDEX idx_branch | รหัสงานรุ่นย่อยและประเภทซ่อม |
| `target_key` | `CHAR(64)` | NO | — | INDEX idx_target | รหัสเป้าหมายจังหวัดและรถยนต์ |
| `source` | `VARCHAR(100)` | NO | — | — | ชื่อแหล่งที่มาของข้อเสนอ (โค้ดบันทึก ANC BROKER) |
| `source_url` | `TEXT` | YES | NULL (implicit) | — | ที่อยู่เว็บหน้ารายละเอียดแพ็กเกจ |
| `listing_url` | `TEXT` | YES | NULL (implicit) | — | ที่อยู่เว็บหน้าแสดงรายการแพ็กเกจ |
| `region` | `VARCHAR(100)` | NO | — | — | ภูมิภาคของจังหวัดเป้าหมาย |
| `province` | `VARCHAR(100)` | NO | — | INDEX idx_province | จังหวัดที่ใช้ขอราคา |
| `brand` | `VARCHAR(80)` | NO | — | INDEX idx_vehicle (1) | ยี่ห้อรถ |
| `model` | `VARCHAR(120)` | NO | — | INDEX idx_vehicle (2) | รุ่นรถ |
| `submodel` | `VARCHAR(255)` | YES | NULL (implicit) | — | รุ่นย่อยรถที่พบ |
| `car_id` | `INT` | NO | — | — | รหัสรถที่เว็บไซต์ใช้ในการค้นหา |
| `vehicle_year` | `SMALLINT` | NO | — | INDEX idx_vehicle (3) | ปีรถยนต์ |
| `insurance_type` | `VARCHAR(30)` | NO | — | — | ประเภทประกันที่เก็บ (โค้ดกำหนด ประเภท 1) |
| `repair_type` | `VARCHAR(30)` | NO | — | — | ประเภทซ่อม เช่น ซ่อมอู่ หรือ ซ่อมห้าง |
| `insurer` | `VARCHAR(255)` | YES | NULL (implicit) | — | ชื่อบริษัทประกันภัยที่พบ/เติมจากหน้า Detail |
| `insurer_code` | `VARCHAR(80)` | YES | NULL (implicit) | — | รหัสบริษัทประกันจากแหล่งข้อมูล |
| `package_id` | `VARCHAR(100)` | YES | NULL (implicit) | — | รหัสแพ็กเกจของเว็บไซต์ |
| `package_name` | `VARCHAR(500)` | YES | NULL (implicit) | — | ชื่อแพ็กเกจประกันภัย |
| `premium` | `DECIMAL(15,2)` | YES | NULL (implicit) | — | ราคาเสนอของแพ็กเกจที่เก็บจาก Listing |
| `net_premium` | `DECIMAL(15,2)` | YES | NULL (implicit) | — | เบี้ยสุทธิตามฟิลด์ที่พบใน Listing |
| `original_premium` | `DECIMAL(15,2)` | YES | NULL (implicit) | — | เบี้ยก่อนส่วนลด/เบี้ยต้นทางที่เว็บไซต์แสดง |
| `actual_sum_insured` | `DECIMAL(15,2)` | YES | NULL (implicit) | — | ทุนประกันภัยของข้อเสนอ |
| `deductible` | `DECIMAL(15,2)` | YES | NULL (implicit) | — | ค่าเสียหายส่วนแรก หากเว็บไซต์ระบุ |
| `coverage_bodily_injury_person` | `DECIMAL(15,2)` | YES | NULL (implicit) | — | วงเงินคุ้มครองบุคคลภายนอกต่อคน |
| `coverage_bodily_injury_accident` | `DECIMAL(15,2)` | YES | NULL (implicit) | — | วงเงินคุ้มครองบุคคลภายนอกต่ออุบัติเหตุ |
| `coverage_third_party_property` | `DECIMAL(15,2)` | YES | NULL (implicit) | — | วงเงินคุ้มครองทรัพย์สินบุคคลภายนอก |
| `coverage_own_damage` | `DECIMAL(15,2)` | YES | NULL (implicit) | — | วงเงินคุ้มครองความเสียหายรถผู้เอาประกัน |
| `coverage_fire_theft` | `DECIMAL(15,2)` | YES | NULL (implicit) | — | วงเงินความคุ้มครองไฟไหม้หรือโจรกรรม |
| `coverage_flood` | `DECIMAL(15,2)` | YES | NULL (implicit) | — | วงเงินความคุ้มครองน้ำท่วม |
| `personal_accident` | `DECIMAL(15,2)` | YES | NULL (implicit) | — | ความคุ้มครองอุบัติเหตุส่วนบุคคล |
| `medical_expense` | `DECIMAL(15,2)` | YES | NULL (implicit) | — | วงเงินค่ารักษาพยาบาล |
| `bail_bond` | `DECIMAL(15,2)` | YES | NULL (implicit) | — | วงเงินประกันตัวผู้ขับขี่ |
| `listing_status` | `VARCHAR(40)` | NO | — | — | สถานะการเก็บข้อมูลจากหน้า Listing |
| `detail_status` | `VARCHAR(40)` | NO | — | INDEX idx_status | สถานะการเก็บหน้า Detail |
| `validation_status` | `VARCHAR(40)` | NO | — | — | สถานะการตรวจสอบความน่าเชื่อถือของข้อมูล |
| `validation_notes` | `TEXT` | YES | NULL (implicit) | — | คำอธิบายเมื่อพบปัญหาการตรวจสอบ |
| `raw_listing` | `LONGTEXT` | YES | NULL (implicit) | — | ข้อความ JSON จาก Listing (เก็บเป็นข้อความ ไม่ใช่ MySQL JSON) |
| `raw_detail` | `LONGTEXT` | YES | NULL (implicit) | — | ข้อความ JSON จาก Detail (เก็บเป็นข้อความ ไม่ใช่ MySQL JSON) |
| `collected_at` | `DATETIME` | NO | — | — | เวลาที่บันทึกแถวครั้งแรก |
| `updated_at` | `DATETIME` | NO | — | — | เวลาที่อัปเดตแถวล่าสุด |

**Keys/Indexes:** `PRIMARY KEY (id)`; `UNIQUE KEY uq_record_key (record_key)`; `idx_target (target_key)`; `idx_branch (branch_key)`; `idx_vehicle (brand, model, vehicle_year)`; `idx_province (province)`; `idx_status (detail_status)`.

## 2. `anc_competitor_target_v4` — 12 คอลัมน์

**หน้าที่:** ติดตามสถานะเป้าหมายแต่ละคู่จังหวัดและรถยนต์

**ใน Workflow:** จำนวนและสถานะของงานย่อยใช้ประเมินความคืบหน้า ไม่ใช่จำนวนข้อเสนอที่รับประกันว่าจะค้นพบ

| Column | MySQL Data Type | Nullable | Default | Key / Index | Description |
|---|---|---|---|---|---|
| `target_key` | `CHAR(64)` | NO | — | PRIMARY | รหัสเอกลักษณ์ของจังหวัดและรถยนต์เป้าหมาย |
| `region` | `VARCHAR(100)` | NO | — | — | ภูมิภาค |
| `province` | `VARCHAR(100)` | NO | — | — | จังหวัดเป้าหมาย |
| `brand` | `VARCHAR(80)` | NO | — | — | ยี่ห้อรถเป้าหมาย |
| `model` | `VARCHAR(120)` | NO | — | — | รุ่นรถเป้าหมาย |
| `vehicle_year` | `SMALLINT` | NO | — | — | ปีรถยนต์ |
| `status` | `VARCHAR(40)` | NO | — | INDEX idx_status | สถานะรวมของเป้าหมาย |
| `total_submodels` | `INT` | NO | 0 | — | จำนวนรุ่นย่อยที่พบสำหรับเป้าหมาย |
| `complete_branches` | `INT` | NO | 0 | — | จำนวน Branch ที่ทำเสร็จ |
| `total_branches` | `INT` | NO | 0 | — | จำนวน Branch ทั้งหมดที่บันทึกไว้ |
| `error_message` | `TEXT` | YES | NULL (implicit) | — | ข้อความเหตุผิดพลาดของเป้าหมาย |
| `updated_at` | `DATETIME` | NO | — | — | เวลาที่แก้ไขสถานะล่าสุด |

**Keys/Indexes:** `PRIMARY KEY (target_key)`; `idx_status (status)`.

## 3. `anc_competitor_branch_v4` — 13 คอลัมน์

**หน้าที่:** ติดตามงานแยกตามเป้าหมาย รหัสรุ่นย่อย (`car_id`) และประเภทซ่อม

**ใน Workflow:** Branch เดียวอาจพบแพ็กเกจได้หลายรายการ และบางรายการอาจเปิดหน้า Detail ไม่ได้

| Column | MySQL Data Type | Nullable | Default | Key / Index | Description |
|---|---|---|---|---|---|
| `branch_key` | `CHAR(64)` | NO | — | PRIMARY | รหัสเอกลักษณ์ของงานรุ่นย่อยและประเภทซ่อม |
| `target_key` | `CHAR(64)` | NO | — | INDEX idx_target | เชื่อม Branch เข้ากับเป้าหมาย |
| `car_id` | `INT` | NO | — | — | รหัสรุ่นย่อย/รถยนต์ที่ใช้ขอข้อมูล |
| `repair_type` | `VARCHAR(30)` | NO | — | — | ประเภทซ่อมที่ใช้กรองรายการ |
| `status` | `VARCHAR(40)` | NO | — | INDEX idx_status | สถานะของ Branch |
| `listing_url` | `TEXT` | YES | NULL (implicit) | — | ลิงก์ Listing ของ Branch |
| `package_count` | `INT` | NO | 0 | — | จำนวนแพ็กเกจที่ระบบบันทึกสำหรับ Branch |
| `saved_count` | `INT` | NO | 0 | — | จำนวนรายการที่มี Checkpoint ตามการนับของโค้ด |
| `detail_done` | `INT` | NO | 0 | — | จำนวนแพ็กเกจที่จบ Detail ด้วย DONE |
| `detail_unavailable` | `INT` | NO | 0 | — | จำนวนแพ็กเกจที่หน้า Detail ไม่พร้อมให้เรียก |
| `listing_issues` | `TEXT` | YES | NULL (implicit) | — | ข้อความ JSON ของปัญหาจากการอ่าน Listing |
| `error_message` | `TEXT` | YES | NULL (implicit) | — | ข้อความเหตุผิดพลาดของ Branch |
| `updated_at` | `DATETIME` | NO | — | — | เวลาอัปเดต Branch ล่าสุด |

**Keys/Indexes:** `PRIMARY KEY (branch_key)`; `idx_target (target_key)`; `idx_status (status)`.

## 4. `anc_competitor_package_checkpoint_v4` — 6 คอลัมน์

**หน้าที่:** จดจำความคืบหน้าการอ่าน Detail ของแพ็กเกจแต่ละรายการ

**ใน Workflow:** ใช้อ้างอิง `record_key` เพื่อข้ามงานที่ทำเสร็จแล้วเมื่อ Resume

| Column | MySQL Data Type | Nullable | Default | Key / Index | Description |
|---|---|---|---|---|---|
| `record_key` | `CHAR(64)` | NO | — | PRIMARY | รหัสข้อเสนอ ตรงกับ record_key ในตารางประกัน |
| `branch_key` | `CHAR(64)` | NO | — | INDEX idx_branch | รหัส Branch ที่แพ็กเกจนี้สังกัด |
| `status` | `VARCHAR(40)` | NO | — | INDEX idx_status | สถานะการประมวลผลแพ็กเกจ |
| `attempts` | `INT` | NO | 0 | — | จำนวนครั้งที่มีการบันทึก/พยายามประมวลผล |
| `last_error` | `TEXT` | YES | NULL (implicit) | — | เหตุผิดพลาดล่าสุด หากมี |
| `updated_at` | `DATETIME` | NO | — | — | เวลาที่อัปเดต Checkpoint ล่าสุด |

**Keys/Indexes:** `PRIMARY KEY (record_key)`; `idx_branch (branch_key)`; `idx_status (status)`.

## 5. ความหมายของจำนวนเงินและการตรวจสอบข้อมูล

- `premium` = เบี้ยของแพ็กเกจที่อ่านจาก Listing; `net_premium` = เบี้ยสุทธิที่เว็บไซต์ให้มา; `original_premium` = เบี้ยตั้งต้น/ก่อนส่วนลดที่อ่านได้ ทั้งสามฟิลด์ **ไม่ควรสลับกันเอง**
- `actual_sum_insured` = ทุนประกันรถยนต์; `deductible` = ค่าเสียหายส่วนแรก; `coverage_*`, `personal_accident`, `medical_expense`, `bail_bond` = วงเงินคุ้มครองแต่ละหัวข้อ ถ้าเป็น `NULL` ไม่ควรตีความเป็น 0 โดยอัตโนมัติ
- `listing_status` สะท้อนการเก็บ Listing (เช่น `LISTING_CAPTURED`); `detail_status` สะท้อนรายละเอียด (`PENDING`, `DONE`, `DETAIL_UNAVAILABLE`, `RETRY_REQUIRED`); `validation_status` บอกระดับการตรวจสอบ (`LISTING_VERIFIED`, `DETAIL_VERIFIED`, `REVIEW_REQUIRED`) ไม่ใช่การรับรองว่าข้อมูลทุกช่องถูกต้องตามกรมธรรม์
- `raw_listing` และ `raw_detail` คือ JSON **ในรูปข้อความ LONGTEXT**: ไม่มีการบังคับ JSON Schema โดย MySQL และเนื้อหาอาจไม่ครบหรือว่าง

## 6. ตัวอย่างสถานะงาน

| ตาราง | สถานะที่ปรากฏในโค้ด | ความหมาย |
|---|---|---|
| Target | `RUNNING`, `DONE`, `PARTIAL`, `FAILED`, `BLOCKED`, `INTERRUPTED` | ความคืบหน้ารวมของเป้าหมาย |
| Branch | `RUNNING`, `LISTING_CAPTURED`, `DETAIL_PENDING`, `DONE`, `PARTIAL`, `FAILED`, `BLOCKED`, `INTERRUPTED` | ความคืบหน้าของการอ่าน Listing/Detail ในรุ่นย่อยและประเภทซ่อม |
| Package checkpoint | `PENDING`, `DONE`, `DETAIL_UNAVAILABLE`, `RETRY_REQUIRED` | ใช้ตัดสินใจว่าจะข้ามการอ่าน Detail ในการ Resume หรือไม่ |

เมื่อใช้ `--resume` โค้ดข้าม Target/Branch ที่ `DONE` และข้าม Package ที่ `DONE` หรือ `DETAIL_UNAVAILABLE` (ไม่ใช่คำรับรองว่า Detail ครบเสมอ) สถานะ `FAILED` อาจหมายถึงดึงข้อมูลไม่ได้ **ไม่ใช่ไม่มีข้อเสนอประกัน**

## 7. ความสัมพันธ์แบบ Application-level

```text
anc_competitor_target_v4 (target_key)
   └── anc_competitor_branch_v4 (target_key, branch_key)
         ├── anc_competitor_insurance_v4 (record_key, branch_key, target_key)
         └── anc_competitor_package_checkpoint_v4 (record_key, branch_key)
```

- `target_key`: รหัสจากจังหวัด + ยี่ห้อ + รุ่น + ปีรถ (Hash ที่โค้ดสร้าง)
- `branch_key`: รหัสจากเป้าหมาย + `car_id` + ประเภทซ่อม (Hash ที่โค้ดสร้าง)
- `record_key`: รหัสข้อเสนอจาก Branch + บริษัท/แพ็กเกจ/ทุน/URL ตาม Logic เดิม

ตารางปัจจุบัน **ไม่มีการประกาศ SQL FOREIGN KEY** ความสัมพันธ์เหล่านี้จึงถูกดูแลโดยโค้ด ไม่ใช่ Constraint ของ MySQL โดยตรง

## 8. Export Mapping — Excel Snapshot

คำสั่ง `python anc_export_db.py` จะอ่าน MySQL ทั้ง 4 ตารางด้วย `SELECT *` และออกไฟล์ `anc_competitor_db_snapshot.xlsx` โดยเรียง Worksheets ตามนี้:

| MySQL Table | Excel Worksheet |
|---|---|
| `anc_competitor_insurance_v4` | `anc_competitor_insurance_v4` |
| `anc_competitor_target_v4` | `anc_competitor_target_v4` |
| `anc_competitor_branch_v4` | `anc_competitor_branch_v4` |
| `anc_competitor_package_checkpoint_v4` | `anc_package_checkpoint_v4` |

Sheet ที่ 4 ใช้นามแฝง เพราะ Excel จำกัดชื่อ Sheet 31 ตัวอักษร; **ชื่อ Table ใน MySQL ไม่ได้เปลี่ยน** Workbook มีเพียง 4 Sheets ไม่มีชีตสรุปแทรก

Excel จำกัด 1,048,576 แถว/Sheet (รวม Header), 32,767 ตัวอักษร/Cell; ถ้าเกินจะหยุดโดยไม่ตัดข้อมูลเงียบ ๆ ตัวเลข `DECIMAL` ถูกเก็บเป็น Text เพื่อรักษาความแม่นยำ และ Integer ที่ยาวกว่า 15 หลักก็ถูกเก็บเป็น Text เช่นกัน `NULL` จะเป็นเซลล์ว่าง (แยกจากข้อความว่างได้ไม่แน่ชัด) วันที่ที่ Excel รองรับใช้ชนิดวันที่; ค่าเวลาที่มีความละเอียดซึ่ง Excel รักษาไม่ได้จะเก็บเป็นข้อความตามรูปแบบเดิม นี่คือ **Excel Snapshot ไม่ใช่ไฟล์สำรอง SQL สำหรับ Restore**

## 9. คำอธิบายชนิดข้อมูลและคำศัพท์

| คำศัพท์ | ความหมาย |
|---|---|
| `CHAR(n)` | ข้อความความยาวคงที่ เช่นรหัส Hash 64 อักขระ |
| `VARCHAR(n)` | ข้อความความยาวไม่เกิน n อักขระ เช่นชื่อจังหวัด |
| `TEXT` | ข้อความยาว เช่น URL หรือรายละเอียด Error |
| `LONGTEXT` | ข้อความขนาดใหญ่มาก เช่น Raw JSON |
| `INT` | จำนวนเต็ม เช่นจำนวนแพ็กเกจ |
| `SMALLINT` | จำนวนเต็มที่ใช้พื้นที่น้อยกว่า INT เช่นปีรถ |
| `BIGINT UNSIGNED` | จำนวนเต็มขนาดใหญ่ที่ไม่ติดลบ เช่น ID อัตโนมัติ |
| `DECIMAL(15,2)` | เลขฐานสิบแม่นยำ 15 หลักรวม 2 หลักหลังจุด เช่น `1234.50` |
| `DATETIME` | วันที่และเวลา โดยไม่มีข้อมูล Timezone ในชนิด SQL นี้ |
| `PRIMARY KEY` | รหัสหลักที่ระบุแถวได้ไม่ซ้ำและห้ามเป็น NULL |
| `UNIQUE KEY` | บังคับค่ารหัสไม่ซ้ำตามดัชนีที่กำหนด |
| `INDEX` | ดัชนีช่วยการค้นหา ไม่ได้รับรองว่าเชื่อมข้อมูลถูกต้อง |
| `NULL` | ไม่มีค่า (ต่างจากเลข 0 และข้อความว่างใน MySQL) |
| `NOT NULL` | ต้องมีค่า ห้ามเป็น SQL NULL |

## 10. ตรวจโครงสร้างฐานข้อมูลจริง

ตัวอย่างคำสั่ง SQL สำหรับตรวจฐานข้อมูลที่เชื่อมต่ออยู่:

```sql
SELECT DATABASE();
SHOW TABLES LIKE 'anc_competitor_%';
SHOW CREATE TABLE anc_competitor_insurance_v4;
SHOW COLUMNS FROM anc_competitor_package_checkpoint_v4;
```

หาก Schema ของตารางที่มีอยู่จริงต่างจากเอกสาร ให้ใช้ผลจาก MySQL จริงเป็นหลักในการตรวจสอบข้อมูลที่กำลัง Export และอย่าแก้โครงสร้าง Production โดยไม่มีแผนงาน