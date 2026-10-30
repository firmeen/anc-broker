#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ANC Broker Collector v4.1
=========================

Package-level Live Dashboard + Durable Resume

Updates the existing anc_collector.py without changing:
    - Existing MySQL tables
    - Existing record keys
    - Existing checkpoint keys
    - Existing catalog files
    - Existing HTTP, parsing and vehicle discovery logic

Requirements:
    pip install requests beautifulsoup4 pymysql python-dotenv rich truststore

Commands:
    python anc_collector.py --dry-run
    python anc_collector.py --catalog
    python anc_collector.py --limit 1 --delay 2 --debug
    python anc_collector.py --limit 1 --delay 2 --resume
    python anc_collector.py --resume
    python anc_collector.py --export-only

Package statuses:
    PENDING
    DONE
    DETAIL_UNAVAILABLE
    RETRY_REQUIRED

Important:
    - Previously collected records are preserved.
    - Previously completed details are not requested again during resume.
    - Existing v4 tables are reused.
    - Listing records are committed before detail enrichment.
    - The terminal updates after every package.
    - Ctrl+C safely preserves previously committed records.
    - No CAPTCHA bypass or authentication bypass.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import random
import re
import signal
import sys
import time

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser


try:
    import truststore

    truststore.inject_into_ssl()

    import pymysql
    import requests

    from bs4 import BeautifulSoup
    from dotenv import load_dotenv
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    from rich import box
    from rich.align import Align
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )
    from rich.table import Table
    from rich.text import Text

except ImportError as exc:
    print(f"Missing dependency: {exc}")
    print(
        "python -m pip install "
        "requests beautifulsoup4 pymysql "
        "python-dotenv rich truststore"
    )
    sys.exit(1)


load_dotenv()

BASE_URL = "https://www.ancbroker.com"
CLASS_ONE_PATH = "/ประกันภัยรถยนต์-ชั้น1"
CLASS_ONE_URL = BASE_URL + quote(CLASS_ONE_PATH, safe="/-")

RECORD_TABLE = "anc_competitor_insurance_v4"
TARGET_TABLE = "anc_competitor_target_v4"
BRANCH_TABLE = "anc_competitor_branch_v4"
PACKAGE_TABLE = "anc_competitor_package_checkpoint_v4"

CSV_PATH = Path("anc_competitor_insurance_24_provinces.csv")
CATALOG_PATH = Path("anc_vehicle_catalog.json")
LOG_PATH = Path("anc_collector.log")
DEBUG_PATH = Path("anc_debug")

TIMEOUT = (15, 45)
DEFAULT_DELAY = 2.0
MAX_REQUESTS = 20000

USER_AGENT = (
    "ANCInsuranceResearchCollector/4.1 "
    "(public-data research)"
)

REPAIR_TYPES = ("ซ่อมอู่", "ซ่อมห้าง")

console = Console()
logger = logging.getLogger("anc_collector")
logger.setLevel(logging.INFO)

if not logger.handlers:
    handler = logging.FileHandler(
        LOG_PATH,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s"
        )
    )
    logger.addHandler(handler)

STOP_REQUESTED = False


PROVINCES_BY_REGION = {
    "ภาคเหนือ": [
        "สุโขทัย",
        "พะเยา",
        "เพชรบูรณ์",
        "กำแพงเพชร",
        "แพร่",
        "ชัยนาท",
    ],
    "ภาคกลาง": [
        "สุพรรณบุรี",
    ],
    "ภาคใต้": [
        "ประจวบคีรีขันธ์",
        "พังงา",
        "ระนอง",
    ],
    "ภาคตะวันออกเฉียงเหนือ": [
        "หนองคาย",
        "บึงกาฬ",
        "นครพนม",
        "สกลนคร",
        "เลย",
        "หนองบัวลำภู",
        "ศรีสะเกษ",
        "อำนาจเจริญ",
        "มุกดาหาร",
        "ยโสธร",
    ],
    "ภาคตะวันออก": [
        "จันทบุรี",
        "ปราจีนบุรี",
        "ตราด",
        "ฉะเชิงเทรา",
    ],
}

PROVINCES = [
    (region, province)
    for region, values in PROVINCES_BY_REGION.items()
    for province in values
]


@dataclass(frozen=True)
class Vehicle:
    brand: str
    model: str
    year: int


VEHICLES = [
    Vehicle("HONDA", "CITY", 2020),
    Vehicle("MAZDA", "2", 2019),
    Vehicle("TOYOTA", "YARIS", 2019),
    Vehicle("HONDA", "CIVIC", 2019),
    Vehicle("HONDA", "JAZZ", 2018),
    Vehicle("TOYOTA", "VIOS", 2013),
    Vehicle("TOYOTA", "C-HR", 2019),
    Vehicle("NISSAN", "ALMERA", 2020),
    Vehicle("HONDA", "HR-V", 2018),
    Vehicle("ISUZU", "D-MAX", 2020),
    Vehicle("HONDA", "CITY", 2022),
    Vehicle("TOYOTA", "YARIS", 2023),
    Vehicle("HONDA", "HR-V", 2023),
    Vehicle("TOYOTA", "YARIS CROSS", 2024),
    Vehicle("MAZDA", "2", 2022),
    Vehicle("HONDA", "CIVIC", 2023),
    Vehicle("TOYOTA", "COROLLA CROSS", 2022),
    Vehicle("MAZDA", "CX-30", 2022),
    Vehicle("MAZDA", "CX-3", 2022),
    Vehicle("MITSUBISHI", "XFORCE", 2025),
]

assert len(PROVINCES) == 24
assert len(VEHICLES) == 20
assert len(set(VEHICLES)) == 20


# ============================================================
# UTILITIES
# ============================================================

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def clean(value: Any) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(value or "").replace("\xa0", " "),
    ).strip()


def key_text(value: Any) -> str:
    return re.sub(
        r"[^A-Z0-9ก-๙]",
        "",
        clean(value).upper(),
    )


def tokens(value: Any) -> list[str]:
    return re.findall(
        r"[A-Z0-9]+",
        clean(value).upper(),
    )


def hash_key(*items: Any) -> str:
    payload = json.dumps(
        items,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()


def money(value: Any) -> Optional[Decimal]:
    value = (
        clean(value)
        .replace(",", "")
        .replace("฿", "")
        .replace("บาท", "")
    )

    match = re.search(
        r"-?\d+(?:\.\d+)?",
        value,
    )

    if not match:
        return None

    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def money_text(value: Optional[Decimal]) -> str:
    if value is None:
        return "-"
    return f"{value:,.2f}"


def number_as_str(
    value: Optional[Decimal],
) -> Optional[str]:
    return str(value) if value is not None else None


def safe_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )


def absolute_url(base: str, href: str) -> str:
    joined = urljoin(base, href)
    parsed = urlparse(joined)

    path = quote(
        unquote(parsed.path),
        safe="/-._~",
    )

    return parsed._replace(
        path=path
    ).geturl()


def same_origin(url: str) -> bool:
    expected = urlparse(BASE_URL)
    actual = urlparse(url)

    return (
        actual.scheme == "https"
        and actual.hostname == expected.hostname
        and (actual.port or 443) == 443
    )


def get_text(
    soup: BeautifulSoup,
    element_id: str,
) -> str:
    element = soup.find(id=element_id)

    if element is None:
        return ""

    return clean(
        element.get_text(
            " ",
            strip=True,
        )
    )


def get_value(element: Any) -> str:
    if element.name == "input":
        return clean(
            element.get("value", "")
        )

    if element.name == "a":
        return clean(
            element.get("href", "")
        )

    return clean(
        element.get_text(
            " ",
            strip=True,
        )
    )


def target_key(
    province: str,
    vehicle: Vehicle,
) -> str:
    return hash_key(
        "ANC-V4-TARGET",
        province,
        vehicle.brand,
        vehicle.model,
        vehicle.year,
    )


def branch_key(
    province: str,
    vehicle: Vehicle,
    car_id: int,
    repair: str,
) -> str:
    return hash_key(
        "ANC-V4-BRANCH",
        province,
        vehicle.brand,
        vehicle.model,
        vehicle.year,
        car_id,
        repair,
    )


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))

    hours, remaining = divmod(seconds, 3600)
    minutes, seconds = divmod(remaining, 60)

    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    return f"{minutes:02d}:{seconds:02d}"


def format_eta(seconds: Optional[float]) -> str:
    if seconds is None:
        return "calculating"

    return format_duration(seconds)


def on_signal(signum, frame):
    global STOP_REQUESTED

    STOP_REQUESTED = True

    logger.warning(
        "STOP REQUESTED | signal=%s",
        signum,
    )


signal.signal(
    signal.SIGINT,
    on_signal,
)

if hasattr(signal, "SIGTERM"):
    signal.signal(
        signal.SIGTERM,
        on_signal,
    )


# ============================================================
# EXCEPTIONS
# ============================================================

class CollectorError(Exception):
    pass


class AccessBlocked(CollectorError):
    pass


class DiscoveryError(CollectorError):
    pass


class ValidationError(CollectorError):
    pass


class WebFormsError(CollectorError):
    pass


# ============================================================
# DATA MODELS
# ============================================================

@dataclass(frozen=True)
class CatalogEntry:
    brand: str
    model: str
    submodel: str
    car_id: int
    reference_year: int
    reference_url: str


@dataclass
class Package:
    branch_key: str
    record_key: str

    source_url: str
    listing_url: str

    province: str
    region: str

    brand: str
    model: str
    submodel: str
    car_id: int
    vehicle_year: int

    repair_type: str

    package_id: str = ""
    package_name: str = ""
    insurer: str = ""
    insurer_code: str = ""

    premium: Optional[Decimal] = None
    net_premium: Optional[Decimal] = None
    original_premium: Optional[Decimal] = None
    sum_insured: Optional[Decimal] = None
    deductible: Optional[Decimal] = None

    coverage_bodily_injury_person: Optional[Decimal] = None
    coverage_bodily_injury_accident: Optional[Decimal] = None
    coverage_third_party_property: Optional[Decimal] = None
    coverage_own_damage: Optional[Decimal] = None
    coverage_fire_theft: Optional[Decimal] = None
    coverage_flood: Optional[Decimal] = None
    personal_accident: Optional[Decimal] = None
    medical_expense: Optional[Decimal] = None
    bail_bond: Optional[Decimal] = None

    listing_status: str = "LISTING_CAPTURED"
    detail_status: str = "PENDING"
    validation_status: str = "REVIEW_REQUIRED"
    validation_notes: str = ""

    raw_listing: dict = field(default_factory=dict)
    raw_detail: dict = field(default_factory=dict)


# ============================================================
# LIVE OBSERVABILITY
# ============================================================

