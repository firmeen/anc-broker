#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ANC Insurance Complete Offer Exporter v2.0

Source:
    MySQL: anc_competitor_insurance_v4

Output:
    anc_insurance_offers_complete.xlsx

Requirements:
    pip install pymysql python-dotenv openpyxl

Commands:
    python anc_export.py
    python anc_export.py --overwrite
    python anc_export.py --include-partial --overwrite
    python anc_export.py --inspect
    python anc_export.py --strict
    python anc_export.py --output complete.xlsx --overwrite

Design:
    - Read-only MySQL access
    - One Excel file
    - One worksheet
    - One row per distinct insurance offer
    - Thai column names
    - No JSON or timestamp columns
    - Resolve missing insurer names using verified source records
    - Preserve distinct package variants and sums insured
    - Never invent missing coverage values
    - Detect actual source conflicts
    - Add a missing_reason column
    - Do not modify Collector checkpoints
    - Do not delete existing MySQL records
    - Atomic Excel output replacement
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional

import pymysql
from dotenv import load_dotenv
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo


load_dotenv()

SOURCE_TABLE = "anc_competitor_insurance_v4"
DEFAULT_OUTPUT = Path("anc_insurance_offers_complete.xlsx")

BATCH_SIZE = 2000
EXCEL_MAX_ROWS = 1_048_576
EXCEL_MAX_TEXT = 32767

HEADER_COLOR = "17324D"
HEADER_FONT_COLOR = "FFFFFF"

MONEY_FORMAT = '#,##0.00;[Red](#,##0.00)'
INTEGER_FORMAT = "0"


# ============================================================
# OUTPUT SCHEMA
# ============================================================

COLUMNS = [
    ("province", "จังหวัด"),
    ("brand", "ยี่ห้อรถยนต์"),
    ("model", "รุ่นรถยนต์"),
    ("submodel", "รุ่นย่อยรถยนต์"),
    ("vehicle_year", "ปีรถยนต์"),
    ("car_id", "รหัสรถยนต์"),
    ("insurer", "บริษัทประกันภัย"),
    ("insurer_code", "รหัสบริษัทประกันภัย"),
    ("package_id", "รหัสแพ็กเกจประกันภัย"),
    ("package_name", "ชื่อแพ็กเกจประกันภัย"),
    ("insurance_type", "ประเภทประกันภัย"),
    ("repair_type", "ประเภทการซ่อม"),
    ("premium", "เบี้ยประกันภัย"),
    ("net_premium", "เบี้ยประกันภัยสุทธิ"),
    ("original_premium", "เบี้ยประกันภัยก่อนส่วนลด"),
    ("sum_insured", "ทุนประกันภัย"),
    ("deductible", "ค่าเสียหายส่วนแรก"),
    (
        "bodily_injury_per_person",
        "วงเงินคุ้มครองชีวิตและร่างกายบุคคลภายนอกต่อคน",
    ),
    (
        "bodily_injury_per_accident",
        "วงเงินคุ้มครองชีวิตและร่างกายบุคคลภายนอกต่อครั้ง",
    ),
    (
        "third_party_property",
        "วงเงินคุ้มครองทรัพย์สินบุคคลภายนอก",
    ),
    (
        "fire_theft",
        "วงเงินคุ้มครองรถยนต์กรณีไฟไหม้และโจรกรรม",
    ),
    (
        "flood",
        "วงเงินคุ้มครองรถยนต์กรณีน้ำท่วม",
    ),
    (
        "personal_accident",
        "วงเงินคุ้มครองอุบัติเหตุส่วนบุคคล",
    ),
    (
        "medical_expense",
        "วงเงินค่ารักษาพยาบาล",
    ),
    (
        "bail_bond",
        "วงเงินประกันตัวผู้ขับขี่",
    ),
    (
        "own_damage_coverage",
        "วงเงินคุ้มครองความเสียหายต่อตัวรถยนต์",
    ),
    ("missing_reason", "สาเหตุข้อมูลไม่ครบ"),
]

DATA_COLUMNS = [
    name
    for name, _ in COLUMNS
    if name != "missing_reason"
]

NUMERIC_COLUMNS = {
    "premium",
    "net_premium",
    "original_premium",
    "sum_insured",
    "deductible",
    "bodily_injury_per_person",
    "bodily_injury_per_accident",
    "third_party_property",
    "fire_theft",
    "flood",
    "personal_accident",
    "medical_expense",
    "bail_bond",
    "own_damage_coverage",
}

INTEGER_COLUMNS = {
    "vehicle_year",
    "car_id",
}

IDENTITY_COLUMNS = (
    "province",
    "brand",
    "model",
    "vehicle_year",
    "car_id",
    "insurer_code",
    "package_id",
    "repair_type",
    "sum_insured",
    "premium",
)

