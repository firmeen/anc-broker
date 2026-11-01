# ANC Broker Collector — คู่มือใช้งาน

ระบบเก็บข้อมูลข้อเสนอ **ประกันภัยรถยนต์ประเภท 1** จากเว็บไซต์ [ANC Broker](https://www.ancbroker.com) ลงฐานข้อมูล MySQL พร้อมส่งออกเป็น CSV หรือ Excel เหมาะสำหรับเปรียบเทียบเบี้ย ทุนประกัน บริษัทประกัน ความคุ้มครอง และติดตามว่าข้อมูลใดเก็บสำเร็จแล้ว

> คู่มือนี้อธิบายวิธีใช้งานบน **Windows PowerShell** โดยยึดคำสั่งจริงของ `anc_collector.py` (v4.1) และ `anc_export.py` (v2.0) ข้อมูลที่เก็บได้ขึ้นอยู่กับการเข้าถึงเว็บไซต์และข้อเสนอที่มีอยู่จริง **ไม่รับประกันว่าทุกเป้าหมายจะมีผลลัพธ์**

## 1. ภาพรวมและขอบเขต

- กำหนด **24 จังหวัด × 20 รายการรถยนต์ = 480 เป้าหมายเริ่มต้น** (หนึ่งเป้าหมายคือหนึ่งจังหวัดและรถยนต์หนึ่งชุด) จากนั้นระบบขยายไปยังรุ่นย่อยและประเภทซ่อม (`ซ่อมอู่` / `ซ่อมห้าง`) ที่ค้นพบจริง
- เก็บรายการแพ็กเกจ เบี้ยประกัน ทุนประกัน บริษัทประกัน และข้อมูลความคุ้มครองที่ค้นพบ รวมทั้งสถานะการเก็บข้อมูล
- `anc_collector.py` **ดึงข้อมูลและบันทึก MySQL**; `anc_export.py` **จัดทำ Excel สำหรับการวิเคราะห์ข้อเสนอประกัน**; `anc_export_db.py` **คัดข้อมูลทั้งสี่ตารางลง Excel ตามต้นทางโดยไม่วิเคราะห์หรือกรอง**

## 2. ระบบทำงานอย่างไร

```text
ANC Broker Website
  → ค้นหารถยนต์/รุ่นย่อย
  → เลือกจังหวัดและประเภทซ่อม
  → อ่านรายการแพ็กเกจ (Listing)
  → บันทึก Listing ลง MySQL และจดจำ Checkpoint
  → อ่านข้อมูลเพิ่มจากหน้าแพ็กเกจ (Detail)
  → อัปเดต MySQL และสถานะ Checkpoint
  ├─ CSV: รายการจากตารางประกันภัย
  ├─ Excel: ข้อเสนอประกันที่ผ่านการจัดรูปแบบ
  └─ Excel: Snapshot ตรงจาก MySQL ทั้ง 4 ตาราง
```

**Collection** คือการขอข้อมูลจากเว็บไซต์, **Persistence** คือการเขียนลง MySQL, **Checkpoint** คือสถานะที่ใช้ทำงานต่อ, และ **Export** คือการอ่านข้อมูลที่มีอยู่มาสร้างไฟล์ ไม่ใช่การดึงข้อมูลจากเว็บไซต์รอบใหม่

## 3. ไฟล์ในโครงการ

| ไฟล์ | ใช้ทำอะไร |
|---|---|
| `anc_collector.py` | ค้นหาและเก็บข้อเสนอประกันลง MySQL, แสดง Terminal UI, ส่งออก CSV |
| `anc_export.py` | แปลง/คัดกรอง/รวมข้อเสนอจากตารางประกันให้เป็นรายงาน Excel |
| `anc_export_db.py` | อ่านทุกแถวทุกคอลัมน์ของ 4 ตารางลง Excel เดียวพร้อม Terminal UI |
| `README.md` | คู่มือการใช้งานนี้ |
| `README_DB.md` | รายชื่อตาราง ทุกคอลัมน์ ชนิดข้อมูล และความสัมพันธ์ใน MySQL |
| `.env.example` | ตัวอย่างตัวแปรเชื่อมต่อฐานข้อมูล (ไม่ใช่รหัสผ่านจริง) |
| `.gitignore` | ระบุไฟล์ที่ไม่ควรนำขึ้น Git เช่น `.env` และไฟล์ข้อมูลที่สร้างระหว่างรัน |

ไฟล์ CSV/Excel, Log, Catalog และ Debug สร้างเมื่อเรียกคำสั่งที่เกี่ยวข้อง ไม่ใช่ไฟล์ข้อมูลที่ติดมากับ GitHub โดยอัตโนมัติ

## 4. สิ่งที่ต้องเตรียม

ต้องมี **Python**, **MySQL ที่ทำงานอยู่**, อินเทอร์เน็ตสำหรับขั้นตอนดึงข้อมูล และ PowerShell สำหรับคำสั่งด้านล่าง ตรวจสอบด้วย:

```powershell
python --version
mysql --version
```

ถ้า `mysql` ไม่อยู่ใน PATH สามารถใช้เครื่องมือ MySQL ที่ติดตั้งไว้เพื่อเช็ก Server แทนได้ การ Export ต้องใช้ MySQL ที่เข้าถึงได้จริง

ไลบรารีของ Collector:

```powershell
python -m pip install requests beautifulsoup4 pymysql python-dotenv rich truststore
```

ไลบรารีของ Excel Exporter:

```powershell
python -m pip install pymysql python-dotenv openpyxl rich
```

ติดตั้งทั้งหมดพร้อมกันด้วยคำสั่งเดียว:

```powershell
python -m pip install requests beautifulsoup4 pymysql python-dotenv rich truststore openpyxl
```

## 5. ติดตั้งจาก GitHub บน Windows

```powershell
git clone https://github.com/firmeen/anc-broker.git
cd anc-broker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install requests beautifulsoup4 pymysql python-dotenv rich truststore openpyxl
python anc_collector.py --help
python anc_export_db.py --help
```

ถ้า PowerShell ไม่อนุญาตให้ Activate ให้ **เรียก Python ใน Virtual Environment โดยตรง** ไม่ต้องเปลี่ยน Execution Policy:

```powershell
.\.venv\Scripts\python.exe -m pip install requests beautifulsoup4 pymysql python-dotenv rich truststore openpyxl
.\.venv\Scripts\python.exe anc_export_db.py --help
```

ตัวอย่างอื่นในคู่มือนี้ใช้ `python` โดยสมมติว่าได้ Activate แล้ว หากยังไม่ Activate ให้ใช้ `.\.venv\Scripts\python.exe` แทน

## 6. ตั้งค่า MySQL

คัดลอกไฟล์ตัวอย่างแล้วเปิดแก้ค่าตามเครื่องจริง:

```powershell
Copy-Item .env.example .env
notepad .env
```

ตัวแปรที่ใช้:

| ตัวแปร | ความหมาย | ตัวอย่างจาก Repository |
|---|---|---|
| `DB_HOST` | ที่อยู่ Server | `127.0.0.1` |
| `DB_PORT` | Port ของ MySQL | `3307` |
| `DB_USER` | ชื่อผู้ใช้ MySQL | `root` (ควรใช้ผู้ใช้เฉพาะงานเมื่อใช้งานจริง) |
| `DB_PASSWORD` | รหัสผ่านของผู้ใช้ | ตั้งค่าของคุณเอง; ค่าในไฟล์ตัวอย่างเป็น placeholder |
| `DB_NAME` | ฐานข้อมูลที่เก็บข้อมูล | `tqm` ใน `.env.example` |

**สำคัญ:** โค้ดเดิมใช้ `dbd` เป็นชื่อฐานข้อมูล *fallback* เฉพาะเมื่อไม่ได้กำหนด `DB_NAME` หรือ `MYSQL_DATABASE`; หาก `.env` มี `DB_NAME=tqm` จะใช้ `tqm` ตามนั้น และรองรับ `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE` เป็นชื่อตัวแปรสำรอง

ฐานข้อมูลต้องสร้างไว้ก่อนใช้งาน Collector ต้องมีสิทธิ์สร้างตาราง/เขียนข้อมูลตามการทำงานเดิม ส่วน `anc_export_db.py` ใช้คำสั่งอ่านข้อมูลเท่านั้น (สามารถใช้บัญชีที่มีสิทธิ์ SELECT และอ่าน metadata ได้) **ห้าม Commit ไฟล์ `.env` ที่มีรหัสผ่านขึ้น GitHub**

## 7. ทดลองก่อนเก็บจริง

```powershell
python anc_collector.py --dry-run
python anc_collector.py --catalog
python anc_collector.py --probe
```

| คำสั่ง | ผลลัพธ์ | ใช้เว็บไซต์จริง? | ต่อ MySQL? |
|---|---|---|---|
| `--dry-run` | พิมพ์รายการรถยนต์และจำนวนเป้าหมายที่กำหนด | ไม่ | ไม่ |
| `--catalog` | โหลดหน้า Class 1 เพื่ออ่านรายการรุ่นย่อย; เขียน `anc_vehicle_catalog.json` | ใช่ | ไม่ |
| `--probe` | ตรวจสอบหน้าแรกและหน้า Class 1; บันทึก HTML Debug ใน `anc_debug/` | ใช่ | ไม่ |

`--catalog` และ `--probe` อาจล้มเหลวหากเว็บไซต์เปลี่ยนโครงสร้าง Redirect หรือมีข้อจำกัด `robots.txt` / การเข้าถึง; `--dry-run` **ไม่ได้** ตรวจสอบ MySQL หรือความสามารถในการดึงข้อมูลจริง

## 8. เริ่มเก็บข้อมูล

ทดลองหนึ่งเป้าหมายก่อน:

```powershell
python anc_collector.py --limit 1 --delay 2
```

หนึ่งเป้าหมายหมายถึง **จังหวัด + รถยนต์หนึ่งรายการ** อาจมีหลายรุ่นย่อย หลายประเภทซ่อม และหลายแพ็กเกจ ไม่ได้หมายถึง HTTP Request เดียว

เริ่มหรือทำงานต่อทุกเป้าหมาย:

```powershell
python anc_collector.py --resume
```

`--resume` เป็น **ค่าเริ่มต้นอยู่แล้ว**: ข้าม Target / Branch ที่สถานะ `DONE` และข้ามรายละเอียดของแพ็กเกจที่มี Checkpoint `DONE` หรือ `DETAIL_UNAVAILABLE`; งานบางส่วนที่ยังไม่สมบูรณ์อาจต้องประมวลผลอีกครั้งตามโค้ดเดิม ข้อมูล Listing ถูกบันทึกก่อนอ่าน Detail หากหน้า Detail ไม่พบ ข้อมูล Listing อาจยังอยู่พร้อมสถานะ `DETAIL_UNAVAILABLE` ไม่ได้แปลว่าทุกรายละเอียดสมบูรณ์

สามารถกด `Ctrl+C` เพื่อขอหยุดอย่างปลอดภัย ระบบพยายามรักษาข้อมูลที่ Commit แล้ว แต่การหยุดระหว่าง Request อาจทำให้งานปัจจุบันยังไม่สมบูรณ์

## 9. อ่านหน้าจอ Terminal ของ Collector

| แสดงผล | หมายถึง |
|---|---|
| `HTTP` / `SUCCESS` / `404` / `ERROR` | จำนวนคำขอเว็บไซต์และผลตอบกลับ ไม่ใช่จำนวนข้อเสนอ |
| `Saved records` | ข้อมูลข้อเสนอที่มีในฐานข้อมูล (รวมจากรอบก่อน) |
| `CURRENT RUN` | ที่บันทึก/อัปเดต/ข้ามเฉพาะรอบปัจจุบัน |
| `Detail completed` | จำนวนรายการที่รายละเอียดเสร็จ (`DONE`) |
| `Detail unavailable` | รายการที่เปิดหน้ารายละเอียดไม่ได้ แต่ Listing อาจยังอยู่ |
| `Pending` | รายละเอียดรอดำเนินการหรือรอลองใหม่ |
| `Target` / `Branch` | สถานะความคืบหน้าระดับเป้าหมายและรุ่นย่อย/ซ่อม |
| `SKIPPED` | รายการที่ระบบตัดสินใจข้ามตาม Resume |

**คำว่า Failed ไม่ใช่ข้อพิสูจน์ว่าไม่มีข้อเสนอในตลาด** อาจเกิดจากเว็บไซต์ การเชื่อมต่อ หรือการตรวจสอบข้อมูลไม่ผ่าน

## 10. ส่งออกผลลัพธ์: เลือกให้ตรงงาน

### A. CSV จาก Collector

```powershell
python anc_collector.py --export-only
```

อ่านตาราง `anc_competitor_insurance_v4` และสร้าง `anc_competitor_insurance_24_provinces.csv` (หรือระบุชื่อด้วย `--output`) คำสั่งนี้ไม่ดึงข้อมูลจากเว็บไซต์ แต่โค้ด Collector เดิมเรียก `initialize()` ซึ่งมี `CREATE TABLE IF NOT EXISTS` ก่อน Export

### B. Excel รายงานข้อเสนอประกัน (ผ่านการจัดรูปแบบ)

```powershell
python anc_export.py
python anc_export.py --inspect
python anc_export.py --overwrite
```

สร้าง `anc_insurance_offers_complete.xlsx` จากตารางข้อเสนอประกัน โดยมีการตรวจสอบ แปลงชื่อคอลัมน์ คัดกรอง และจัดการรายการซ้ำเพื่อการอ่านเชิงธุรกิจ **ไม่ใช่สำเนาข้อมูล SQL ตรงตัว**

### C. Excel Snapshot ทั้งสี่ตาราง (อ่านอย่างเดียว)

```powershell
python anc_export_db.py
python anc_export_db.py --output backup.xlsx
python anc_export_db.py --overwrite
```

สร้าง `anc_competitor_db_snapshot.xlsx` เป็น **1 Workbook / 4 Worksheets** แยกตามตาราง ใช้ชื่อคอลัมน์จริงทุกคอลัมน์ ไม่กรองสถานะ ไม่ลบข้อมูลซ้ำ และไม่แก้ข้อมูล MySQL

Terminal UI ของ Snapshot จะแสดงฐานข้อมูล/Port (ไม่แสดงรหัสผ่าน), รายการตาราง, จำนวนแถวที่เขียนจริง, ความคืบหน้า, อัตรา Rows/s, เวลา และ ETA ถ้าคำนวณได้ สถานะ `COMPLETED` จะขึ้นหลังไฟล์ Excel ผ่านการตรวจและบันทึกสำเร็จเท่านั้น

หากไฟล์ปลายทางมีอยู่แล้ว จะไม่เขียนทับจนกว่าจะใช้ `--overwrite` การส่งออกใช้ InnoDB read-only consistent snapshot และจะหยุดหากข้อมูลเกินขีดจำกัดของ Excel แทนการตัดข้อมูลทิ้ง

## 11. คู่มือโครงสร้างฐานข้อมูล

ดู [README_DB.md](README_DB.md) สำหรับชื่อทั้ง 4 ตาราง, คำอธิบาย **73 คอลัมน์**, SQL Data Type, Nullable, Default, Keys, Indexes และชื่อ Sheet ใน Excel

## 12. ปัญหาที่พบบ่อย

| ปัญหา | วิธีตรวจ/แก้ |
|---|---|
| `python` not found | ติดตั้ง Python หรือใช้ Python ใน `.venv` โดยตรง |
| `ModuleNotFoundError` | ติดตั้ง Packages ด้วย `python -m pip install ...` ใน Environment ที่รันจริง |
| MySQL connection refused | ตรวจว่า MySQL รันอยู่, `DB_HOST`/`DB_PORT` ถูกต้อง |
| Access denied | ตรวจ `DB_USER`, `DB_PASSWORD`, สิทธิ์ MySQL |
| Unknown database | ตรวจ `DB_NAME` ว่าฐานข้อมูลถูกสร้างไว้แล้วและเป็นฐานเดียวกับที่ Collector ใช้ |
| Required table missing | รัน Collector กับฐานข้อมูลที่ถูกต้องก่อน หรือเลือกฐานข้อมูลที่มีทั้ง 4 ตาราง |
| Website blocked / HTTP 404 | ตรวจเว็บไซต์และ Robots Policy; อย่าสรุปว่าไม่มีข้อเสนอจาก Error |
| Output already exists | เปลี่ยนชื่อด้วย `--output` หรือใช้ `--overwrite` |
| Excel file is open | ปิดไฟล์ใน Excel ก่อนใช้ `--overwrite` |
| Ctrl+C / Export failed | ไฟล์ Snapshot เดิมยังอยู่; ไฟล์ชั่วคราวที่ไม่สมบูรณ์จะถูกลบ |
| Excel row limit exceeded | หนึ่ง Sheet รองรับข้อมูลได้สูงสุด 1,048,575 แถว; ไม่ตัดแถวอัตโนมัติ |
| Excel cell value unsupported | ดูชื่อ Table/Row/Column ใน Error; Snapshot จะหยุดแทนการตัดข้อความหรือแปลงผิด |

## 13. คำสั่งที่ใช้บ่อย

| สิ่งที่ต้องการ | คำสั่ง |
|---|---|
| แสดงขอบเขตงาน | `python anc_collector.py --dry-run` |
| ตรวจรายชื่อรถ | `python anc_collector.py --catalog` |
| ตรวจหน้าเว็บไซต์ | `python anc_collector.py --probe` |
| ทดลองเก็บ 1 เป้าหมาย | `python anc_collector.py --limit 1 --delay 2` |
| เก็บต่อ | `python anc_collector.py --resume` |
| ส่งออก CSV | `python anc_collector.py --export-only` |
| ตรวจข้อมูลก่อน Excel รายงาน | `python anc_export.py --inspect` |
| Excel รายงานข้อเสนอ | `python anc_export.py` |
| Excel Snapshot 4 ตาราง | `python anc_export_db.py` |
| เขียนทับ Snapshot | `python anc_export_db.py --overwrite` |
| ดูตัวเลือก | `python anc_export_db.py --help` |

## 14. การดูแลข้อมูลและข้อจำกัด

- เว็บไซต์ต้นทางอาจมีข้อจำกัดหรือปรับโครงสร้าง; ข้อมูลอาจไม่ครบทุกจังหวัด/รุ่นรถ/แพ็กเกจ
- `DONE` ของ Checkpoint บอกความสำเร็จตาม Workflow ไม่ใช่การรับรองความถูกต้องเชิงธุรกิจของทุกฟิลด์
- Snapshot Excel เป็นข้อมูลที่ Excel **แสดงแทนได้** ไม่ใช่ไฟล์ Backup/Restore ของ MySQL: ไม่มี SQL Index, Constraint และ Native Data Type ครบทั้งหมด
- `NULL` ใน SQL และข้อความว่างอาจแยกไม่ออกใน Excel; ตัวเลข Decimal และ Integer ที่เสี่ยงสูญเสียความแม่นยำถูกเก็บเป็น Text; ข้อมูลที่ Excel เก็บไม่ได้จะทำให้ Export ล้มเหลวพร้อมแจ้งสาเหตุ
- เก็บ `.env` และข้อมูลประกันที่ Export อย่างเหมาะสม อย่า Commit รหัสผ่านหรือข้อมูลที่ไม่ควรเปิดเผย