class CollectorDashboard:
    """
    Live package-level dashboard.

    All counters are updated by actual collection events.
    Previously saved database records are reported separately
    from records written during the current run.
    """

    def __init__(self):
        self.started_at = time.monotonic()

        self.status = "INITIALIZING"
        self.operation = "Preparing collector"

        self.target_index = 0
        self.target_total = 0

        self.province = "-"
        self.brand = "-"
        self.model = "-"
        self.year = "-"

        self.submodel_index = 0
        self.submodel_total = 0
        self.car_id = "-"
        self.submodel = "-"

        self.repair_index = 0
        self.repair_total = len(REPAIR_TYPES)
        self.repair_type = "-"

        self.package_index = 0
        self.package_total = 0
        self.package_name = "-"
        self.package_insurer = "-"
        self.package_id = "-"
        self.package_premium = "-"
        self.package_sum_insured = "-"

        self.http_requests = 0
        self.http_success = 0
        self.http_404 = 0
        self.http_errors = 0

        self.saved_this_run = 0
        self.updated_this_run = 0
        self.skipped_this_run = 0

        self.detail_done_this_run = 0
        self.detail_404_this_run = 0
        self.detail_failed_this_run = 0

        self.db_records = 0
        self.db_detail_done = 0
        self.db_detail_unavailable = 0
        self.db_pending = 0

        self.targets_done = 0
        self.targets_partial = 0
        self.targets_failed = 0
        self.targets_skipped = 0

        self.branches_done = 0
        self.branches_partial = 0
        self.branches_skipped = 0

        self.package_started_at = 0.0

        self.package_times = deque(maxlen=30)
        self.activities = deque(maxlen=7)

        self._live = None
        self._last_render = 0.0

    def start(self):
        self._live = Live(
            self.render(),
            console=console,
            refresh_per_second=4,
            screen=False,
            transient=False,
            auto_refresh=True,
        )
        self._live.start()
        self.refresh(force=True)

    def stop(self):
        if self._live is not None:
            self.refresh(force=True)
            self._live.stop()
            self._live = None

    def refresh(self, force=False):
        if self._live is None:
            return

        now = time.monotonic()

        if not force and now - self._last_render < 0.15:
            return

        self._last_render = now
        self._live.update(
            self.render(),
            refresh=True,
        )

    def event(
        self,
        message: str,
        level: str = "INFO",
    ):
        timestamp = datetime.now().strftime("%H:%M:%S")

        styles = {
            "INFO": "cyan",
            "OK": "green",
            "WARN": "yellow",
            "ERROR": "red",
            "SKIP": "bright_black",
        }

        style = styles.get(level, "white")

        self.activities.appendleft(
            (
                timestamp,
                level,
                message,
                style,
            )
        )

        logger.info(
            "UI EVENT | %s | %s",
            level,
            message,
        )

        self.refresh()

    def set_target(
        self,
        index: int,
        total: int,
        province: str,
        vehicle: Vehicle,
    ):
        self.target_index = index
        self.target_total = total

        self.province = province
        self.brand = vehicle.brand
        self.model = vehicle.model
        self.year = vehicle.year

        self.submodel_index = 0
        self.submodel_total = 0

        self.package_index = 0
        self.package_total = 0

        self.status = "RUNNING"
        self.operation = "Resolving vehicle"

        self.event(
            f"Target {index}/{total}: "
            f"{province} | "
            f"{vehicle.brand} {vehicle.model} {vehicle.year}"
        )

    def set_submodel(
        self,
        index: int,
        total: int,
        entry: CatalogEntry,
    ):
        self.submodel_index = index
        self.submodel_total = total

        self.car_id = entry.car_id
        self.submodel = entry.submodel

        self.repair_index = 0

        self.package_index = 0
        self.package_total = 0

        self.operation = "Resolving submodel"

        self.event(
            f"Submodel {index}/{total}: "
            f"Car ID {entry.car_id} | {entry.submodel}"
        )

    def set_repair(
        self,
        index: int,
        repair_type: str,
    ):
        self.repair_index = index
        self.repair_type = repair_type

        self.package_index = 0
        self.package_total = 0

        self.package_name = "-"
        self.package_insurer = "-"
        self.package_id = "-"

        self.operation = "Loading filtered listing"

        self.event(
            f"Repair {index}/{self.repair_total}: {repair_type}"
        )

    def set_listing(
        self,
        package_count: int,
    ):
        self.package_total = package_count
        self.package_index = 0

        self.operation = "Saving listing packages"

        self.event(
            f"Listing parsed: {package_count} packages",
            "OK",
        )

    def set_package(
        self,
        index: int,
        total: int,
        package: Package,
        operation: str,
    ):
        self.package_index = index
        self.package_total = total

        self.package_name = package.package_name or "-"
        self.package_insurer = (
            package.insurer
            or package.insurer_code
            or "-"
        )
        self.package_id = package.package_id or "-"

        self.package_premium = money_text(
            package.premium
        )
        self.package_sum_insured = money_text(
            package.sum_insured
        )

        self.operation = operation
        self.package_started_at = time.monotonic()

        self.refresh(force=True)

    def finish_package(
        self,
        status: str,
        message: str,
        level: str = "OK",
    ):
        if self.package_started_at:
            duration = (
                time.monotonic()
                - self.package_started_at
            )

            if duration > 0:
                self.package_times.append(duration)

        self.package_started_at = 0.0

        self.operation = status

        self.event(
            f"[{self.package_index}/{self.package_total}] "
            f"{message}",
            level,
        )

    def update_http(
        self,
        total: int,
        success: int,
        not_found: int,
        errors: int,
    ):
        self.http_requests = total
        self.http_success = success
        self.http_404 = not_found
        self.http_errors = errors
        self.refresh()

    def update_database(
        self,
        records: int,
        detail_done: int,
        detail_unavailable: int,
        pending: int,
    ):
        self.db_records = records
        self.db_detail_done = detail_done
        self.db_detail_unavailable = detail_unavailable
        self.db_pending = pending
        self.refresh()

    def package_eta(self) -> Optional[float]:
        remaining = max(
            0,
            self.package_total - self.package_index,
        )

        if not self.package_times:
            return None

        average = sum(self.package_times) / len(
            self.package_times
        )

        return remaining * average

    @staticmethod
    def progress_bar(
        value: int,
        total: int,
        width: int = 24,
    ) -> Text:
        if total <= 0:
            return Text("─" * width, style="bright_black")

        ratio = min(
            1.0,
            max(0.0, value / total),
        )

        filled = int(width * ratio)

        bar = Text()

        bar.append("━" * filled, style="cyan")
        bar.append("─" * (width - filled), style="bright_black")

        return bar

    def render(self):
        elapsed = time.monotonic() - self.started_at

        header = Table.grid(expand=True)
        header.add_column(ratio=1)
        header.add_column(justify="right")

        status_style = {
            "INITIALIZING": "yellow",
            "RUNNING": "green",
            "INTERRUPTED": "yellow",
            "BLOCKED": "red",
            "COMPLETED": "green",
            "FAILED": "red",
        }.get(self.status, "cyan")

        header.add_row(
            Text(
                "ANC COLLECTOR v4.1",
                style="bold cyan",
            ),
            Text(
                f"{self.status}  |  {format_duration(elapsed)}",
                style=status_style,
            ),
        )

        target = Table.grid(expand=True, padding=(0, 1))
        target.add_column(ratio=1)
        target.add_column(ratio=1)

        target.add_row(
            Text("TARGET", style="bold bright_black"),
            Text("SUBMODEL", style="bold bright_black"),
        )

        target.add_row(
            Text(
                f"{self.province} | "
                f"{self.brand} {self.model} {self.year}",
                style="bold white",
            ),
            Text(
                f"{self.car_id} | {self.submodel}",
                style="white",
                overflow="ellipsis",
            ),
        )

        target.add_row(
            Text(
                f"{self.target_index}/{self.target_total}",
                style="cyan",
            ),
            Text(
                f"{self.submodel_index}/{self.submodel_total}",
                style="cyan",
            ),
        )

        target.add_row(
            self.progress_bar(
                self.target_index - (
                    1 if self.status == "RUNNING" else 0
                ),
                self.target_total,
            ),
            self.progress_bar(
                self.submodel_index - (
                    1 if self.submodel_index else 0
                ),
                self.submodel_total,
            ),
        )

        package_table = Table.grid(expand=True, padding=(0, 1))
        package_table.add_column(ratio=1)
        package_table.add_column(justify="right")

        package_table.add_row(
            Text(
                f"PACKAGE  {self.package_index}/{self.package_total}",
                style="bold cyan",
            ),
            Text(
                f"ETA {format_eta(self.package_eta())}",
                style="bright_black",
            ),
        )

        package_table.add_row(
            self.progress_bar(
                self.package_index,
                self.package_total,
                width=38,
            ),
            Text(
                (
                    f"{100 * self.package_index / self.package_total:.1f}%"
                    if self.package_total
                    else "0.0%"
                ),
                style="white",
            ),
        )

        package_table.add_row(
            Text(
                f"{self.repair_type} | "
                f"{self.package_insurer} | "
                f"{self.package_name}",
                style="white",
                overflow="ellipsis",
            ),
            Text(
                f"ID {self.package_id}",
                style="bright_black",
            ),
        )

        package_table.add_row(
            Text(
                f"Premium: {self.package_premium} THB"
            ),
            Text(
                f"Sum insured: {self.package_sum_insured} THB"
            ),
        )

        package_table.add_row(
            Text(
                f"Current: {self.operation}",
                style="yellow",
            ),
            Text(
                f"Repair {self.repair_index}/{self.repair_total}",
                style="bright_black",
            ),
        )

        metrics = Table(
            box=box.SIMPLE,
            show_header=True,
            expand=True,
            padding=(0, 1),
        )

        metrics.add_column(
            "DATABASE",
            style="cyan",
        )
        metrics.add_column(
            "TOTAL",
            justify="right",
            style="white",
        )
        metrics.add_column(
            "CURRENT RUN",
            justify="right",
            style="green",
        )

        metrics.add_row(
            "Saved records",
            f"{self.db_records:,}",
            f"+{self.saved_this_run:,}",
        )

        metrics.add_row(
            "Detail completed",
            f"{self.db_detail_done:,}",
            f"+{self.detail_done_this_run:,}",
        )

        metrics.add_row(
            "Detail unavailable",
            f"{self.db_detail_unavailable:,}",
            f"+{self.detail_404_this_run:,}",
        )

        metrics.add_row(
            "Pending",
            f"{self.db_pending:,}",
            f"Skipped {self.skipped_this_run:,}",
        )

        http_grid = Table.grid(expand=True)
        http_grid.add_column(ratio=1)
        http_grid.add_column(ratio=1)
        http_grid.add_column(ratio=1)
        http_grid.add_column(ratio=1)

        for label, value, style in (
            ("HTTP", self.http_requests, "white"),
            ("SUCCESS", self.http_success, "green"),
            ("404", self.http_404, "yellow"),
            ("ERROR", self.http_errors, "red"),
        ):
            http_grid.add_column()

        http_grid = Table(
            box=box.SIMPLE,
            expand=True,
            show_header=True,
        )

        for name in ("HTTP", "SUCCESS", "404", "ERROR"):
            http_grid.add_column(
                name,
                justify="center",
            )

        http_grid.add_row(
            str(self.http_requests),
            str(self.http_success),
            str(self.http_404),
            str(self.http_errors),
        )

        activity = Table.grid(expand=True)

        activity.add_column(
            width=9,
            no_wrap=True,
        )
        activity.add_column(
            width=6,
            no_wrap=True,
        )
        activity.add_column(ratio=1)

        for timestamp, level, message, style in self.activities:
            activity.add_row(
                Text(timestamp, style="bright_black"),
                Text(level, style=style),
                Text(
                    message,
                    style=style,
                    overflow="ellipsis",
                ),
            )

        if not self.activities:
            activity.add_row(
                "",
                "",
                "Preparing collection...",
            )

        return Panel(
            Group(
                header,
                Text(""),
                target,
                Text(""),
                Panel(
                    package_table,
                    title="PACKAGE-LEVEL PROGRESS",
                    border_style="cyan",
                ),
                metrics,
                http_grid,
                Panel(
                    activity,
                    title="LATEST ACTIVITY",
                    border_style="bright_black",
                ),
            ),
            border_style="cyan",
            title="ANC MOTOR INSURANCE INTELLIGENCE",
        )


# ============================================================
# HTTP
# ============================================================