REQUIRED_COLUMNS = {
    "record_key",
    "province",
    "brand",
    "model",
    "vehicle_year",
    "car_id",
    "insurer_code",
    "package_id",
    "package_name",
    "repair_type",
    "premium",
    "actual_sum_insured",
    "raw_listing",
    "raw_detail",
}

CRITICAL_FIELDS = (
    "province",
    "brand",
    "model",
    "vehicle_year",
    "car_id",
    "insurer_code",
    "package_id",
    "repair_type",
    "premium",
    "sum_insured",
)

COVERAGE_FIELDS = (
    "deductible",
    "bodily_injury_per_person",
    "bodily_injury_per_accident",
    "third_party_property",
    "fire_theft",
    "flood",
    "personal_accident",
    "medical_expense",
    "bail_bond",
    "own_damage_coverage",
)


# ============================================================
# SOURCE FIELD MAPPING
# ============================================================

DIRECT_MAPPING = {
    "province": "province",
    "brand": "brand",
    "model": "model",
    "submodel": "submodel",
    "vehicle_year": "vehicle_year",
    "car_id": "car_id",
    "insurer": "insurer",
    "insurer_code": "insurer_code",
    "package_id": "package_id",
    "package_name": "package_name",
    "insurance_type": "insurance_type",
    "repair_type": "repair_type",
    "premium": "premium",
    "net_premium": "net_premium",
    "original_premium": "original_premium",
    "actual_sum_insured": "sum_insured",
    "deductible": "deductible",
    "coverage_bodily_injury_person": "bodily_injury_per_person",
    "coverage_bodily_injury_accident": "bodily_injury_per_accident",
    "coverage_third_party_property": "third_party_property",
    "coverage_own_damage": "own_damage_coverage",
    "coverage_fire_theft": "fire_theft",
    "coverage_flood": "flood",
    "personal_accident": "personal_accident",
    "medical_expense": "medical_expense",
    "bail_bond": "bail_bond",
}

LISTING_MAPPING = {
    "insurer_code": "insurer_code",
    "Hid_qsub_vmi_INS": "insurer_code",
    "package_id": "package_id",
    "Hid_qsub_vmi_IDPACK": "package_id",
    "package_name": "package_name",
    "Hid_qsub_vmi_PACKNAME": "package_name",
    "PACKNAMECUS": "package_name",
    "display_package_name": "package_name",
    "premium": "premium",
    "Hid_offer_price": "premium",
    "OFFER_PRICE": "premium",
    "display_premium": "premium",
    "net_premium": "net_premium",
    "Hid_qsub_vmi_NETPREMIUM": "net_premium",
    "original_premium": "original_premium",
    "Hid_qsub_vmi_PREMIUM": "original_premium",
    "sum_insured": "sum_insured",
    "Hid_qsub_vmi_OD": "sum_insured",
    "display_sum_insured": "sum_insured",
    "coverage_fire_theft": "fire_theft",
    "Hid_qsub_vmi_FIRE": "fire_theft",
    "coverage_flood": "flood",
    "Hid_qsub_vmi_FLOOD": "flood",
    "repair_label": "repair_type",
    "FIX": "repair_type",
}

DETAIL_MAPPING = {
    "insurer": "insurer",
    "insurer_name": "insurer",
    "company_name": "insurer",
    "insurance_company": "insurer",
    "ContentPlaceHolder_pack_name": "insurer",
    "package_name": "package_name",
    "ContentPlaceHolder_TitleName": "package_name",
    "premium": "premium",
    "ContentPlaceHolder_pack_offerprice": "premium",
    "original_premium": "original_premium",
    "ContentPlaceHolder_pack_pre": "original_premium",
    "deductible": "deductible",
    "ContentPlaceHolder_mc1_ODDD": "deductible",
    "coverage_bodily_injury_person": "bodily_injury_per_person",
    "ContentPlaceHolder_mc1_BI": "bodily_injury_per_person",
    "coverage_bodily_injury_accident": "bodily_injury_per_accident",
    "ContentPlaceHolder_mc1_BT": "bodily_injury_per_accident",
    "coverage_third_party_property": "third_party_property",
    "ContentPlaceHolder_mc1_TP": "third_party_property",
    "coverage_own_damage": "own_damage_coverage",
    "ContentPlaceHolder_mc1_OD": "own_damage_coverage",
    "coverage_fire_theft": "fire_theft",
    "ContentPlaceHolder_mc1_FIRE": "fire_theft",
    "coverage_flood": "flood",
    "personal_accident": "personal_accident",
    "ContentPlaceHolder_mc1_PA": "personal_accident",
    "medical_expense": "medical_expense",
    "ContentPlaceHolder_mc1_ME": "medical_expense",
    "bail_bond": "bail_bond",
    "ContentPlaceHolder_mc1_BB": "bail_bond",
}


