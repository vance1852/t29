from __future__ import annotations
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .models import FileInfo, MaterialType
from .utils import (
    file_hash,
    parse_store_id,
    parse_date_from_filename,
    parse_time_from_filename,
    classify_material,
    adjust_date_for_night_shift,
)


def scan_directory(input_dir: str, cutoff_hour: int = 6) -> List[FileInfo]:
    base = Path(input_dir)
    if not base.exists():
        raise FileNotFoundError(f"输入目录不存在: {base}")

    results: List[FileInfo] = []
    for path in sorted(base.rglob("*")):
        if path.is_dir():
            continue
        if path.name.startswith("."):
            continue

        fi = _parse_file(path, base, cutoff_hour)
        if fi is not None:
            results.append(fi)

    results = _infer_missing_metadata(results)
    return results


def _parse_file(path: Path, base: Path, cutoff_hour: int) -> Optional[FileInfo]:
    rel = path.relative_to(base)
    filename = path.name

    store_id = parse_store_id(filename)
    date_str = parse_date_from_filename(filename)
    material_type = classify_material(filename)
    time_tuple = parse_time_from_filename(filename)

    timestamp = None
    if date_str and time_tuple:
        try:
            timestamp = datetime.strptime(
                f"{date_str} {time_tuple[0]:02d}:{time_tuple[1]:02d}",
                "%Y-%m-%d %H:%M",
            )
        except ValueError:
            pass

    inferred_store = None
    inferred_date = None

    if store_id is None and material_type in (
        MaterialType.SENSOR_CSV,
        MaterialType.ACCESS_LOG,
        MaterialType.DUTY_NOTE,
        MaterialType.EXCEPTION_REGISTER,
    ):
        inferred_store = _try_read_store_from_content(path, material_type)

    if date_str is None:
        inferred_date = _try_read_date_from_content(path, material_type)

    if date_str and timestamp:
        date_str = adjust_date_for_night_shift(timestamp, cutoff_hour)
    elif date_str and time_tuple:
        try:
            dt = datetime.strptime(f"{date_str} {time_tuple[0]:02d}:{time_tuple[1]:02d}", "%Y-%m-%d %H:%M")
            date_str = adjust_date_for_night_shift(dt, cutoff_hour)
        except ValueError:
            pass

    chash = None
    try:
        chash = file_hash(path)
    except OSError:
        pass

    return FileInfo(
        path=path,
        original_filename=filename,
        store_id=store_id or inferred_store,
        date=date_str or inferred_date,
        material_type=material_type,
        timestamp=timestamp,
        content_hash=chash,
        inferred_store=inferred_store,
        inferred_date=inferred_date,
    )


def _try_read_store_from_content(path: Path, mt: MaterialType) -> Optional[str]:
    try:
        if mt == MaterialType.SENSOR_CSV:
            with open(path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if header and "store_id" in header:
                    row = next(reader, None)
                    if row:
                        idx = header.index("store_id")
                        if row[idx]:
                            return f"S{row[idx].strip().lstrip('Ss').zfill(3)}"
        elif mt in (MaterialType.ACCESS_LOG, MaterialType.DUTY_NOTE):
            with open(path, "r", encoding="utf-8") as f:
                first_line = f.readline()
            if "门店:" in first_line or "门店：" in first_line:
                for sep in ("门店:", "门店："):
                    if sep in first_line:
                        after = first_line.split(sep)[1]
                        sid = after.split()[0].strip().rstrip(" ,，")
                        return f"S{sid.lstrip('Ss').zfill(3)}"
        elif mt == MaterialType.EXCEPTION_REGISTER:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                sid = data[0].get("store_id", "")
                if sid:
                    return f"S{sid.lstrip('Ss').zfill(3)}"
    except Exception:
        pass
    return None


def _try_read_date_from_content(path: Path, mt: MaterialType) -> Optional[str]:
    try:
        if mt in (MaterialType.ACCESS_LOG, MaterialType.DUTY_NOTE):
            with open(path, "r", encoding="utf-8") as f:
                first_line = f.readline()
            for sep in ("日期:", "日期："):
                if sep in first_line:
                    after = first_line.split(sep)[1]
                    date_part = after.strip().split()[0].rstrip(" ,，")
                    try:
                        dt = datetime.strptime(date_part, "%Y-%m-%d")
                        return dt.strftime("%Y-%m-%d")
                    except ValueError:
                        pass
        elif mt == MaterialType.SENSOR_CSV:
            with open(path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if header and "timestamp" in header:
                    row = next(reader, None)
                    if row:
                        idx = header.index("timestamp")
                        ts = row[idx][:10]
                        try:
                            datetime.strptime(ts, "%Y-%m-%d")
                            return ts
                        except ValueError:
                            pass
        elif mt == MaterialType.EXCEPTION_REGISTER:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and data:
                d = data[0].get("date", "")
                if d:
                    try:
                        datetime.strptime(d, "%Y-%m-%d")
                        return d
                    except ValueError:
                        pass
    except Exception:
        pass
    return None


def _infer_missing_metadata(files: List[FileInfo]) -> List[FileInfo]:
    known_stores = set()
    known_dates = set()
    for fi in files:
        if fi.store_id and not fi.inferred_store:
            known_stores.add(fi.store_id)
        if fi.date and not fi.inferred_date:
            known_dates.add(fi.date)

    for fi in files:
        if fi.store_id is None:
            pass
    return files