class HTTPClient:

    def __init__(
        self,
        delay: float,
        debug: bool = False,
        dashboard: Optional[CollectorDashboard] = None,
    ):
        self.delay = max(0.2, float(delay))
        self.debug = debug
        self.dashboard = dashboard

        self.session = requests.Session()

        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "th-TH,th;q=0.9,en;q=0.7",
        })

        adapter = HTTPAdapter(
            max_retries=Retry(
                total=2,
                connect=2,
                read=1,
                status=0,
                backoff_factor=1.0,
            ),
            pool_connections=4,
            pool_maxsize=4,
        )

        self.session.mount("https://", adapter)

        self.requests_count = 0
        self.success_count = 0
        self.not_found_count = 0
        self.error_count = 0

        self.last_request = 0.0

        self.robots = RobotFileParser()

        self.load_robots()

    def update_dashboard(self):
        if self.dashboard:
            self.dashboard.update_http(
                self.requests_count,
                self.success_count,
                self.not_found_count,
                self.error_count,
            )

    def load_robots(self):
        url = BASE_URL + "/robots.txt"

        self.robots.set_url(url)

        try:
            response = self.session.get(
                url,
                timeout=TIMEOUT,
                allow_redirects=False,
            )

            if response.status_code == 200:
                self.robots.parse(
                    response.text.splitlines()
                )

            elif response.status_code in (404, 410):
                self.robots.parse([])

            else:
                raise AccessBlocked(
                    "Cannot verify robots policy: "
                    f"HTTP {response.status_code}"
                )

        except requests.RequestException as exc:
            raise AccessBlocked(
                f"robots.txt unavailable: {exc}"
            ) from exc

    def allowed(self, url: str) -> bool:
        return (
            same_origin(url)
            and self.robots.can_fetch(
                USER_AGENT,
                url,
            )
        )

    def throttle(self):
        elapsed = time.monotonic() - self.last_request

        wait = (
            self.delay
            + random.uniform(0, 0.25)
            - elapsed
        )

        if wait > 0:
            time.sleep(wait)

    @staticmethod
    def is_challenge(soup: BeautifulSoup) -> bool:
        title = clean(
            soup.title.get_text(" ", strip=True)
            if soup.title else ""
        ).lower()

        if any(
            phrase in title
            for phrase in (
                "verify you are human",
                "just a moment",
                "attention required",
                "security check",
            )
        ):
            return True

        return any(
            soup.find(id=identifier) is not None
            for identifier in (
                "cf-challenge-running",
                "challenge-form",
                "cf-turnstile",
            )
        )

    def save_debug(
        self,
        soup: BeautifulSoup,
        label: str,
    ):
        if not self.debug:
            return

        DEBUG_PATH.mkdir(
            parents=True,
            exist_ok=True,
        )

        filename = (
            re.sub(
                r"[^A-Za-z0-9_-]",
                "_",
                label,
            )[:70]
            + "-"
            + str(int(time.time() * 1000))
            + ".html"
        )

        path = DEBUG_PATH / filename

        path.write_text(
            str(soup),
            encoding="utf-8",
        )

    def request(
        self,
        method: str,
        url: str,
        data: Optional[dict] = None,
    ):
        url = absolute_url(BASE_URL, url)
        method = method.upper()

        redirects = 0

        while True:
            if STOP_REQUESTED:
                raise KeyboardInterrupt

            if self.requests_count >= MAX_REQUESTS:
                raise CollectorError(
                    "Maximum HTTP request limit reached"
                )

            if not self.allowed(url):
                raise AccessBlocked(
                    f"URL disallowed: {url}"
                )

            self.throttle()

            self.last_request = time.monotonic()
            self.requests_count += 1

            logger.info(
                "HTTP %s | %s",
                method,
                url,
            )

            if self.dashboard:
                self.dashboard.operation = (
                    f"HTTP {method}: "
                    f"{unquote(urlparse(url).path)[-75:]}"
                )
                self.dashboard.refresh()

            try:
                response = self.session.request(
                    method,
                    url,
                    data=data,
                    timeout=TIMEOUT,
                    allow_redirects=False,
                )

            except requests.RequestException:
                self.error_count += 1
                self.update_dashboard()
                raise

            if response.status_code in (401, 403, 429):
                self.error_count += 1
                self.update_dashboard()

                raise AccessBlocked(
                    f"HTTP {response.status_code}: {url}"
                )

            if response.is_redirect:
                location = response.headers.get("Location")

                if not location:
                    self.error_count += 1
                    self.update_dashboard()
                    raise DiscoveryError(
                        "Redirect without Location"
                    )

                redirects += 1

                if redirects > 5:
                    self.error_count += 1
                    self.update_dashboard()
                    raise DiscoveryError(
                        "Too many redirects"
                    )

                url = absolute_url(
                    response.url,
                    location,
                )

                if response.status_code in (301, 302, 303):
                    method = "GET"
                    data = None

                continue

            if response.status_code == 404:
                self.not_found_count += 1
                self.update_dashboard()

                raise DiscoveryError(
                    f"Detail page unavailable: {response.url}"
                )

            try:
                response.raise_for_status()

            except requests.HTTPError:
                self.error_count += 1
                self.update_dashboard()
                raise

            content_type = response.headers.get(
                "Content-Type",
                "",
            ).lower()

            if not any(
                item in content_type
                for item in (
                    "text/html",
                    "text/plain",
                    "application/xhtml+xml",
                )
            ):
                self.error_count += 1
                self.update_dashboard()

                raise DiscoveryError(
                    f"Unexpected content type: {content_type}"
                )

            content = response.text.lstrip("\ufeff")

            if (
                re.match(r"^\d+\|", content)
                and "|updatePanel|" in content
            ):
                self.success_count += 1
                self.update_dashboard()

                return content, response.url

            soup = BeautifulSoup(
                content,
                "html.parser",
            )

            if self.is_challenge(soup):
                self.error_count += 1
                self.update_dashboard()

                raise AccessBlocked(
                    "Human verification encountered"
                )

            path = unquote(
                urlparse(response.url).path
            ).lower()

            if "/http404.aspx" in path:
                self.not_found_count += 1
                self.update_dashboard()

                raise DiscoveryError(
                    f"Detail page unavailable: {response.url}"
                )

            title = clean(
                soup.title.get_text(" ", strip=True)
                if soup.title else ""
            ).lower()

            if title in (
                "404",
                "404 not found",
                "page not found",
                "error 404",
            ):
                self.not_found_count += 1
                self.update_dashboard()

                raise DiscoveryError(
                    f"Website error page: {response.url}"
                )

            self.success_count += 1
            self.update_dashboard()

            logger.info(
                "HTTP OK | status=%s | bytes=%s",
                response.status_code,
                len(response.content),
            )

            return soup, response.url

    def get(self, url: str):
        return self.request(
            "GET",
            url,
        )

    def post(self, url: str, data: dict):
        return self.request(
            "POST",
            url,
            data=data,
        )

    def close(self):
        self.session.close()


# ============================================================
# WEBFORMS
# ============================================================

class WebForms:

    def __init__(self, http: HTTPClient):
        self.http = http

    @staticmethod
    def find_form(soup: BeautifulSoup):
        form = (
            soup.find("form", id="form1")
            or soup.find("form")
        )

        if form is None:
            raise WebFormsError(
                "ASP.NET form missing"
            )

        return form

    @classmethod
    def payload(cls, soup: BeautifulSoup) -> dict:
        form = cls.find_form(soup)

        result = {}

        for element in form.find_all("input"):
            name = element.get("name")

            if not name:
                continue

            input_type = element.get(
                "type",
                "text",
            ).lower()

            if input_type in (
                "submit",
                "button",
                "reset",
                "image",
                "file",
            ):
                continue

            if input_type in ("checkbox", "radio"):
                if not element.has_attr("checked"):
                    continue

            result[name] = element.get(
                "value",
                "",
            )

        for element in form.find_all("select"):
            name = element.get("name")

            if not name:
                continue

            selected = (
                element.find("option", selected=True)
                or element.find("option")
            )

            if selected:
                result[name] = selected.get(
                    "value",
                    "",
                )

        for element in form.find_all("textarea"):
            name = element.get("name")

            if name:
                result[name] = element.get_text()

        return result

    @staticmethod
    def options(
        soup: BeautifulSoup,
        element_id: str,
    ):
        element = soup.find(id=element_id)

        if element is None:
            return []

        return [
            {
                "value": clean(option.get("value", "")),
                "text": clean(
                    option.get_text(
                        " ",
                        strip=True,
                    )
                ),
                "selected": option.has_attr("selected"),
            }
            for option in element.find_all("option")
        ]

    @classmethod
    def selected(
        cls,
        soup: BeautifulSoup,
        element_id: str,
    ):
        options = cls.options(
            soup,
            element_id,
        )

        for option in options:
            if option["selected"]:
                return option

        return options[0] if options else None

    @staticmethod
    def merge_delta(
        previous: BeautifulSoup,
        delta: str,
    ):
        merged = BeautifulSoup(
            str(previous),
            "html.parser",
        )

        position = 0
        updates = 0

        while position < len(delta):
            boundary = delta.find(
                "|",
                position,
            )

            if boundary < 0:
                break

            length_text = delta[position:boundary]

            if not length_text.isdigit():
                raise WebFormsError(
                    "Invalid AJAX frame length"
                )

            length = int(length_text)
            position = boundary + 1

            boundary = delta.find(
                "|",
                position,
            )

            if boundary < 0:
                raise WebFormsError(
                    "Invalid AJAX frame"
                )

            frame_type = delta[position:boundary]
            position = boundary + 1

            boundary = delta.find(
                "|",
                position,
            )

            if boundary < 0:
                raise WebFormsError(
                    "Invalid AJAX frame ID"
                )

            element_id = delta[position:boundary]
            position = boundary + 1

            content = delta[
                position:position + length
            ]

            if len(content) != length:
                raise WebFormsError(
                    "Incomplete AJAX payload"
                )

            position += length

            if (
                position >= len(delta)
                or delta[position] != "|"
            ):
                raise WebFormsError(
                    "Invalid AJAX delimiter"
                )

            position += 1

            if frame_type == "updatePanel":
                target = merged.find(
                    id=element_id
                )

                if target is None:
                    raise WebFormsError(
                        f"AJAX panel not found: {element_id}"
                    )

                fragment = BeautifulSoup(
                    content,
                    "html.parser",
                )

                target.clear()

                for child in list(fragment.contents):
                    target.append(
                        child.extract()
                    )

                updates += 1

            elif frame_type == "hiddenField":
                element = merged.find(
                    "input",
                    attrs={"name": element_id},
                )

                if element is None:
                    element = merged.new_tag(
                        "input"
                    )
                    element["type"] = "hidden"
                    element["name"] = element_id
                    element["id"] = element_id

                    WebForms.find_form(
                        merged
                    ).append(element)

                element["value"] = content

            elif frame_type in (
                "error",
                "pageRedirect",
            ):
                raise WebFormsError(
                    f"ASP.NET AJAX {frame_type}: {content}"
                )

        if updates == 0:
            raise WebFormsError(
                "No updatePanel in AJAX response"
            )

        return merged

    def postback(
        self,
        soup: BeautifulSoup,
        page_url: str,
        element_id: str,
        selected_value: str,
    ):
        element = soup.find(
            id=element_id
        )

        if element is None:
            raise WebFormsError(
                f"Control not found: {element_id}"
            )

        name = element.get("name")

        if not name:
            raise WebFormsError(
                f"Control name missing: {element_id}"
            )

        form = self.find_form(soup)

        url = absolute_url(
            page_url,
            form.get("action") or page_url,
        )

        payload = self.payload(soup)

        payload["__EVENTTARGET"] = name
        payload["__EVENTARGUMENT"] = ""
        payload[name] = selected_value

        result, final_url = self.http.post(
            url,
            payload,
        )

        if isinstance(result, str):
            result = self.merge_delta(
                soup,
                result,
            )

        return result, final_url


# ============================================================
# VEHICLE CATALOG
# ============================================================