# ============================================================
# NORMALIZATION
# ============================================================

def clean(value: Any) -> Optional[str]:
    if value is None:
        return None

    value = re.sub(
        r"\s+",
        " ",
        str(value).replace("\xa0", " "),
    ).strip()

    return value or None


def clean_insurer(value: Any) -> Optional[str]:
    value = clean(value)

    if not value:
        return None

    # Detail titles may include insurance class and repair type.
    # Remove these labels without altering the company name.

    value = re.sub(
        r"\s*ชั้น\s*[1๑]\s*[“\"']?\s*"
        r"(?:ซ่อมอู่|ซ่อมห้าง|ซ่อมศูนย์)?"
        r"\s*[”\"']?\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"\s*[“\"']?\s*"
        r"(?:ซ่อมอู่|ซ่อมห้าง|ซ่อมศูนย์)"
        r"\s*[”\"']?\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"\s*ประเภท\s*[1๑]\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    return clean(value)


def normalize_repair(value: Any) -> Optional[str]:
    value = clean(value)

    if not value:
        return None

    if "ซ่อมอู่" in value or value == "อู่":
        return "ซ่อมอู่"

    if (
        "ซ่อมห้าง" in value
        or "ซ่อมศูนย์" in value
        or value == "ห้าง"
    ):
        return "ซ่อมห้าง"

    return value


def normalize_type(value: Any) -> Optional[str]:
    value = clean(value)

    if not value:
        return None

    if value.upper() in {"1", "110", "TYPE 1", "ป1"}:
        return "ประเภท 1"

    if re.fullmatch(
        r"(?:ประเภท|ชั้น)\s*[1๑]",
        value,
    ):
        return "ประเภท 1"

    return value


def normalize_money(value: Any) -> Optional[Decimal]:
    if value is None:
        return None

    if isinstance(value, Decimal):
        return value if value.is_finite() else None

    value = clean(value)

    if not value:
        return None

    value = (
        value.replace(",", "")
        .replace("฿", "")
        .replace("บาท", "")
    )

    if not re.fullmatch(
        r"-?\d+(?:\.\d+)?",
        value,
    ):
        return None

    try:
        result = Decimal(value)

        return (
            result
            if result.is_finite()
            else None
        )

    except InvalidOperation:
        return None


def normalize_integer(value: Any) -> Optional[int]:
    number = normalize_money(value)

    if number is None:
        return None

    if number != number.to_integral_value():
        return None

    return int(number)


def normalize_value(
    column: str,
    value: Any,
):
    if column in NUMERIC_COLUMNS:
        return normalize_money(value)

    if column in INTEGER_COLUMNS:
        return normalize_integer(value)

    if column == "insurer":
        return clean_insurer(value)

    if column == "repair_type":
        return normalize_repair(value)

    if column == "insurance_type":
        return normalize_type(value)

    if column in {"brand", "model"}:
        value = clean(value)
        return value.upper() if value else None

    return clean(value)


def parse_json(value: Any) -> dict:
    if isinstance(value, dict):
        return value

    if not value:
        return {}

    try:
        result = json.loads(value)

        return (
            result
            if isinstance(result, dict)
            else {}
        )

    except (
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return {}


def empty(value: Any) -> bool:
    return value is None or value == ""


def comparable(
    column: str,
    value: Any,
):
    value = normalize_value(
        column,
        value,
    )

    if value is None:
        return None

    if isinstance(value, Decimal):
        return value

    if column in {"insurer", "package_name"}:
        return re.sub(
            r"\s+",
            "",
            str(value),
        ).casefold()

    return value


# ============================================================
# DATABASE
# ============================================================

class Database:

    def __init__(self):
        self.connection = None

    @staticmethod
    def env(*names, default=None):
        for name in names:
            value = os.getenv(name)

            if value not in (None, ""):
                return value

        return default

    def connect(self):
        user = self.env(
            "DB_USER",
            "MYSQL_USER",
        )

        if not user:
            raise RuntimeError(
                "DB_USER / MYSQL_USER is required"
            )

        self.connection = pymysql.connect(
            host=self.env(
                "DB_HOST",
                "MYSQL_HOST",
                default="127.0.0.1",
            ),
            port=int(
                self.env(
                    "DB_PORT",
                    "MYSQL_PORT",
                    default="3307",
                )
            ),
            user=user,
            password=self.env(
                "DB_PASSWORD",
                "MYSQL_PASSWORD",
                default="",
            ),
            database=self.env(
                "DB_NAME",
                "MYSQL_DATABASE",
                default="dbd",
            ),
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=15,
            read_timeout=120,
            cursorclass=pymysql.cursors.DictCursor,
        )

        return self

    def source_columns(self) -> set[str]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"SELECT * FROM `{SOURCE_TABLE}` LIMIT 0"
            )

            return {
                description[0]
                for description in cursor.description
            }

    def verify(self):
        columns = self.source_columns()

        missing = REQUIRED_COLUMNS - columns

        if missing:
            raise RuntimeError(
                "Missing source columns: "
                + ", ".join(sorted(missing))
            )

    def count(self) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) AS total FROM `{SOURCE_TABLE}`"
            )

            return int(
                cursor.fetchone()["total"]
            )

    def iter_rows(self):
        last_id = 0

        while True:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM `{SOURCE_TABLE}`
                    WHERE id > %s
                    ORDER BY id
                    LIMIT %s
                    """,
                    (
                        last_id,
                        BATCH_SIZE,
                    ),
                )

                rows = cursor.fetchall()

            if not rows:
                break

            for row in rows:
                yield row

            last_id = rows[-1]["id"]

    def close(self):
        if self.connection is not None:
            self.connection.close()


# ============================================================
# INSURER RESOLUTION
# ============================================================

class InsurerResolver:

    """
    Build a source-derived insurer code -> insurer name map.

    Never fabricate an insurer name from a code.

    A code is resolved only when all observed normalized
    insurer names for that code agree.

    If multiple different names are observed for the same
    code, automatic resolution is disabled for that code.
    """

    def __init__(self):
        self.observations = defaultdict(Counter)
        self.names = {}
        self.ambiguous = {}
        self.resolved_count = 0

    def observe(
        self,
        code: Any,
        name: Any,
    ):
        code = clean(code)
        name = clean_insurer(name)

        if not code or not name:
            return

        code = code.upper()

        canonical_key = comparable(
            "insurer",
            name,
        )

        self.observations[code][
            (canonical_key, name)
        ] += 1

    def build(self):
        for code, observations in self.observations.items():
            grouped = defaultdict(Counter)

            for (canonical, name), count in observations.items():
                grouped[canonical][name] += count

            if len(grouped) != 1:
                self.ambiguous[code] = sorted(
                    {
                        name
                        for _, name in observations
                    }
                )
                continue

            names = next(
                iter(grouped.values())
            )

            self.names[code] = names.most_common(1)[0][0]

    def resolve(
        self,
        code: Any,
    ) -> Optional[str]:
        code = clean(code)

        if not code:
            return None

        return self.names.get(
            code.upper()
        )


# ============================================================
# OFFER MODEL
# ============================================================

@dataclass
class Offer:
    values: dict
    record_key: str
    source_url: str
    detail_status: str
    validation_status: str

    conflicts: dict = field(default_factory=dict)
    invalid_numeric: set = field(default_factory=set)
    missing_reasons: set = field(default_factory=set)

    raw_listing: dict = field(default_factory=dict)
    raw_detail: dict = field(default_factory=dict)


# ============================================================
# TRANSFORMER
# ============================================================

class OfferTransformer:

    def __init__(self):
        self.conflict_columns = Counter()
        self.unknown_fields = Counter()

    @staticmethod
    def raw_field_value(
        raw: dict,
        key: str,
    ):
        if key in raw:
            return raw[key]

        return None

    @staticmethod
    def store_candidate(
        candidates: dict,
        column: str,
        raw_value: Any,
        source_name: str,
        priority: int,
    ):
        if empty(raw_value):
            return

        value = normalize_value(
            column,
            raw_value,
        )

        if value is None:
            return

        candidates[column].append(
            (
                priority,
                source_name,
                value,
            )
        )

    def transform(
        self,
        source: dict,
    ) -> Offer:
        candidates = defaultdict(list)

        raw_listing = parse_json(
            source.get("raw_listing")
        )

        raw_detail = parse_json(
            source.get("raw_detail")
        )

        for source_name, column in DIRECT_MAPPING.items():
            self.store_candidate(
                candidates,
                column,
                source.get(source_name),
                f"db.{source_name}",
                0,
            )

        for source_name, column in DETAIL_MAPPING.items():
            self.store_candidate(
                candidates,
                column,
                self.raw_field_value(
                    raw_detail,
                    source_name,
                ),
                f"detail.{source_name}",
                1,
            )

        for source_name, column in LISTING_MAPPING.items():
            self.store_candidate(
                candidates,
                column,
                self.raw_field_value(
                    raw_listing,
                    source_name,
                ),
                f"listing.{source_name}",
                2,
            )

        values = {
            column: None
            for column in DATA_COLUMNS
        }

        conflicts = {}

        for column, options in candidates.items():
            options.sort(
                key=lambda item: item[0]
            )

            selected = options[0][2]

            values[column] = selected

            disagreements = []

            for priority, source_name, value in options[1:]:
                if comparable(column, value) != comparable(
                    column,
                    selected,
                ):
                    disagreements.append(
                        (
                            source_name,
                            str(value),
                        )
                    )

            if disagreements:
                conflicts[column] = disagreements
                self.conflict_columns[column] += 1

        # Insurance type should be sourced from the canonical
        # database column or independently verified type label.
        # Do not infer Class 1 solely from this exporter.

        # Own-damage amount and general sum insured are different
        # business concepts in first-loss products.
        # Only omit the redundant own-damage amount when equal.

        if (
            values.get("own_damage_coverage") is not None
            and values.get("sum_insured") is not None
            and values["own_damage_coverage"]
            == values["sum_insured"]
        ):
            values["own_damage_coverage"] = None

        offer = Offer(
            values=values,
            record_key=clean(
                source.get("record_key")
            ) or "",
            source_url=clean(
                source.get("source_url")
            ) or "",
            detail_status=clean(
                source.get("detail_status")
            ) or "",
            validation_status=clean(
                source.get("validation_status")
            ) or "",
            conflicts=conflicts,
            raw_listing=raw_listing,
            raw_detail=raw_detail,
        )

        self.validate(
            offer,
            source,
        )

        return offer

    @staticmethod
    def validate(
        offer: Offer,
        source: dict,
    ):
        values = offer.values

        if empty(values.get("insurer")):
            offer.missing_reasons.add(
                "COMPANY_NAME_MISSING"
            )

        if offer.detail_status == "DETAIL_UNAVAILABLE":
            offer.missing_reasons.add(
                "DETAIL_UNAVAILABLE"
            )

        elif offer.detail_status in {
            "PENDING",
            "RETRY_REQUIRED",
        }:
            offer.missing_reasons.add(
                "NOT_VERIFIED"
            )

        if offer.conflicts:
            offer.missing_reasons.add(
                "FIELD_CONFLICT"
            )

        if any(
            empty(values.get(column))
            for column in COVERAGE_FIELDS
        ):
            # Do not claim the insurer does not provide a benefit.
            # Missing coverage may indicate a source limitation.
            if offer.detail_status == "DONE":
                offer.missing_reasons.add(
                    "COVERAGE_NOT_PROVIDED"
                )

        if offer.validation_status in {
            "REVIEW_REQUIRED",
            "PENDING",
        }:
            offer.missing_reasons.add(
                "NOT_VERIFIED"
            )

        if (
            values.get("premium") is not None
            and values["premium"] <= 0
        ):
            offer.missing_reasons.add(
                "NOT_VERIFIED"
            )

        if (
            values.get("sum_insured") is not None
            and values["sum_insured"] <= 0
        ):
            offer.missing_reasons.add(
                "NOT_VERIFIED"
            )


# ============================================================
# RESOLUTION AND QUALITY CHECKS
# ============================================================

class OfferResolver:

    def __init__(self):
        self.insurers = InsurerResolver()
        self.stats = Counter()

    def build_reference(
        self,
        offers: list[Offer],
    ):
        # Only use a direct insurer name found in the source.
        # Do not allow an inferred name to become its own
        # reference.

        for offer in offers:
            code = offer.values.get(
                "insurer_code"
            )

            name = offer.values.get(
                "insurer"
            )

            if not code or not name:
                continue

            self.insurers.observe(
                code,
                name,
            )

        self.insurers.build()

    def resolve(
        self,
        offers: list[Offer],
    ):
        self.build_reference(
            offers
        )

        for offer in offers:
            values = offer.values

            if not values.get("insurer"):
                resolved = self.insurers.resolve(
                    values.get("insurer_code")
                )

                if resolved:
                    values["insurer"] = resolved

                    offer.missing_reasons.discard(
                        "COMPANY_NAME_MISSING"
                    )

                    self.stats[
                        "insurer_names_resolved"
                    ] += 1

                else:
                    self.stats[
                        "insurer_names_unresolved"
                    ] += 1

            # A different insurer name already in the record
            # must be preserved. Do not overwrite it silently.

        self.stats[
            "ambiguous_insurer_codes"
        ] = len(
            self.insurers.ambiguous
        )


# ============================================================
# BUSINESS-LEVEL DEDUPLICATION
# ============================================================

class OfferDeduplicator:

    def __init__(self):
        self.unique = {}
        self.duplicates = 0
        self.conflicts = []

    @staticmethod
    def identity(
        offer: Offer,
    ):
        values = offer.values

        business_key = tuple(
            comparable(
                column,
                values.get(column),
            )
            for column in IDENTITY_COLUMNS
        )

        # When essential identifiers are absent, do not
        # aggressively merge potentially different offers.
        if any(
            value is None
            for value in business_key
        ):
            return (
                "source",
                offer.record_key,
            )

        return (
            "business",
            business_key,
        )

    @staticmethod
    def merge(
        existing: Offer,
        incoming: Offer,
    ) -> bool:
        differences = {}

        updates = {}

        for column in DATA_COLUMNS:
            previous = existing.values.get(
                column
            )

            current = incoming.values.get(
                column
            )

            if empty(previous) and not empty(current):
                updates[column] = current
                continue

            if empty(current):
                continue

            if comparable(
                column,
                previous,
            ) != comparable(
                column,
                current,
            ):
                differences[column] = (
                    previous,
                    current,
                )

        if differences:
            return False

        existing.values.update(
            updates
        )

        existing.missing_reasons |= incoming.missing_reasons

        return True

    def add(self, offer: Offer):
        key = self.identity(
            offer
        )

        previous = self.unique.get(
            key
        )

        if previous is None:
            self.unique[key] = offer
            return

        if self.merge(
            previous,
            offer,
        ):
            self.duplicates += 1
            return

        self.conflicts.append(
            (
                previous.record_key,
                offer.record_key,
            )
        )

        # Preserve a conflicting variant rather than dropping it.
        self.unique[
            ("conflict", offer.record_key)
        ] = offer

    def results(self):
        return sorted(
            self.unique.values(),
            key=lambda offer: (
                offer.values.get("province") or "",
                offer.values.get("brand") or "",
                offer.values.get("model") or "",
                offer.values.get("vehicle_year") or 0,
                offer.values.get("car_id") or 0,
                offer.values.get("repair_type") or "",
                offer.values.get("insurer_code") or "",
                offer.values.get("package_id") or "",
                offer.values.get("sum_insured") or Decimal(0),
                offer.values.get("premium") or Decimal(0),
            ),
        )


# ============================================================
# MISSING REASON CLASSIFICATION
# ============================================================

REASON_ORDER = (
    "COMPANY_NAME_MISSING",
    "DETAIL_UNAVAILABLE",
    "COVERAGE_NOT_PROVIDED",
    "FIELD_CONFLICT",
    "PARSING_FAILED",
    "NOT_VERIFIED",
)


def missing_reason(
    offer: Offer,
) -> Optional[str]:
    reasons = set(
        offer.missing_reasons
    )

    values = offer.values

    if not values.get("insurer"):
        reasons.add(
            "COMPANY_NAME_MISSING"
        )

    if offer.detail_status == "DETAIL_UNAVAILABLE":
        reasons.add(
            "DETAIL_UNAVAILABLE"
        )

    elif offer.detail_status != "DONE":
        reasons.add(
            "NOT_VERIFIED"
        )

    if offer.conflicts:
        reasons.add(
            "FIELD_CONFLICT"
        )

    # Only mark coverage missing when details were successfully
    # fetched but at least one coverage field remains blank.
    #
    # This is an observation about available source data,
    # not a claim that the insurer excludes the coverage.

    if offer.detail_status == "DONE":
        coverage_values = (
            values.get(column)
            for column in COVERAGE_FIELDS
            if column != "own_damage_coverage"
        )

        if any(
            value is None
            for value in coverage_values
        ):
            reasons.add(
                "COVERAGE_NOT_PROVIDED"
            )

    if any(
        empty(values.get(column))
        for column in CRITICAL_FIELDS
    ):
        reasons.add(
            "NOT_VERIFIED"
        )

    ordered = [
        reason
        for reason in REASON_ORDER
        if reason in reasons
    ]

    return (
        ";".join(ordered)
        if ordered
        else None
    )


# ============================================================
# EXCEL
# ============================================================

class ExcelExporter:

    def __init__(
        self,
        output: Path,
        overwrite: bool,
    ):
        self.output = output.resolve()
        self.overwrite = overwrite

    @staticmethod
    def cell_value(
        column: str,
        value: Any,
    ):
        if value is None:
            return None

        if column in NUMERIC_COLUMNS:
            if isinstance(value, Decimal):
                return (
                    float(value)
                    if value.is_finite()
                    else None
                )

            parsed = normalize_money(
                value
            )

            return float(parsed) if parsed is not None else None

        if column in INTEGER_COLUMNS:
            return normalize_integer(
                value
            )

        text = clean(value)

        if not text:
            return None

        if text.startswith(
            ("=", "+", "-", "@")
        ):
            text = "'" + text

        return text[:EXCEL_MAX_TEXT]

    def export(
        self,
        offers: list[Offer],
    ):
        if not offers:
            raise RuntimeError(
                "No records available for export"
            )

        if len(offers) + 1 > EXCEL_MAX_ROWS:
            raise RuntimeError(
                "Excel row limit exceeded"
            )

        if self.output.exists() and not self.overwrite:
            raise FileExistsError(
                f"Output exists: {self.output}. "
                "Use --overwrite."
            )

        self.output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        workbook = Workbook()

        sheet = workbook.active
        sheet.title = "insurance_offers"

        headers = [
            title
            for _, title in COLUMNS
        ]

        sheet.append(
            headers
        )

        for offer in offers:
            offer.values["missing_reason"] = missing_reason(
                offer
            )

            sheet.append(
                [
                    self.cell_value(
                        column,
                        offer.values.get(column),
                    )
                    for column, _ in COLUMNS
                ]
            )

        sheet.freeze_panes = "A2"
        sheet.sheet_view.showGridLines = False
        sheet.row_dimensions[1].height = 34

        for cell in sheet[1]:
            cell.font = Font(
                bold=True,
                color=HEADER_FONT_COLOR,
            )

            cell.fill = PatternFill(
                fill_type="solid",
                fgColor=HEADER_COLOR,
            )

            cell.alignment = Alignment(
                horizontal="center",
                vertical="center",
                wrap_text=True,
            )

        width_overrides = {
            "province": 19,
            "brand": 19,
            "model": 20,
            "submodel": 34,
            "vehicle_year": 15,
            "car_id": 15,
            "insurer": 37,
            "insurer_code": 21,
            "package_id": 22,
            "package_name": 49,
            "insurance_type": 20,
            "repair_type": 20,
            "missing_reason": 56,
        }

        for index, (
            column,
            title,
        ) in enumerate(
            COLUMNS,
            start=1,
        ):
            letter = get_column_letter(
                index
            )

            sheet.column_dimensions[
                letter
            ].width = width_overrides.get(
                column,
                max(
                    19,
                    min(
                        35,
                        len(title) + 4,
                    ),
                ),
            )

        for row in sheet.iter_rows(
            min_row=2,
        ):
            for index, cell in enumerate(row):
                column = COLUMNS[index][0]

                if column in NUMERIC_COLUMNS:
                    cell.number_format = MONEY_FORMAT

                elif column in INTEGER_COLUMNS:
                    cell.number_format = INTEGER_FORMAT

                if column == "missing_reason":
                    cell.alignment = Alignment(
                        vertical="center",
                        wrap_text=False,
                    )

        final_column = get_column_letter(
            len(COLUMNS)
        )

        table = Table(
            displayName="InsuranceOffers",
            ref=f"A1:{final_column}{sheet.max_row}",
        )

        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )

        sheet.add_table(table)

        temporary = self.output.with_name(
            self.output.stem
            + ".writing"
            + self.output.suffix
        )

        try:
            workbook.save(
                temporary
            )

            workbook.close()

            try:
                temporary.replace(
                    self.output
                )

            except PermissionError as exc:
                raise PermissionError(
                    f"Cannot replace {self.output}. "
                    "Close the file in Excel and try again."
                ) from exc

        finally:
            try:
                workbook.close()
            except Exception:
                pass

            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

        return len(offers)


# ============================================================
# INSPECT
# ============================================================

def inspect(
    db: Database,
):
    listing_fields = Counter()
    detail_fields = Counter()
    status_counts = Counter()

    total = 0

    for row in db.iter_rows():
        total += 1

        status_counts[
            row.get("detail_status") or "UNKNOWN"
        ] += 1

        listing = parse_json(
            row.get("raw_listing")
        )

        detail = parse_json(
            row.get("raw_detail")
        )

        for key in listing:
            listing_fields[key] += 1

        for key in detail:
            detail_fields[key] += 1

    print()
    print("ANC SOURCE INSPECTION")
    print("=" * 72)
    print(f"Records: {total:,}")

    print()
    print("DETAIL STATUS")
    print("-" * 72)

    for status, count in status_counts.most_common():
        print(f"{status:<32} {count:>10,}")

    print()
    print("LISTING FIELDS")
    print("-" * 72)

    for key, count in listing_fields.most_common():
        print(f"{key:<55} {count:>10,}")

    print()
    print("DETAIL FIELDS")
    print("-" * 72)

    for key, count in detail_fields.most_common():
        print(f"{key:<55} {count:>10,}")


# ============================================================
# EXPORT PIPELINE
# ============================================================

def run(
    db: Database,
    output: Path,
    overwrite: bool,
    include_partial: bool,
    strict: bool,
):
    transformer = OfferTransformer()
    resolver = OfferResolver()
    deduplicator = OfferDeduplicator()

    source_total = db.count()

    offers = []
    stats = Counter()

    print()
    print("ANC COMPLETE INSURANCE OFFER EXPORTER v2.0")
    print("=" * 72)

    print(f"Source        : {SOURCE_TABLE}")
    print(f"Source rows   : {source_total:,}")
    print(f"Output        : {output.resolve()}")
    print(f"Include partial: {include_partial}")
    print(f"Strict        : {strict}")

    print("=" * 72)

    for row in db.iter_rows():
        stats["read"] += 1

        offer = transformer.transform(
            row
        )

        missing_critical = [
            column
            for column in CRITICAL_FIELDS
            if empty(
                offer.values.get(column)
            )
        ]

        if missing_critical:
            stats["critical_missing"] += 1

            if strict:
                raise RuntimeError(
                    "Missing critical fields: "
                    f"{offer.record_key} -> "
                    + ", ".join(missing_critical)
                )

            # Invalid offer identities are not exported.
            continue

        premium = offer.values.get(
            "premium"
        )

        sum_insured = offer.values.get(
            "sum_insured"
        )

        if (
            premium is None
            or premium <= 0
            or sum_insured is None
            or sum_insured <= 0
        ):
            stats["invalid_amount"] += 1

            if strict:
                raise RuntimeError(
                    f"Invalid amount: {offer.record_key}"
                )

            continue

        if not include_partial:
            if offer.detail_status != "DONE":
                stats["excluded_partial"] += 1
                continue

            if offer.validation_status not in {
                "DETAIL_VERIFIED",
                "LISTING_VERIFIED",
            }:
                stats["excluded_unverified"] += 1
                continue

        offers.append(
            offer
        )

        stats["accepted"] += 1

        if stats["read"] % BATCH_SIZE == 0:
            print(
                f"\rREAD={stats['read']:,}/{source_total:,} "
                f"ACCEPTED={stats['accepted']:,} "
                f"CRITICAL_MISSING={stats['critical_missing']:,}",
                end="",
                flush=True,
            )

    print()

    resolver.resolve(
        offers
    )

    for offer in offers:
        deduplicator.add(
            offer
        )

    results = deduplicator.results()

    if strict and deduplicator.conflicts:
        raise RuntimeError(
            "Conflicting business identities detected: "
            f"{len(deduplicator.conflicts):,}"
        )

    exporter = ExcelExporter(
        output=output,
        overwrite=overwrite,
    )

    exported = exporter.export(
        results
    )

    reason_counts = Counter()

    for offer in results:
        reasons = missing_reason(
            offer
        )

        if reasons:
            for reason in reasons.split(";"):
                reason_counts[reason] += 1

    print()
    print("EXPORT SUMMARY")
    print("=" * 72)

    summary = {
        "Source rows": source_total,
        "Rows inspected": stats["read"],
        "Accepted rows": stats["accepted"],
        "Critical fields missing": stats["critical_missing"],
        "Invalid amounts": stats["invalid_amount"],
        "Excluded partial": stats["excluded_partial"],
        "Excluded unverified": stats["excluded_unverified"],
        "Insurer names resolved": resolver.stats[
            "insurer_names_resolved"
        ],
        "Insurer names unresolved": resolver.stats[
            "insurer_names_unresolved"
        ],
        "Ambiguous insurer codes": resolver.stats[
            "ambiguous_insurer_codes"
        ],
        "Business duplicates removed": deduplicator.duplicates,
        "Business identity conflicts": len(
            deduplicator.conflicts
        ),
        "Unique offers exported": exported,
    }

    for name, value in summary.items():
        print(
            f"{name:<32}: {value:>10,}"
        )

    print()
    print("MISSING REASONS")
    print("-" * 72)

    for reason in REASON_ORDER:
        print(
            f"{reason:<32}: "
            f"{reason_counts[reason]:>10,}"
        )

    print()
    print("FIELD CONFLICTS")
    print("-" * 72)

    for column, count in transformer.conflict_columns.most_common():
        print(
            f"{column:<32}: {count:>10,}"
        )

    print()
    print("OUTPUT")
    print("-" * 72)

    print(output.resolve())

    print("=" * 72)

    return exported


# ============================================================
# ARGUMENTS
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "ANC Complete Insurance Offer Exporter v2.0"
        )
    )

    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
    )

    parser.add_argument(
        "--include-partial",
        action="store_true",
        default=True,
    )

    parser.add_argument(
        "--complete-only",
        dest="include_partial",
        action="store_false",
    )

    parser.add_argument(
        "--strict",
        action="store_true",
    )

    parser.add_argument(
        "--inspect",
        action="store_true",
    )

    args = parser.parse_args()

    if Path(args.output).suffix.lower() != ".xlsx":
        parser.error(
            "--output must be an .xlsx file"
        )

    return args


# ============================================================
# MAIN
# ============================================================

def main():
    args = parse_args()

    db = Database()

    try:
        db.connect()
        db.verify()

        if args.inspect:
            inspect(db)
            return 0

        run(
            db=db,
            output=Path(args.output),
            overwrite=args.overwrite,
            include_partial=args.include_partial,
            strict=args.strict,
        )

        return 0

    except KeyboardInterrupt:
        print()
        print("Export interrupted.")
        return 130

    except Exception as exc:
        print()
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        return 1

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())