from __future__ import annotations
import enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime
from pathlib import Path


class MaterialType(str, enum.Enum):
    DOOR_PHOTO = "door_photo"
    COUNTER_PHOTO = "counter_photo"
    WAREHOUSE_PHOTO = "warehouse_photo"
    SENSOR_CSV = "sensor_csv"
    ACCESS_LOG = "access_log"
    DUTY_NOTE = "duty_note"
    EXCEPTION_REGISTER = "exception_register"
    UNKNOWN = "unknown"


class Severity(str, enum.Enum):
    BLOCKING = "blocking"
    REVIEW = "review"
    REMINDER = "reminder"


MATERIAL_LABELS = {
    MaterialType.DOOR_PHOTO: "门头照",
    MaterialType.COUNTER_PHOTO: "收银台照",
    MaterialType.WAREHOUSE_PHOTO: "仓库照",
    MaterialType.SENSOR_CSV: "温湿度记录",
    MaterialType.ACCESS_LOG: "门禁日志",
    MaterialType.DUTY_NOTE: "值班备注",
    MaterialType.EXCEPTION_REGISTER: "异常登记表",
}

REQUIRED_MATERIALS = [
    MaterialType.DOOR_PHOTO,
    MaterialType.COUNTER_PHOTO,
    MaterialType.WAREHOUSE_PHOTO,
    MaterialType.SENSOR_CSV,
    MaterialType.ACCESS_LOG,
    MaterialType.DUTY_NOTE,
]


@dataclass
class FileInfo:
    path: Path
    original_filename: str
    store_id: Optional[str] = None
    date: Optional[str] = None
    material_type: MaterialType = MaterialType.UNKNOWN
    timestamp: Optional[datetime] = None
    content_hash: Optional[str] = None
    inferred_store: Optional[str] = None
    inferred_date: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ManifestEntry:
    source_path: str
    target_path: str
    content_hash: str
    store_id: str
    date: str
    material_type: str
    original_filename: str
    new_filename: str


@dataclass
class ExceptionItem:
    severity: Severity
    rule_id: str
    description: str
    related_files: List[str] = field(default_factory=list)
    suggestion: str = ""
    store_id: Optional[str] = None
    date: Optional[str] = None
    file_path: Optional[str] = None


@dataclass
class RuleHit:
    rule_id: str
    rule_name: str
    description: str
    severity: Severity
    related_files: List[str] = field(default_factory=list)
    suggestion: str = ""
