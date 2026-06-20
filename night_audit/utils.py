from __future__ import annotations
import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

from .models import MaterialType


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


STORE_PATTERN = re.compile(r"(?:S|STORE|store|s)[_-]?(\d{3,4})", re.IGNORECASE)
DATE_PATTERN = re.compile(
    r"(\d{4})[_-](\d{1,2})[_-](\d{1,2})|"
    r"(\d{4})(\d{2})(\d{2})|"
    r"(\d{4})[.](\d{1,2})[.](\d{1,2})"
)
TIME_PATTERN_SEP = re.compile(r"(\d{1,2})[:_-](\d{2})")
TIME_PATTERN_COMPACT = re.compile(r"(?:^|[_\-\s])(\d{1,2})(\d{2})(?:$|[_\-\s.])")


PHOTO_KEYWORDS = {
    MaterialType.DOOR_PHOTO: ["door", "entrance", "门头", "men"],
    MaterialType.COUNTER_PHOTO: ["counter", "cashier", "收银", "shou"],
    MaterialType.WAREHOUSE_PHOTO: ["warehouse", "storage", "仓库", "cang"],
}
SENSOR_KEYWORDS = ["sensor", "temp", "humidity", "温湿", "csv"]
ACCESS_KEYWORDS = ["access", "door_log", "门禁", "gate"]
DUTY_KEYWORDS = ["duty", "note", "shift", "值班", "备注"]
EXCEPTION_KEYWORDS = ["exception", "anomaly", "异常", "alarm"]


def parse_store_id(filename: str) -> Optional[str]:
    m = STORE_PATTERN.search(filename)
    if m:
        return f"S{m.group(1).zfill(3)}"
    return None


def parse_date_from_filename(filename: str) -> Optional[str]:
    m = DATE_PATTERN.search(filename)
    if not m:
        return None
    groups = [g for g in m.groups() if g is not None]
    if len(groups) >= 3:
        y, mo, d = groups[0], groups[1], groups[2]
        try:
            dt = datetime(int(y), int(mo), int(d))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def parse_time_from_filename(filename: str) -> Optional[Tuple[int, int]]:
    date_pattern = re.compile(r"\d{4}[_\-.\s]\d{1,2}[_\-.\s]\d{1,2}")
    cleaned = date_pattern.sub("", filename)
    m = TIME_PATTERN_SEP.search(cleaned)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    m = TIME_PATTERN_COMPACT.search(cleaned)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    return None


def classify_material(filename: str) -> MaterialType:
    lower = filename.lower()
    ext = Path(filename).suffix.lower()
    if ext in (".jpg", ".jpeg", ".png"):
        for mt, keywords in PHOTO_KEYWORDS.items():
            for kw in keywords:
                if kw in lower:
                    return mt
        return MaterialType.DOOR_PHOTO
    if ext == ".csv":
        for kw in SENSOR_KEYWORDS:
            if kw in lower:
                return MaterialType.SENSOR_CSV
        for kw in EXCEPTION_KEYWORDS:
            if kw in lower:
                return MaterialType.EXCEPTION_REGISTER
        return MaterialType.SENSOR_CSV
    if ext == ".json":
        for kw in EXCEPTION_KEYWORDS:
            if kw in lower:
                return MaterialType.EXCEPTION_REGISTER
        return MaterialType.EXCEPTION_REGISTER
    if ext == ".txt":
        for kw in ACCESS_KEYWORDS:
            if kw in lower:
                return MaterialType.ACCESS_LOG
        for kw in DUTY_KEYWORDS:
            if kw in lower:
                return MaterialType.DUTY_NOTE
        return MaterialType.ACCESS_LOG
    return MaterialType.UNKNOWN


def adjust_date_for_night_shift(dt: datetime, cutoff_hour: int = 6) -> str:
    if 0 <= dt.hour < cutoff_hour:
        adjusted = dt - timedelta(days=1)
        return adjusted.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d")


def stable_filename(
    store_id: str, date: str, material_type: MaterialType, index: int, ext: str
) -> str:
    return f"{store_id}_{date}_{material_type.value}_{index:03d}{ext}"


def sanitize_store_id(raw: str) -> str:
    s = raw.strip().upper()
    if not s.startswith("S"):
        s = "S" + s
    return s