class VehicleCatalog:

    LINK_PATTERN = re.compile(
        r"rptCarBrand_rptCarModel",
        re.I,
    )

    URL_PATTERN = re.compile(
        r"^(.+)-ปี(\d{4})-id(\d+)-",
        re.I,
    )

    def __init__(self, http: HTTPClient):
        self.http = http

        self.entries = []
        self.loaded = False

    @staticmethod
    def split_slug(slug: str):
        brands = sorted(
            {
                vehicle.brand
                for vehicle in VEHICLES
            },
            key=len,
            reverse=True,
        )

        upper = slug.upper()

        for brand in brands:
            if upper.startswith(brand + "-"):
                return (
                    brand,
                    upper[len(brand) + 1:],
                )

        return None

    @staticmethod
    def matches_model(
        candidate: str,
        expected: str,
    ) -> bool:
        candidate_tokens = tokens(candidate)
        expected_tokens = tokens(expected)

        return (
            bool(expected_tokens)
            and candidate_tokens[:len(expected_tokens)]
            == expected_tokens
        )

    def load(self):
        if self.loaded:
            return self.entries

        soup, page_url = self.http.get(
            CLASS_ONE_URL
        )

        if isinstance(soup, str):
            raise DiscoveryError(
                "Catalog returned AJAX data"
            )

        entries = []
        seen = set()

        for element in soup.find_all(
            "a",
            href=True,
            id=True,
        ):
            if not self.LINK_PATTERN.search(
                element.get("id", "")
            ):
                continue

            url = absolute_url(
                page_url,
                element["href"],
            )

            if not same_origin(url):
                continue

            filename = unquote(
                urlparse(url).path
            ).rsplit("/", 1)[-1]

            match = self.URL_PATTERN.match(
                filename
            )

            if not match:
                continue

            parsed = self.split_slug(
                match.group(1)
            )

            if parsed is None:
                continue

            brand, catalog_model = parsed

            year = int(match.group(2))
            car_id = int(match.group(3))

            matching_models = sorted(
                {
                    vehicle.model
                    for vehicle in VEHICLES
                    if (
                        vehicle.brand == brand
                        and self.matches_model(
                            catalog_model,
                            vehicle.model,
                        )
                    )
                },
                key=lambda item: len(tokens(item)),
                reverse=True,
            )

            if not matching_models:
                continue

            model = matching_models[0]
            identity = (brand, model, car_id)

            if identity in seen:
                continue

            seen.add(identity)

            entries.append(
                CatalogEntry(
                    brand=brand,
                    model=model,
                    submodel=clean(
                        element.get_text(
                            " ",
                            strip=True,
                        )
                    ) or catalog_model,
                    car_id=car_id,
                    reference_year=year,
                    reference_url=url,
                )
            )

        if not entries:
            raise DiscoveryError(
                "No catalog entries found"
            )

        self.entries = entries
        self.loaded = True

        CATALOG_PATH.write_text(
            json.dumps(
                [
                    {
                        "brand": e.brand,
                        "model": e.model,
                        "submodel": e.submodel,
                        "car_id": e.car_id,
                        "reference_year": e.reference_year,
                        "reference_url": e.reference_url,
                    }
                    for e in entries
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        logger.info(
            "CATALOG LOADED | entries=%s",
            len(entries),
        )

        return entries

    def candidates(self, vehicle: Vehicle):
        self.load()

        return [
            entry
            for entry in self.entries
            if (
                entry.brand == vehicle.brand
                and entry.model == vehicle.model
            )
        ]

    @staticmethod
    def year_url(
        entry: CatalogEntry,
        year: int,
    ) -> str:
        parsed = urlparse(
            entry.reference_url
        )

        path = unquote(
            parsed.path
        )

        old = (
            f"-ปี{entry.reference_year}"
            f"-id{entry.car_id}-"
        )

        new = (
            f"-ปี{year}"
            f"-id{entry.car_id}-"
        )

        if old not in path:
            raise DiscoveryError(
                "Catalog URL identity mismatch"
            )

        return absolute_url(
            BASE_URL,
            parsed._replace(
                path=path.replace(
                    old,
                    new,
                    1,
                )
            ).geturl(),
        )


# ============================================================
# VEHICLE DISCOVERY
# ============================================================

class VehicleDiscovery:

    PROVINCE_CONTROL = (
        "ContentPlaceHolder_ddlCarPlate"
    )

    def __init__(
        self,
        http: HTTPClient,
        catalog: VehicleCatalog,
    ):
        self.http = http
        self.catalog = catalog

    def resolve(
        self,
        vehicle: Vehicle,
        entry: CatalogEntry,
    ):
        url = self.catalog.year_url(
            entry,
            vehicle.year,
        )

        soup, final_url = self.http.get(
            url
        )

        if isinstance(soup, str):
            raise DiscoveryError(
                "Unexpected AJAX vehicle response"
            )

        path = unquote(
            urlparse(final_url).path
        )

        expected_id = re.search(
            rf"-id{entry.car_id}(?:-|/|$)",
            path,
            flags=re.I,
        )

        if (
            not expected_id
            or f"-ปี{vehicle.year}-" not in path
        ):
            raise ValidationError(
                f"Vehicle ID/year mismatch: {final_url}"
            )

        if soup.find(
            id=self.PROVINCE_CONTROL
        ) is None:
            raise DiscoveryError(
                "Vehicle listing control missing"
            )

        title = clean(
            soup.title.get_text(
                " ",
                strip=True,
            )
            if soup.title
            else ""
        ).upper()

        headings = " ".join(
            clean(
                x.get_text(
                    " ",
                    strip=True,
                )
            ).upper()
            for x in soup.find_all(
                ("h1", "h2", "h3")
            )[:15]
        )

        identity_text = title + " " + headings

        if (
            vehicle.brand not in identity_text
            or not (
                str(vehicle.year) in identity_text
                or str(vehicle.year + 543) in identity_text
            )
        ):
            raise ValidationError(
                "Vehicle/year not independently verified"
            )

        return soup, final_url


# ============================================================
# LISTING FILTERS
# ============================================================

class ListingFilter:

    PROVINCE_CONTROL = (
        "ContentPlaceHolder_ddlCarPlate"
    )

    REPAIR_IDS = {
        "ซ่อมอู่": (
            "ContentPlaceHolder_Filter_Fix2"
        ),
        "ซ่อมห้าง": (
            "ContentPlaceHolder_Filter_Fix1"
        ),
    }

    def __init__(self, http: HTTPClient):
        self.http = http
        self.forms = WebForms(http)

    def choose_repair(
        self,
        soup: BeautifulSoup,
        url: str,
        repair_type: str,
    ):
        element_id = self.REPAIR_IDS[
            repair_type
        ]

        element = soup.find(
            id=element_id
        )

        if element is None:
            raise DiscoveryError(
                f"Repair filter missing: {repair_type}"
            )

        href = element.get(
            "href",
            "",
        )

        if not href:
            raise DiscoveryError(
                "Repair filter URL missing"
            )

        if href.lower().startswith(
            "javascript:"
        ):
            raise DiscoveryError(
                "Unsupported JavaScript repair link"
            )

        target = absolute_url(
            url,
            href,
        )

        result, final_url = self.http.get(
            target
        )

        if isinstance(result, str):
            raise DiscoveryError(
                "Unexpected AJAX repair response"
            )

        path = unquote(
            urlparse(final_url).path
        )

        if repair_type not in path:
            raise ValidationError(
                "Repair marker missing from URL"
            )

        return result, final_url

    def choose_province(
        self,
        soup: BeautifulSoup,
        url: str,
        province: str,
    ):
        options = self.forms.options(
            soup,
            self.PROVINCE_CONTROL,
        )

        matching = [
            option
            for option in options
            if (
                key_text(option["value"])
                == key_text(province)
                or key_text(option["text"])
                == key_text(province)
            )
        ]

        if len(matching) != 1:
            raise ValidationError(
                f"Province ambiguous: {province}"
            )

        value = matching[0]["value"]

        selected = self.forms.selected(
            soup,
            self.PROVINCE_CONTROL,
        )

        if selected and selected["value"] == value:
            result = soup
            final_url = url

        else:
            result, final_url = self.forms.postback(
                soup,
                url,
                self.PROVINCE_CONTROL,
                value,
            )

        selected = self.forms.selected(
            result,
            self.PROVINCE_CONTROL,
        )

        if (
            selected is None
            or key_text(selected["value"])
            != key_text(value)
        ):
            raise ValidationError(
                "Province selection not retained"
            )

        final_path = unquote(
            urlparse(final_url).path
        )

        if (
            f"-{province}-" not in final_path
            and not final_path.endswith(
                "-" + province
            )
        ):
            raise ValidationError(
                "Province missing from listing URL"
            )

        return result, final_url

    def filtered_listing(
        self,
        base_soup: BeautifulSoup,
        base_url: str,
        province: str,
        repair_type: str,
    ):
        repair_soup, repair_url = self.choose_repair(
            base_soup,
            base_url,
            repair_type,
        )

        filtered_soup, filtered_url = (
            self.choose_province(
                repair_soup,
                repair_url,
                province,
            )
        )

        if repair_type not in unquote(
            urlparse(filtered_url).path
        ):
            raise ValidationError(
                "Repair marker missing after province selection"
            )

        return filtered_soup, filtered_url


# ============================================================
# PACKAGE PARSER
# ============================================================

class PackageParser:

    FIELD_RE = re.compile(
        r"^ContentPlaceHolder_"
        r"PackageGroup_rptPackageList_"
        r"(\d+)_(.+)_(\d+)$"
    )

    FIELD_MAP = {
        "Hid_qsub_vmi_IDPACK": "package_id",
        "Hid_qsub_vmi_PACKNAME": "package_name",
        "Hid_qsub_vmi_INS": "insurer_code",
        "Hid_qsub_vmi_POTYPE": "insurance_type",
        "Hid_qsub_vmi_POTYPEID": "insurance_type_id",
        "Hid_qsub_vmi_NETPREMIUM": "net_premium",
        "Hid_qsub_vmi_PREMIUM": "original_premium",
        "Hid_qsub_vmi_OD": "sum_insured",
        "Hid_qsub_vmi_FIRE": "fire_theft",
        "Hid_qsub_vmi_FLOOD": "flood",
        "Hid_offer_price": "premium",
        "PACKNAMECUS": "display_package_name",
        "FIX": "repair_label",
        "OD": "display_sum_insured",
        "OFFER_PRICE": "display_premium",
        "HyperLink1": "detail_url",
    }

    COVERAGE_IDS = {
        "coverage_bodily_injury_person": (
            "ContentPlaceHolder_mc1_BI"
        ),
        "coverage_bodily_injury_accident": (
            "ContentPlaceHolder_mc1_BT"
        ),
        "coverage_third_party_property": (
            "ContentPlaceHolder_mc1_TP"
        ),
        "coverage_own_damage": (
            "ContentPlaceHolder_mc1_OD"
        ),
        "coverage_fire_theft": (
            "ContentPlaceHolder_mc1_FIRE"
        ),
        "deductible": (
            "ContentPlaceHolder_mc1_ODDD"
        ),
        "personal_accident": (
            "ContentPlaceHolder_mc1_PA"
        ),
        "medical_expense": (
            "ContentPlaceHolder_mc1_ME"
        ),
        "bail_bond": (
            "ContentPlaceHolder_mc1_BB"
        ),
    }

    def __init__(self, http: HTTPClient):
        self.http = http

    @staticmethod
    def repair_label(value: str) -> Optional[str]:
        value = clean(value)

        if (
            "ซ่อมห้าง" in value
            or "ซ่อมศูนย์" in value
            or value == "ห้าง"
        ):
            return "ซ่อมห้าง"

        if (
            "ซ่อมอู่" in value
            or value == "อู่"
        ):
            return "ซ่อมอู่"

        return None

    @staticmethod
    def class_one(value: str) -> bool:
        value = clean(value).upper()

        return (
            value in ("1", "110", "ป1", "TYPE 1")
            or "ชั้น1" in value
            or "ชั้น 1" in value
            or "ประเภท 1" in value
        )

    def parse(
        self,
        soup: BeautifulSoup,
        listing_url: str,
        region: str,
        province: str,
        vehicle: Vehicle,
        entry: CatalogEntry,
        repair_type: str,
    ) -> tuple[list[Package], list[str]]:
        groups = defaultdict(dict)

        found_fields = 0

        for element in soup.find_all(id=True):
            match = self.FIELD_RE.match(
                element["id"]
            )

            if not match:
                continue

            found_fields += 1

            group = int(
                match.group(1)
            )

            field_name = match.group(2)
            package_index = int(match.group(3))

            mapped = self.FIELD_MAP.get(
                field_name
            )

            if not mapped:
                continue

            value = get_value(element)

            if mapped == "detail_url":
                value = absolute_url(
                    listing_url,
                    value,
                )

            groups[
                (group, package_index)
            ][mapped] = value

        if found_fields == 0:
            raise DiscoveryError(
                "No package fields found"
            )

        branch_id = branch_key(
            province,
            vehicle,
            entry.car_id,
            repair_type,
        )

        result = []
        issues = []
        seen = set()

        for position, raw in sorted(
            groups.items()
        ):
            detail_url = raw.get(
                "detail_url",
                "",
            )

            if (
                not detail_url
                or not same_origin(detail_url)
            ):
                issues.append(
                    f"PACKAGE_URL_INVALID:{position}"
                )
                continue

            class_type = raw.get(
                "insurance_type",
                "",
            )

            class_type_id = raw.get(
                "insurance_type_id",
                "",
            )

            if class_type or class_type_id:
                if not (
                    self.class_one(class_type)
                    or self.class_one(class_type_id)
                ):
                    issues.append(
                        f"INSURANCE_TYPE_UNVERIFIED:{position}"
                    )
                    continue
            else:
                issues.append(
                    f"INSURANCE_TYPE_MISSING:{position}"
                )
                continue

            actual_repair = self.repair_label(
                raw.get(
                    "repair_label",
                    "",
                )
            )

            if actual_repair != repair_type:
                issues.append(
                    f"REPAIR_MISMATCH:{position}"
                )
                continue

            package_id = clean(
                raw.get("package_id", "")
            )

            package_name = clean(
                raw.get("package_name")
                or raw.get("display_package_name")
            )

            insurer_code = clean(
                raw.get("insurer_code", "")
            )

            premium = money(
                raw.get("premium")
                or raw.get("display_premium")
            )

            sum_insured = money(
                raw.get("sum_insured")
                or raw.get("display_sum_insured")
            )

            if premium is None or premium <= 0:
                issues.append(
                    f"PREMIUM_UNVERIFIED:{position}"
                )
                continue

            if sum_insured is None or sum_insured <= 0:
                issues.append(
                    f"SUM_INSURED_UNVERIFIED:{position}"
                )
                continue

            if not package_id and not package_name:
                issues.append(
                    f"PACKAGE_IDENTITY_UNVERIFIED:{position}"
                )
                continue

            identity = (
                insurer_code,
                package_id,
                package_name,
                str(sum_insured),
                repair_type,
                detail_url,
            )

            if identity in seen:
                continue

            seen.add(identity)

            record_id = hash_key(
                "ANC-V4-RECORD",
                branch_id,
                insurer_code,
                package_id,
                package_name,
                str(sum_insured),
                detail_url,
            )

            result.append(
                Package(
                    branch_key=branch_id,
                    record_key=record_id,
                    source_url=detail_url,
                    listing_url=listing_url,
                    province=province,
                    region=region,
                    brand=vehicle.brand,
                    model=vehicle.model,
                    submodel=entry.submodel,
                    car_id=entry.car_id,
                    vehicle_year=vehicle.year,
                    repair_type=repair_type,
                    package_id=package_id,
                    package_name=package_name,
                    insurer_code=insurer_code,
                    premium=premium,
                    net_premium=money(
                        raw.get("net_premium")
                    ),
                    original_premium=money(
                        raw.get("original_premium")
                    ),
                    sum_insured=sum_insured,
                    coverage_fire_theft=money(
                        raw.get("fire_theft")
                    ),
                    coverage_flood=money(
                        raw.get("flood")
                    ),
                    raw_listing=dict(raw),
                    validation_status="LISTING_VERIFIED",
                )
            )

        logger.info(
            "LISTING PARSED | province=%s | car_id=%s "
            "| repair=%s | groups=%s | valid=%s | issues=%s",
            province,
            entry.car_id,
            repair_type,
            len(groups),
            len(result),
            len(issues),
        )

        return result, issues

    def enrich(self, package: Package) -> Package:
        soup, final_url = self.http.get(
            package.source_url
        )

        if isinstance(soup, str):
            raise DiscoveryError(
                "Unexpected AJAX detail response"
            )

        detail = {
            "insurer": get_text(
                soup,
                "ContentPlaceHolder_pack_name",
            ),
            "package_name": get_text(
                soup,
                "ContentPlaceHolder_TitleName",
            ),
            "premium": number_as_str(
                money(
                    get_text(
                        soup,
                        "ContentPlaceHolder_pack_offerprice",
                    )
                )
            ),
            "original_premium": number_as_str(
                money(
                    get_text(
                        soup,
                        "ContentPlaceHolder_pack_pre",
                    )
                )
            ),
        }

        for name, element_id in self.COVERAGE_IDS.items():
            detail[name] = number_as_str(
                money(
                    get_text(
                        soup,
                        element_id,
                    )
                )
            )

        detail_repair = self.repair_label(
            " ".join(
                [
                    detail.get(
                        "package_name",
                        "",
                    ),
                    get_text(
                        soup,
                        "ContentPlaceHolder_pack_dd",
                    ),
                    get_text(
                        soup,
                        "ContentPlaceHolder_pack_description",
                    ),
                ]
            )
        )

        if (
            detail_repair
            and detail_repair != package.repair_type
        ):
            raise ValidationError(
                "Detail repair contradicts listing"
            )

        detail_price = money(
            detail["premium"]
        )

        if (
            detail_price is not None
            and package.premium is not None
            and detail_price != package.premium
        ):
            detail["premium_mismatch"] = {
                "listing": str(package.premium),
                "detail": str(detail_price),
            }

        package.insurer = (
            detail.get("insurer")
            or package.insurer
        )

        if not package.package_name:
            package.package_name = detail.get(
                "package_name",
                "",
            )

        for name in self.COVERAGE_IDS:
            value = money(
                detail.get(name)
            )

            if value is not None:
                setattr(
                    package,
                    name,
                    value,
                )

        package.raw_detail = detail
        package.source_url = final_url
        package.detail_status = "DONE"

        if detail.get("premium_mismatch"):
            package.validation_status = "REVIEW_REQUIRED"
            package.validation_notes = (
                "DETAIL_PREMIUM_DIFFERS_FROM_LISTING"
            )
        else:
            package.validation_status = "DETAIL_VERIFIED"

        return package


# ============================================================
# DATABASE
# ============================================================

class Database:

    RECORD_COLUMNS = (
        "record_key",
        "branch_key",
        "target_key",
        "source",
        "source_url",
        "listing_url",
        "region",
        "province",
        "brand",
        "model",
        "submodel",
        "car_id",
        "vehicle_year",
        "insurance_type",
        "repair_type",
        "insurer",
        "insurer_code",
        "package_id",
        "package_name",
        "premium",
        "net_premium",
        "original_premium",
        "actual_sum_insured",
        "deductible",
        "coverage_bodily_injury_person",
        "coverage_bodily_injury_accident",
        "coverage_third_party_property",
        "coverage_own_damage",
        "coverage_fire_theft",
        "coverage_flood",
        "personal_accident",
        "medical_expense",
        "bail_bond",
        "listing_status",
        "detail_status",
        "validation_status",
        "validation_notes",
        "raw_listing",
        "raw_detail",
    )

    def __init__(self):
        self.connection = None

    @staticmethod
    def setting(*names, default=None):
        for name in names:
            value = os.getenv(name)

            if value not in (None, ""):
                return value

        return default

    def connect(self):
        user = self.setting(
            "DB_USER",
            "MYSQL_USER",
        )

        if not user:
            raise CollectorError(
                "DB_USER is required"
            )

        self.connection = pymysql.connect(
            host=self.setting(
                "DB_HOST",
                "MYSQL_HOST",
                default="127.0.0.1",
            ),
            port=int(
                self.setting(
                    "DB_PORT",
                    "MYSQL_PORT",
                    default="3307",
                )
            ),
            user=user,
            password=self.setting(
                "DB_PASSWORD",
                "MYSQL_PASSWORD",
                default="",
            ),
            database=self.setting(
                "DB_NAME",
                "MYSQL_DATABASE",
                default="dbd",
            ),
            charset="utf8mb4",
            autocommit=False,
            connect_timeout=15,
            read_timeout=60,
            write_timeout=60,
            cursorclass=pymysql.cursors.DictCursor,
        )

        return self

    def execute(
        self,
        sql,
        params=None,
        fetch=False,
    ):
        # Do not use deprecated ping(reconnect=True).
        # Reconnect explicitly only when outside a transaction.
        if not self.connection.open:
            self.connect()

        with self.connection.cursor() as cursor:
            cursor.execute(
                sql,
                params,
            )

            if fetch:
                return cursor.fetchall()

            return cursor.rowcount

    def initialize(self):
        self.execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{RECORD_TABLE}` (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,

                record_key CHAR(64) NOT NULL,
                branch_key CHAR(64) NOT NULL,
                target_key CHAR(64) NOT NULL,

                source VARCHAR(100) NOT NULL,
                source_url TEXT,
                listing_url TEXT,

                region VARCHAR(100) NOT NULL,
                province VARCHAR(100) NOT NULL,

                brand VARCHAR(80) NOT NULL,
                model VARCHAR(120) NOT NULL,
                submodel VARCHAR(255),

                car_id INT NOT NULL,
                vehicle_year SMALLINT NOT NULL,

                insurance_type VARCHAR(30) NOT NULL,
                repair_type VARCHAR(30) NOT NULL,

                insurer VARCHAR(255),
                insurer_code VARCHAR(80),
                package_id VARCHAR(100),
                package_name VARCHAR(500),

                premium DECIMAL(15,2),
                net_premium DECIMAL(15,2),
                original_premium DECIMAL(15,2),
                actual_sum_insured DECIMAL(15,2),
                deductible DECIMAL(15,2),

                coverage_bodily_injury_person DECIMAL(15,2),
                coverage_bodily_injury_accident DECIMAL(15,2),
                coverage_third_party_property DECIMAL(15,2),
                coverage_own_damage DECIMAL(15,2),
                coverage_fire_theft DECIMAL(15,2),
                coverage_flood DECIMAL(15,2),

                personal_accident DECIMAL(15,2),
                medical_expense DECIMAL(15,2),
                bail_bond DECIMAL(15,2),

                listing_status VARCHAR(40) NOT NULL,
                detail_status VARCHAR(40) NOT NULL,
                validation_status VARCHAR(40) NOT NULL,
                validation_notes TEXT,

                raw_listing LONGTEXT,
                raw_detail LONGTEXT,

                collected_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,

                PRIMARY KEY (id),
                UNIQUE KEY uq_record_key (record_key),
                KEY idx_target (target_key),
                KEY idx_branch (branch_key),
                KEY idx_vehicle (brand, model, vehicle_year),
                KEY idx_province (province),
                KEY idx_status (detail_status)
            )
            ENGINE=InnoDB
            DEFAULT CHARSET=utf8mb4
            COLLATE=utf8mb4_unicode_ci
            """
        )

        self.execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{TARGET_TABLE}` (
                target_key CHAR(64) NOT NULL PRIMARY KEY,

                region VARCHAR(100) NOT NULL,
                province VARCHAR(100) NOT NULL,

                brand VARCHAR(80) NOT NULL,
                model VARCHAR(120) NOT NULL,
                vehicle_year SMALLINT NOT NULL,

                status VARCHAR(40) NOT NULL,
                total_submodels INT NOT NULL DEFAULT 0,
                complete_branches INT NOT NULL DEFAULT 0,
                total_branches INT NOT NULL DEFAULT 0,

                error_message TEXT,
                updated_at DATETIME NOT NULL,

                KEY idx_status (status)
            )
            ENGINE=InnoDB
            DEFAULT CHARSET=utf8mb4
            COLLATE=utf8mb4_unicode_ci
            """
        )

        self.execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{BRANCH_TABLE}` (
                branch_key CHAR(64) NOT NULL PRIMARY KEY,
                target_key CHAR(64) NOT NULL,

                car_id INT NOT NULL,
                repair_type VARCHAR(30) NOT NULL,

                status VARCHAR(40) NOT NULL,
                listing_url TEXT,

                package_count INT NOT NULL DEFAULT 0,
                saved_count INT NOT NULL DEFAULT 0,
                detail_done INT NOT NULL DEFAULT 0,
                detail_unavailable INT NOT NULL DEFAULT 0,

                listing_issues TEXT,
                error_message TEXT,

                updated_at DATETIME NOT NULL,

                KEY idx_target (target_key),
                KEY idx_status (status)
            )
            ENGINE=InnoDB
            DEFAULT CHARSET=utf8mb4
            COLLATE=utf8mb4_unicode_ci
            """
        )

        self.execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{PACKAGE_TABLE}` (
                record_key CHAR(64) NOT NULL PRIMARY KEY,
                branch_key CHAR(64) NOT NULL,

                status VARCHAR(40) NOT NULL,
                attempts INT NOT NULL DEFAULT 0,
                last_error TEXT,

                updated_at DATETIME NOT NULL,

                KEY idx_branch (branch_key),
                KEY idx_status (status)
            )
            ENGINE=InnoDB
            DEFAULT CHARSET=utf8mb4
            COLLATE=utf8mb4_unicode_ci
            """
        )

        self.connection.commit()

    @staticmethod
    def record_values(
        package: Package,
    ) -> dict:
        return {
            "record_key": package.record_key,
            "branch_key": package.branch_key,
            "target_key": hash_key(
                "ANC-V4-TARGET",
                package.province,
                package.brand,
                package.model,
                package.vehicle_year,
            ),
            "source": "ANC BROKER",
            "source_url": package.source_url,
            "listing_url": package.listing_url,
            "region": package.region,
            "province": package.province,
            "brand": package.brand,
            "model": package.model,
            "submodel": package.submodel,
            "car_id": package.car_id,
            "vehicle_year": package.vehicle_year,
            "insurance_type": "ประเภท 1",
            "repair_type": package.repair_type,
            "insurer": package.insurer,
            "insurer_code": package.insurer_code,
            "package_id": package.package_id,
            "package_name": package.package_name,
            "premium": package.premium,
            "net_premium": package.net_premium,
            "original_premium": package.original_premium,
            "actual_sum_insured": package.sum_insured,
            "deductible": package.deductible,
            "coverage_bodily_injury_person": (
                package.coverage_bodily_injury_person
            ),
            "coverage_bodily_injury_accident": (
                package.coverage_bodily_injury_accident
            ),
            "coverage_third_party_property": (
                package.coverage_third_party_property
            ),
            "coverage_own_damage": (
                package.coverage_own_damage
            ),
            "coverage_fire_theft": (
                package.coverage_fire_theft
            ),
            "coverage_flood": package.coverage_flood,
            "personal_accident": package.personal_accident,
            "medical_expense": package.medical_expense,
            "bail_bond": package.bail_bond,
            "listing_status": package.listing_status,
            "detail_status": package.detail_status,
            "validation_status": package.validation_status,
            "validation_notes": package.validation_notes,
            "raw_listing": safe_json(
                package.raw_listing
            ),
            "raw_detail": safe_json(
                package.raw_detail
            ),
        }

    def get_package_status(
        self,
        record_key: str,
    ) -> Optional[str]:
        rows = self.execute(
            f"""
            SELECT status
            FROM `{PACKAGE_TABLE}`
            WHERE record_key=%s
            """,
            (record_key,),
            fetch=True,
        )

        return (
            rows[0]["status"]
            if rows
            else None
        )

    def get_branch_status(
        self,
        branch_id: str,
    ):
        rows = self.execute(
            f"""
            SELECT *
            FROM `{BRANCH_TABLE}`
            WHERE branch_key=%s
            """,
            (branch_id,),
            fetch=True,
        )

        return rows[0] if rows else None

    def get_target_status(
        self,
        key: str,
    ):
        rows = self.execute(
            f"""
            SELECT *
            FROM `{TARGET_TABLE}`
            WHERE target_key=%s
            """,
            (key,),
            fetch=True,
        )

        return rows[0] if rows else None

    def save_package(
        self,
        package: Package,
        checkpoint_status: str,
        error_message: str = "",
    ) -> str:
        values = self.record_values(
            package
        )

        columns = self.RECORD_COLUMNS

        names = ", ".join(
            f"`{name}`"
            for name in columns
        )

        markers = ", ".join(
            ["%s"] * len(columns)
        )

        updates = ", ".join(
            f"`{name}`=VALUES(`{name}`)"
            for name in columns
            if name not in (
                "record_key",
                "branch_key",
                "target_key",
            )
        )

        sql = f"""
            INSERT INTO `{RECORD_TABLE}` (
                {names},
                collected_at,
                updated_at
            )
            VALUES (
                {markers},
                NOW(),
                NOW()
            )
            ON DUPLICATE KEY UPDATE
                {updates},
                updated_at=NOW()
        """

        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT 1
                    FROM `{RECORD_TABLE}`
                    WHERE record_key=%s
                    """,
                    (package.record_key,),
                )

                existed = cursor.fetchone() is not None

                cursor.execute(
                    sql,
                    tuple(
                        values[name]
                        for name in columns
                    ),
                )

                cursor.execute(
                    f"""
                    INSERT INTO `{PACKAGE_TABLE}` (
                        record_key,
                        branch_key,
                        status,
                        attempts,
                        last_error,
                        updated_at
                    )
                    VALUES (
                        %s,%s,%s,1,%s,NOW()
                    )
                    ON DUPLICATE KEY UPDATE
                        status=VALUES(status),
                        attempts=attempts+1,
                        last_error=VALUES(last_error),
                        updated_at=NOW()
                    """,
                    (
                        package.record_key,
                        package.branch_key,
                        checkpoint_status,
                        error_message[:2000],
                    ),
                )

            self.connection.commit()

            return (
                "UPDATED"
                if existed
                else "INSERTED"
            )

        except Exception:
            self.connection.rollback()
            raise

    def set_branch(
        self,
        branch_id: str,
        target_id: str,
        car_id: int,
        repair: str,
        status: str,
        listing_url: str = "",
        issues: Optional[list[str]] = None,
        error: str = "",
    ):
        issues = issues or []

        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        COUNT(*) AS total,
                        SUM(status='DONE') AS done_count,
                        SUM(
                            status='DETAIL_UNAVAILABLE'
                        ) AS unavailable
                    FROM `{PACKAGE_TABLE}`
                    WHERE branch_key=%s
                    """,
                    (branch_id,),
                )

                counts = cursor.fetchone()

                cursor.execute(
                    f"""
                    INSERT INTO `{BRANCH_TABLE}` (
                        branch_key,
                        target_key,
                        car_id,
                        repair_type,
                        status,
                        listing_url,
                        package_count,
                        saved_count,
                        detail_done,
                        detail_unavailable,
                        listing_issues,
                        error_message,
                        updated_at
                    )
                    VALUES (
                        %s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,NOW()
                    )
                    ON DUPLICATE KEY UPDATE
                        status=VALUES(status),
                        listing_url=VALUES(listing_url),
                        package_count=VALUES(package_count),
                        saved_count=VALUES(saved_count),
                        detail_done=VALUES(detail_done),
                        detail_unavailable=VALUES(detail_unavailable),
                        listing_issues=VALUES(listing_issues),
                        error_message=VALUES(error_message),
                        updated_at=NOW()
                    """,
                    (
                        branch_id,
                        target_id,
                        car_id,
                        repair,
                        status,
                        listing_url,
                        counts["total"] or 0,
                        counts["total"] or 0,
                        counts["done_count"] or 0,
                        counts["unavailable"] or 0,
                        safe_json(issues),
                        error[:2000],
                    ),
                )

            self.connection.commit()

        except Exception:
            self.connection.rollback()
            raise

    def set_target(
        self,
        region: str,
        province: str,
        vehicle: Vehicle,
        status: str,
        total_submodels: int,
        error_message: str = "",
    ):
        key = target_key(
            province,
            vehicle,
        )

        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        COUNT(*) AS total,
                        SUM(status='DONE') AS done_count
                    FROM `{BRANCH_TABLE}`
                    WHERE target_key=%s
                    """,
                    (key,),
                )

                counts = cursor.fetchone()

                cursor.execute(
                    f"""
                    INSERT INTO `{TARGET_TABLE}` (
                        target_key,
                        region,
                        province,
                        brand,
                        model,
                        vehicle_year,
                        status,
                        total_submodels,
                        complete_branches,
                        total_branches,
                        error_message,
                        updated_at
                    )
                    VALUES (
                        %s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,NOW()
                    )
                    ON DUPLICATE KEY UPDATE
                        status=VALUES(status),
                        total_submodels=VALUES(total_submodels),
                        complete_branches=VALUES(complete_branches),
                        total_branches=VALUES(total_branches),
                        error_message=VALUES(error_message),
                        updated_at=NOW()
                    """,
                    (
                        key,
                        region,
                        province,
                        vehicle.brand,
                        vehicle.model,
                        vehicle.year,
                        status,
                        total_submodels,
                        counts["done_count"] or 0,
                        counts["total"] or 0,
                        error_message[:2000],
                    ),
                )

            self.connection.commit()

        except Exception:
            self.connection.rollback()
            raise

    def stats(self):
        rows = self.execute(
            f"""
            SELECT
                COUNT(*) AS records,
                SUM(
                    detail_status='DONE'
                ) AS details_done,
                SUM(
                    detail_status='DETAIL_UNAVAILABLE'
                ) AS unavailable,
                SUM(
                    detail_status IN (
                        'PENDING',
                        'RETRY_REQUIRED'
                    )
                ) AS pending
            FROM `{RECORD_TABLE}`
            """,
            fetch=True,
        )

        return rows[0]

    def target_stats(self):
        rows = self.execute(
            f"""
            SELECT status, COUNT(*) AS total
            FROM `{TARGET_TABLE}`
            GROUP BY status
            """,
            fetch=True,
        )

        return {
            row["status"]: row["total"]
            for row in rows
        }

    def export_csv(
        self,
        output: Path,
    ) -> int:
        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary = output.with_suffix(
            ".tmp"
        )

        count = 0

        with self.connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM `{RECORD_TABLE}`
                ORDER BY
                    province,
                    brand,
                    model,
                    vehicle_year,
                    car_id,
                    repair_type,
                    insurer,
                    actual_sum_insured,
                    premium
                """
            )

            fields = [
                item[0]
                for item in cursor.description
            ]

            with temporary.open(
                "w",
                encoding="utf-8-sig",
                newline="",
            ) as stream:
                writer = csv.writer(
                    stream
                )

                writer.writerow(fields)

                while True:
                    records = cursor.fetchmany(
                        2000
                    )

                    if not records:
                        break

                    for record in records:
                        writer.writerow(
                            [
                                record.get(name)
                                for name in fields
                            ]
                        )

                        count += 1

        temporary.replace(
            output
        )

        return count

    def close(self):
        if self.connection is not None:
            self.connection.close()


# ============================================================
# COLLECTOR
# ============================================================

class ANCCollector:

    def __init__(
        self,
        db: Database,
        delay: float,
        limit: Optional[int],
        resume: bool,
        debug: bool,
        detail_mode: str,
        max_submodels: int,
        max_details: int,
    ):
        self.db = db

        self.dashboard = CollectorDashboard()

        self.http = HTTPClient(
            delay=delay,
            debug=debug,
            dashboard=self.dashboard,
        )

        self.catalog = VehicleCatalog(
            self.http
        )

        self.discovery = VehicleDiscovery(
            self.http,
            self.catalog,
        )

        self.filter = ListingFilter(
            self.http
        )

        self.parser = PackageParser(
            self.http
        )

        self.limit = limit
        self.resume = resume
        self.debug = debug
        self.detail_mode = detail_mode
        self.max_submodels = max_submodels
        self.max_details = max_details

        self.metrics = Counter()
        self.current_target = None

        self.started = time.monotonic()

    @staticmethod
    def targets():
        for region, province in PROVINCES:
            for vehicle in VEHICLES:
                yield region, province, vehicle

    def refresh_database_metrics(self):
        stats = self.db.stats()

        self.dashboard.update_database(
            records=stats["records"] or 0,
            detail_done=stats["details_done"] or 0,
            detail_unavailable=stats["unavailable"] or 0,
            pending=stats["pending"] or 0,
        )

    def save_listing_package(
        self,
        package: Package,
    ):
        status = self.db.get_package_status(
            package.record_key
        )

        if (
            self.resume
            and status in (
                "DONE",
                "DETAIL_UNAVAILABLE",
            )
        ):
            self.dashboard.skipped_this_run += 1

            self.dashboard.finish_package(
                "SKIPPED",
                (
                    f"{package.insurer_code} | "
                    f"{package.package_name} | "
                    "Already completed"
                ),
                "SKIP",
            )

            return

        result = self.db.save_package(
            package,
            "PENDING",
        )

        if result == "INSERTED":
            self.dashboard.saved_this_run += 1

        else:
            self.dashboard.updated_this_run += 1

        self.dashboard.finish_package(
            "SAVED",
            (
                f"{package.insurer_code} | "
                f"{package.package_name} | "
                f"{money_text(package.premium)} THB"
            ),
            "OK",
        )

    def enrich_package(
        self,
        package: Package,
    ):
        status = self.db.get_package_status(
            package.record_key
        )

        if (
            self.resume
            and status in (
                "DONE",
                "DETAIL_UNAVAILABLE",
            )
        ):
            self.dashboard.skipped_this_run += 1

            self.dashboard.finish_package(
                "SKIPPED",
                (
                    f"{package.insurer_code} | "
                    f"Detail already processed"
                ),
                "SKIP",
            )

            return status

        try:
            self.parser.enrich(
                package
            )

            package.detail_status = "DONE"

            self.db.save_package(
                package,
                "DONE",
            )

            self.dashboard.detail_done_this_run += 1

            self.dashboard.finish_package(
                "DETAIL_DONE",
                (
                    f"{package.insurer_code} | "
                    f"{package.package_name} | "
                    "Detail verified"
                ),
                "OK",
            )

            return "DONE"

        except AccessBlocked:
            raise

        except DiscoveryError as exc:
            text = str(exc)

            if (
                "Detail page unavailable:" in text
                or "Website error page:" in text
            ):
                package.detail_status = "DETAIL_UNAVAILABLE"
                package.validation_status = "LISTING_VERIFIED"
                package.validation_notes = (
                    "DETAIL_UNAVAILABLE;LISTING_ONLY"
                )

                self.db.save_package(
                    package,
                    "DETAIL_UNAVAILABLE",
                    text,
                )

                self.dashboard.detail_404_this_run += 1

                self.dashboard.finish_package(
                    "DETAIL_UNAVAILABLE",
                    (
                        f"{package.insurer_code} | "
                        "Detail 404 | Listing retained"
                    ),
                    "WARN",
                )

                return "DETAIL_UNAVAILABLE"

            package.detail_status = "RETRY_REQUIRED"
            package.validation_status = "LISTING_VERIFIED"
            package.validation_notes = "DETAIL_RETRY_REQUIRED"

            self.db.save_package(
                package,
                "RETRY_REQUIRED",
                text,
            )

            self.dashboard.detail_failed_this_run += 1

            self.dashboard.finish_package(
                "RETRY_REQUIRED",
                (
                    f"{package.insurer_code} | "
                    f"{text[:65]}"
                ),
                "ERROR",
            )

            return "RETRY_REQUIRED"

        except Exception as exc:
            package.detail_status = "RETRY_REQUIRED"
            package.validation_status = "LISTING_VERIFIED"
            package.validation_notes = "DETAIL_RETRY_REQUIRED"

            self.db.save_package(
                package,
                "RETRY_REQUIRED",
                str(exc),
            )

            self.dashboard.detail_failed_this_run += 1

            self.dashboard.finish_package(
                "RETRY_REQUIRED",
                (
                    f"{package.insurer_code} | "
                    f"{str(exc)[:65]}"
                ),
                "ERROR",
            )

            logger.exception(
                "PACKAGE ENRICHMENT FAILED | %s",
                package.record_key,
            )

            return "RETRY_REQUIRED"

    def collect_branch(
        self,
        region: str,
        province: str,
        vehicle: Vehicle,
        entry: CatalogEntry,
        base_soup: BeautifulSoup,
        base_url: str,
        repair_type: str,
    ) -> bool:
        target_id = target_key(
            province,
            vehicle,
        )

        branch_id = branch_key(
            province,
            vehicle,
            entry.car_id,
            repair_type,
        )

        existing_branch = self.db.get_branch_status(
            branch_id
        )

        if (
            self.resume
            and existing_branch
            and existing_branch["status"] == "DONE"
        ):
            self.dashboard.branches_skipped += 1

            self.dashboard.event(
                (
                    f"Branch skipped: Car ID {entry.car_id} | "
                    f"{repair_type} | DONE"
                ),
                "SKIP",
            )

            return True

        self.db.set_branch(
            branch_id,
            target_id,
            entry.car_id,
            repair_type,
            "RUNNING",
        )

        listing_url = ""
        issues = []

        try:
            self.dashboard.operation = (
                "Loading province + repair filtered listing"
            )

            self.dashboard.refresh()

            soup, listing_url = self.filter.filtered_listing(
                base_soup,
                base_url,
                province,
                repair_type,
            )

            packages, issues = self.parser.parse(
                soup,
                listing_url,
                region,
                province,
                vehicle,
                entry,
                repair_type,
            )

            if not packages:
                raise ValidationError(
                    "No verified packages in filtered listing"
                )

            truncated = (
                len(packages) > self.max_details
            )

            if truncated:
                packages = packages[
                    :self.max_details
                ]

                issues.append(
                    "MAX_DETAILS_LIMIT_REACHED"
                )

            self.dashboard.set_listing(
                len(packages)
            )

            self.db.set_branch(
                branch_id,
                target_id,
                entry.car_id,
                repair_type,
                "LISTING_CAPTURED",
                listing_url,
                issues,
            )

            # ------------------------------------------------
            # PHASE 1: PERSIST ALL LISTING PACKAGES
            # ------------------------------------------------

            self.dashboard.event(
                (
                    f"Saving {len(packages)} listing records "
                    "before enrichment"
                ),
                "INFO",
            )

            for index, package in enumerate(
                packages,
                start=1,
            ):
                if STOP_REQUESTED:
                    raise KeyboardInterrupt

                self.dashboard.set_package(
                    index,
                    len(packages),
                    package,
                    "Saving listing record",
                )

                self.save_listing_package(
                    package
                )

            self.refresh_database_metrics()

            self.db.set_branch(
                branch_id,
                target_id,
                entry.car_id,
                repair_type,
                "DETAIL_PENDING",
                listing_url,
                issues,
            )

            self.dashboard.event(
                "Listing committed | Starting detail enrichment",
                "OK",
            )

            # ------------------------------------------------
            # PHASE 2: DETAIL ENRICHMENT
            # ------------------------------------------------

            if self.detail_mode == "none":
                self.db.set_branch(
                    branch_id,
                    target_id,
                    entry.car_id,
                    repair_type,
                    "PARTIAL",
                    listing_url,
                    issues + [
                        "DETAIL_ENRICHMENT_DISABLED"
                    ],
                )

                return False

            details_complete = True

            self.dashboard.package_times.clear()

            for index, package in enumerate(
                packages,
                start=1,
            ):
                if STOP_REQUESTED:
                    raise KeyboardInterrupt

                self.dashboard.set_package(
                    index,
                    len(packages),
                    package,
                    "Fetching package detail",
                )

                status = self.enrich_package(
                    package
                )

                if status == "RETRY_REQUIRED":
                    details_complete = False

                self.refresh_database_metrics()

            complete = (
                not issues
                and not truncated
                and details_complete
            )

            final_status = (
                "DONE"
                if complete
                else "PARTIAL"
            )

            self.db.set_branch(
                branch_id,
                target_id,
                entry.car_id,
                repair_type,
                final_status,
                listing_url,
                issues,
            )

            if complete:
                self.dashboard.branches_done += 1
            else:
                self.dashboard.branches_partial += 1

            self.dashboard.event(
                (
                    f"Branch {final_status} | "
                    f"Car ID {entry.car_id} | "
                    f"{repair_type} | "
                    f"{len(packages)} packages"
                ),
                "OK" if complete else "WARN",
            )

            return complete

        except KeyboardInterrupt:
            self.db.set_branch(
                branch_id,
                target_id,
                entry.car_id,
                repair_type,
                "INTERRUPTED",
                listing_url=listing_url,
                issues=issues,
                error="User interruption",
            )

            self.dashboard.event(
                "Branch interrupted | Saved data preserved",
                "WARN",
            )

            raise

        except AccessBlocked:
            self.db.set_branch(
                branch_id,
                target_id,
                entry.car_id,
                repair_type,
                "BLOCKED",
                listing_url=listing_url,
                issues=issues,
                error="Website access restriction",
            )

            raise

        except Exception as exc:
            self.db.set_branch(
                branch_id,
                target_id,
                entry.car_id,
                repair_type,
                "FAILED",
                listing_url=listing_url,
                issues=issues,
                error=str(exc),
            )

            logger.exception(
                "BRANCH FAILED | province=%s "
                "| car_id=%s | repair=%s",
                province,
                entry.car_id,
                repair_type,
            )

            self.dashboard.event(
                (
                    f"Branch failed | Car ID {entry.car_id} "
                    f"| {repair_type} | {str(exc)[:65]}"
                ),
                "ERROR",
            )

            return False

    def collect_target(
        self,
        region: str,
        province: str,
        vehicle: Vehicle,
    ):
        target_id = target_key(
            province,
            vehicle,
        )

        existing = self.db.get_target_status(
            target_id
        )

        if (
            self.resume
            and existing
            and existing["status"] == "DONE"
        ):
            self.dashboard.targets_skipped += 1

            self.dashboard.event(
                "Target already complete | Skipped",
                "SKIP",
            )

            return "SKIPPED"

        entries = self.catalog.candidates(
            vehicle
        )

        if not entries:
            self.db.set_target(
                region,
                province,
                vehicle,
                "FAILED",
                0,
                "No matching catalog submodels",
            )

            self.dashboard.targets_failed += 1

            return "FAILED"

        total_entries = len(entries)

        truncated = (
            total_entries > self.max_submodels
        )

        if truncated:
            entries = entries[
                :self.max_submodels
            ]

        self.dashboard.submodel_total = len(
            entries
        )

        self.db.set_target(
            region,
            province,
            vehicle,
            "RUNNING",
            total_entries,
        )

        expected_branches = (
            len(entries) * len(REPAIR_TYPES)
        )

        completed_branches = 0

        for submodel_index, entry in enumerate(
            entries,
            start=1,
        ):
            if STOP_REQUESTED:
                raise KeyboardInterrupt

            self.dashboard.set_submodel(
                submodel_index,
                len(entries),
                entry,
            )

            existing_branches = {
                repair: self.db.get_branch_status(
                    branch_key(
                        province,
                        vehicle,
                        entry.car_id,
                        repair,
                    )
                )
                for repair in REPAIR_TYPES
            }

            if (
                self.resume
                and all(
                    value is not None
                    and value["status"] == "DONE"
                    for value in existing_branches.values()
                )
            ):
                completed_branches += len(
                    REPAIR_TYPES
                )

                self.dashboard.branches_skipped += len(
                    REPAIR_TYPES
                )

                self.dashboard.event(
                    (
                        f"Car ID {entry.car_id} | "
                        "Both repair branches already DONE"
                    ),
                    "SKIP",
                )

                continue

            try:
                self.dashboard.operation = (
                    "Resolving vehicle page"
                )

                self.dashboard.refresh()

                base_soup, base_url = (
                    self.discovery.resolve(
                        vehicle,
                        entry,
                    )
                )

            except AccessBlocked:
                raise

            except Exception as exc:
                logger.exception(
                    "VEHICLE FAILED | province=%s "
                    "| %s %s %s | car_id=%s",
                    province,
                    vehicle.brand,
                    vehicle.model,
                    vehicle.year,
                    entry.car_id,
                )

                self.dashboard.event(
                    (
                        f"Vehicle failed | Car ID {entry.car_id} "
                        f"| {str(exc)[:60]}"
                    ),
                    "ERROR",
                )

                continue

            for repair_index, repair in enumerate(
                REPAIR_TYPES,
                start=1,
            ):
                if STOP_REQUESTED:
                    raise KeyboardInterrupt

                self.dashboard.set_repair(
                    repair_index,
                    repair,
                )

                success = self.collect_branch(
                    region,
                    province,
                    vehicle,
                    entry,
                    base_soup,
                    base_url,
                    repair,
                )

                if success:
                    completed_branches += 1

        complete = (
            not truncated
            and completed_branches == expected_branches
        )

        status = (
            "DONE"
            if complete
            else "PARTIAL"
        )

        self.db.set_target(
            region,
            province,
            vehicle,
            status,
            total_entries,
            (
                ""
                if complete
                else (
                    f"Complete branches: "
                    f"{completed_branches}/{expected_branches}"
                )
            ),
        )

        if complete:
            self.dashboard.targets_done += 1
        else:
            self.dashboard.targets_partial += 1

        self.dashboard.event(
            (
                f"Target {status} | "
                f"Branches {completed_branches}/"
                f"{expected_branches}"
            ),
            "OK" if complete else "WARN",
        )

        return status

    def run(self):
        targets = list(
            self.targets()
        )

        if self.limit is not None:
            targets = targets[
                :self.limit
            ]

        self.dashboard.target_total = len(
            targets
        )

        self.refresh_database_metrics()

        self.dashboard.start()

        try:
            self.dashboard.event(
                "Loading vehicle catalog",
                "INFO",
            )

            self.catalog.load()

            for index, (
                region,
                province,
                vehicle,
            ) in enumerate(
                targets,
                start=1,
            ):
                if STOP_REQUESTED:
                    break

                self.dashboard.set_target(
                    index,
                    len(targets),
                    province,
                    vehicle,
                )

                self.current_target = (
                    region,
                    province,
                    vehicle,
                )

                try:
                    self.collect_target(
                        region,
                        province,
                        vehicle,
                    )

                except AccessBlocked as exc:
                    self.db.set_target(
                        region,
                        province,
                        vehicle,
                        "BLOCKED",
                        0,
                        str(exc),
                    )

                    self.dashboard.status = "BLOCKED"

                    self.dashboard.event(
                        f"Access blocked: {exc}",
                        "ERROR",
                    )

                    break

                except KeyboardInterrupt:
                    self.db.set_target(
                        region,
                        province,
                        vehicle,
                        "INTERRUPTED",
                        0,
                        "User interruption",
                    )

                    self.dashboard.status = "INTERRUPTED"

                    self.dashboard.event(
                        "Interrupted | Saved records preserved",
                        "WARN",
                    )

                    break

                except Exception as exc:
                    logger.exception(
                        "TARGET FAILED | %s %s %s",
                        province,
                        vehicle.brand,
                        vehicle.model,
                    )

                    self.db.set_target(
                        region,
                        province,
                        vehicle,
                        "FAILED",
                        0,
                        str(exc),
                    )

                    self.dashboard.targets_failed += 1

                    self.dashboard.event(
                        f"Target failed: {str(exc)[:75]}",
                        "ERROR",
                    )

            if STOP_REQUESTED:
                self.dashboard.status = "INTERRUPTED"

            elif self.dashboard.status == "RUNNING":
                self.dashboard.status = "COMPLETED"

            self.refresh_database_metrics()
            self.dashboard.refresh(force=True)

        finally:
            self.dashboard.stop()

    def close(self):
        self.http.close()


# ============================================================
# COMMANDS
# ============================================================

def print_catalog(delay: float):
    http = HTTPClient(
        delay=delay
    )

    try:
        catalog = VehicleCatalog(
            http
        )

        catalog.load()

        table = Table(
            title="ANC Vehicle Catalog"
        )

        table.add_column("Brand")
        table.add_column("Model")
        table.add_column("Year")
        table.add_column(
            "Submodels",
            justify="right",
        )
        table.add_column("Car IDs")

        for vehicle in VEHICLES:
            entries = catalog.candidates(
                vehicle
            )

            table.add_row(
                vehicle.brand,
                vehicle.model,
                str(vehicle.year),
                str(len(entries)),
                ", ".join(
                    str(entry.car_id)
                    for entry in entries
                ),
            )

        console.print(table)

        console.print(
            f"Catalog: {CATALOG_PATH.resolve()}"
        )

    finally:
        http.close()


def dry_run():
    table = Table(
        title="ANC Collection Scope"
    )

    table.add_column("Brand")
    table.add_column("Model")
    table.add_column(
        "Year",
        justify="right",
    )

    for vehicle in VEHICLES:
        table.add_row(
            vehicle.brand,
            vehicle.model,
            str(vehicle.year),
        )

    console.print(table)

    console.print(
        f"Provinces={len(PROVINCES)} "
        f"Vehicles={len(VEHICLES)} "
        f"Targets={len(PROVINCES) * len(VEHICLES)}"
    )


def probe(delay: float):
    http = HTTPClient(
        delay=delay,
        debug=True,
    )

    try:
        for name, url in (
            ("HOME", BASE_URL + "/"),
            ("CLASS_ONE", CLASS_ONE_URL),
        ):
            soup, final_url = http.get(
                url
            )

            if isinstance(soup, str):
                raise DiscoveryError(
                    "Unexpected AJAX response"
                )

            result = {
                "page": name,
                "url": final_url,
                "title": clean(
                    soup.title.get_text(
                        " ",
                        strip=True,
                    )
                    if soup.title
                    else ""
                ),
                "selects": [
                    element.get("id")
                    for element in soup.find_all(
                        "select"
                    )
                ],
                "catalog_links": len(
                    soup.find_all(
                        "a",
                        id=re.compile(
                            "rptCarBrand_rptCarModel"
                        ),
                    )
                ),
            }

            console.print(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    indent=2,
                )
            )

            http.save_debug(
                soup,
                name.lower(),
            )

    finally:
        http.close()


def summary(
    collector: ANCCollector,
    db: Database,
    csv_rows: int,
):
    stats = db.stats()
    targets = db.target_stats()
    dashboard = collector.dashboard

    table = Table(
        title="ANC Collector v4.1 Summary"
    )

    table.add_column("Metric")
    table.add_column(
        "Value",
        justify="right",
    )

    values = {
        "HTTP requests": collector.http.requests_count,
        "HTTP success": collector.http.success_count,
        "HTTP 404": collector.http.not_found_count,
        "HTTP errors": collector.http.error_count,
        "Database records": stats["records"] or 0,
        "Detail done": stats["details_done"] or 0,
        "Detail unavailable": stats["unavailable"] or 0,
        "Detail pending": stats["pending"] or 0,
        "Saved this run": dashboard.saved_this_run,
        "Updated this run": dashboard.updated_this_run,
        "Skipped this run": dashboard.skipped_this_run,
        "Targets done": targets.get("DONE", 0),
        "Targets partial": targets.get("PARTIAL", 0),
        "Targets failed": targets.get("FAILED", 0),
        "Targets interrupted": targets.get(
            "INTERRUPTED",
            0,
        ),
        "Targets blocked": targets.get("BLOCKED", 0),
        "CSV rows": csv_rows,
        "Elapsed": format_duration(
            time.monotonic() - collector.started
        ),
        "Run status": dashboard.status,
    }

    for name, value in values.items():
        table.add_row(
            name,
            str(value),
        )

    console.print(table)

def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "ANC Broker Collector v4.1 "
            "- Package-level Live Dashboard"
        )
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    parser.add_argument(
        "--catalog",
        action="store_true",
    )

    parser.add_argument(
        "--probe",
        action="store_true",
    )

    parser.add_argument(
        "--export-only",
        action="store_true",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
    )

    parser.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=True,
    )

    parser.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
    )

    parser.add_argument(
        "--detail-mode",
        choices=("all", "none"),
        default="all",
    )

    parser.add_argument(
        "--max-submodels",
        type=int,
        default=100,
    )

    parser.add_argument(
        "--max-details",
        type=int,
        default=500,
    )

    parser.add_argument(
        "--debug",
        action="store_true",
    )

    parser.add_argument(
        "--output",
        default=str(CSV_PATH),
    )

    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error(
            "--limit must be >= 1"
        )

    if args.delay < 0.2:
        parser.error(
            "--delay must be >= 0.2"
        )

    if args.max_submodels < 1:
        parser.error(
            "--max-submodels must be >= 1"
        )

    if args.max_details < 1:
        parser.error(
            "--max-details must be >= 1"
        )

    return args


# ============================================================
# MAIN
# ============================================================

def main():
    args = parse_args()

    if args.dry_run:
        dry_run()
        return 0

    if args.catalog:
        print_catalog(
            args.delay
        )
        return 0

    if args.probe:
        probe(
            args.delay
        )
        return 0

    db = Database()
    collector = None

    try:
        db.connect()
        db.initialize()

        output = Path(
            args.output
        ).resolve()

        if args.export_only:
            count = db.export_csv(
                output
            )

            console.print(
                Panel.fit(
                    f"Rows: {count:,}\n"
                    f"CSV: {output}",
                    title="EXPORT COMPLETE",
                    border_style="green",
                )
            )

            return 0

        collector = ANCCollector(
            db=db,
            delay=args.delay,
            limit=args.limit,
            resume=args.resume,
            debug=args.debug,
            detail_mode=args.detail_mode,
            max_submodels=args.max_submodels,
            max_details=args.max_details,
        )

        collector.run()

        count = db.export_csv(
            output
        )

        summary(
            collector,
            db,
            count,
        )

        console.print(
            f"CSV: {output}"
        )

        logger.info(
            "RUN END | status=%s | HTTP=%s "
            "| CSV_ROWS=%s",
            collector.dashboard.status,
            collector.http.requests_count,
            count,
        )

        return (
            130
            if STOP_REQUESTED
            else 0
        )

    except KeyboardInterrupt:
        logger.warning(
            "RUN INTERRUPTED"
        )

        console.print(
            "[yellow]"
            "Interrupted safely. "
            "Committed records remain in MySQL."
            "[/yellow]"
        )

        try:
            if collector:
                collector.dashboard.stop()

                if collector.current_target:
                    region, province, vehicle = (
                        collector.current_target
                    )

                    db.set_target(
                        region,
                        province,
                        vehicle,
                        "INTERRUPTED",
                        0,
                        "User interruption",
                    )

            output = Path(
                args.output
            ).resolve()

            count = db.export_csv(
                output
            )

            console.print(
                f"CSV rows preserved: {count:,}"
            )

        except Exception:
            logger.exception(
                "INTERRUPTION FINALIZATION FAILED"
            )

        return 130

    except Exception as exc:
        logger.exception(
            "FATAL ERROR"
        )

        console.print(
            f"[bold red]ERROR: {exc}[/bold red]"
        )

        return 1

    finally:
        if collector:
            collector.close()

        db.close()


if __name__ == "__main__":
    sys.exit(
        main()
    )