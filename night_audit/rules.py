from __future__ import annotations
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .models import (
    ExceptionItem,
    FileInfo,
    MaterialType,
    REQUIRED_MATERIALS,
    Severity,
)
from .utils import file_hash


THRESHOLDS = {
    "default": {"temp_min": 16, "temp_max": 30, "humidity_min": 30, "humidity_max": 80},
    "FREEZER": {"temp_min": -25, "temp_max": -15},
    "METER": {"spike_ratio": 3.0, "regress_check": True},
}

FREEZER_CONSECUTIVE_COUNT = 2
PHOTO_TOLERANCE_MINUTES = 60


def run_all_rules(
    files: List[FileInfo],
    cutoff_hour: int = 6,
) -> List[ExceptionItem]:
    exceptions: List[ExceptionItem] = []

    grouped = _group_by_store_date(files)
    device_stores = _build_device_store_map(files)

    exceptions.extend(rule_missing_store(files))
    exceptions.extend(rule_missing_date(files))
    exceptions.extend(rule_required_materials(grouped))

    for key, file_list in grouped.items():
        store_id, date = key
        exceptions.extend(rule_sensor_thresholds(store_id, date, file_list))
        exceptions.extend(rule_freezer_consecutive_overtemp(store_id, date, file_list))
        exceptions.extend(rule_access_after_close(store_id, date, file_list))
        exceptions.extend(rule_meter_anomaly(store_id, date, file_list))
        exceptions.extend(rule_photo_sensor_time_consistency(store_id, date, file_list))

    exceptions.extend(rule_exception_register_correlation(grouped))
    exceptions.extend(rule_device_id_conflict(device_stores))
    exceptions.extend(rule_duplicate_content(files))

    return exceptions


def _group_by_store_date(
    files: List[FileInfo],
) -> Dict[Tuple[str, str], List[FileInfo]]:
    groups: Dict[Tuple[str, str], List[FileInfo]] = defaultdict(list)
    for fi in files:
        if fi.store_id and fi.date:
            groups[(fi.store_id, fi.date)].append(fi)
    return groups


def _build_device_store_map(
    files: List[FileInfo],
) -> Dict[str, Set[str]]:
    device_stores: Dict[str, Set[str]] = defaultdict(set)
    for fi in files:
        if fi.material_type == MaterialType.SENSOR_CSV and fi.store_id:
            try:
                with open(fi.path, "r", encoding="utf-8-sig") as f:
                    reader = csv.reader(f)
                    header = next(reader, None)
                    if header and "device_id" in header:
                        didx = header.index("device_id")
                        sidx = header.index("store_id") if "store_id" in header else -1
                        seen_devices = set()
                        for row in reader:
                            if len(row) > max(didx, 0):
                                dev = row[didx]
                                if dev and dev not in seen_devices:
                                    seen_devices.add(dev)
                                    row_store = row[sidx] if sidx >= 0 and len(row) > sidx else fi.store_id
                                    device_stores[dev].add(row_store)
            except Exception:
                pass
    return device_stores


def rule_missing_store(files: List[FileInfo]) -> List[ExceptionItem]:
    results = []
    for fi in files:
        if fi.store_id is None:
            sev = Severity.REVIEW if fi.inferred_store else Severity.BLOCKING
            results.append(ExceptionItem(
                severity=sev,
                rule_id="R001",
                description=f"文件缺少门店编号: {fi.original_filename}",
                related_files=[str(fi.path)],
                suggestion="从内容推断门店编号" if fi.inferred_store else "请手动补充门店编号",
                store_id=fi.inferred_store,
                date=fi.date,
                file_path=str(fi.path),
            ))
    return results


def rule_missing_date(files: List[FileInfo]) -> List[ExceptionItem]:
    results = []
    for fi in files:
        if fi.date is None:
            sev = Severity.REVIEW if fi.inferred_date else Severity.BLOCKING
            results.append(ExceptionItem(
                severity=sev,
                rule_id="R002",
                description=f"文件缺少日期: {fi.original_filename}",
                related_files=[str(fi.path)],
                suggestion="从内容推断日期" if fi.inferred_date else "请手动补充日期",
                store_id=fi.store_id,
                date=fi.inferred_date,
                file_path=str(fi.path),
            ))
    return results


def rule_required_materials(
    grouped: Dict[Tuple[str, str], List[FileInfo]],
) -> List[ExceptionItem]:
    results = []
    for (store_id, date), file_list in grouped.items():
        present = {fi.material_type for fi in file_list}
        for mt in REQUIRED_MATERIALS:
            if mt not in present:
                results.append(ExceptionItem(
                    severity=Severity.BLOCKING,
                    rule_id="R003",
                    description=f"门店 {store_id} 日期 {date} 缺少必交材料: {mt.value}",
                    related_files=[str(fi.path) for fi in file_list],
                    suggestion=f"请补传 {mt.value} 材料",
                    store_id=store_id,
                    date=date,
                ))
    return results


def rule_sensor_thresholds(
    store_id: str, date: str, files: List[FileInfo]
) -> List[ExceptionItem]:
    results = []
    for fi in files:
        if fi.material_type != MaterialType.SENSOR_CSV:
            continue
        try:
            anomalies = _check_sensor_file(fi)
            for anom in anomalies:
                results.append(ExceptionItem(
                    severity=Severity.REVIEW,
                    rule_id="R004",
                    description=f"门店 {store_id} 日期 {date} 传感器超阈值: {anom}",
                    related_files=[str(fi.path)],
                    suggestion="检查传感器是否正常工作",
                    store_id=store_id,
                    date=date,
                    file_path=str(fi.path),
                ))
        except Exception:
            pass
    return results


def _check_sensor_file(fi: FileInfo) -> List[str]:
    anomalies = []
    with open(fi.path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            dev = row.get("device_id", "")
            temp_str = row.get("temperature", "")
            hum_str = row.get("humidity", "")
            if "FREEZER" in dev:
                ft_str = row.get("freezer_temp", "")
                if ft_str:
                    try:
                        ft = float(ft_str)
                        thresh = THRESHOLDS["FREEZER"]
                        if ft > thresh["temp_max"] or ft < thresh["temp_min"]:
                            anomalies.append(f"{dev} 冰柜温度={ft}")
                    except ValueError:
                        pass
            elif "METER" not in dev:
                if temp_str:
                    try:
                        t = float(temp_str)
                        thresh = THRESHOLDS["default"]
                        if t > thresh["temp_max"] or t < thresh["temp_min"]:
                            anomalies.append(f"{dev} 温度={t}")
                    except ValueError:
                        pass
                if hum_str:
                    try:
                        h = float(hum_str)
                        thresh = THRESHOLDS["default"]
                        if h > thresh["humidity_max"] or h < thresh["humidity_min"]:
                            anomalies.append(f"{dev} 湿度={h}")
                    except ValueError:
                        pass
    return anomalies


def rule_freezer_consecutive_overtemp(
    store_id: str, date: str, files: List[FileInfo]
) -> List[ExceptionItem]:
    results = []
    for fi in files:
        if fi.material_type != MaterialType.SENSOR_CSV:
            continue
        try:
            overtemp_runs = _check_freezer_consecutive(fi)
            if overtemp_runs:
                for run_info in overtemp_runs:
                    results.append(ExceptionItem(
                        severity=Severity.BLOCKING,
                        rule_id="R005",
                        description=f"门店 {store_id} 日期 {date} 冰柜连续超温: {run_info}",
                        related_files=[str(fi.path)],
                        suggestion="立即检查冰柜设备，确认食品是否安全",
                        store_id=store_id,
                        date=date,
                        file_path=str(fi.path),
                    ))
        except Exception:
            pass
    return results


def _check_freezer_consecutive(fi: FileInfo) -> List[str]:
    freezer_readings: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
    with open(fi.path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            dev = row.get("device_id", "")
            if "FREEZER" not in dev:
                continue
            ft_str = row.get("freezer_temp", "")
            ts_str = row.get("timestamp", "")
            if ft_str and ts_str:
                try:
                    ft = float(ft_str)
                    freezer_readings[dev].append((ts_str, ft))
                except ValueError:
                    pass

    overtemp_runs = []
    for dev, readings in freezer_readings.items():
        consec = 0
        start_ts = None
        thresh = THRESHOLDS["FREEZER"]
        for ts, ft in readings:
            if ft > thresh["temp_max"]:
                consec += 1
                if consec == 1:
                    start_ts = ts
                if consec >= FREEZER_CONSECUTIVE_COUNT:
                    overtemp_runs.append(
                        f"{dev} 从 {start_ts} 起连续 {consec} 条超温 (>{thresh['temp_max']}°C)"
                    )
                    break
            else:
                consec = 0
                start_ts = None
    return overtemp_runs


def rule_access_after_close(
    store_id: str, date: str, files: List[FileInfo]
) -> List[ExceptionItem]:
    results = []
    access_files = [fi for fi in files if fi.material_type == MaterialType.ACCESS_LOG]
    exception_files = [fi for fi in files if fi.material_type == MaterialType.EXCEPTION_REGISTER]

    for afi in access_files:
        after_close_events = _check_access_after_close(afi)
        if after_close_events:
            has_exception_entry = False
            for efi in exception_files:
                try:
                    with open(efi.path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            if "门禁" in item.get("type", "") or "door" in item.get("type", "").lower():
                                has_exception_entry = True
                                break
                except Exception:
                    pass

            desc_suffix = ""
            if not has_exception_entry:
                desc_suffix = "（异常登记表中无对应记录！）"

            sev = Severity.REVIEW if has_exception_entry else Severity.BLOCKING
            for evt in after_close_events:
                results.append(ExceptionItem(
                    severity=sev,
                    rule_id="R006",
                    description=f"门店 {store_id} 日期 {date} 闭店后开门记录: {evt}{desc_suffix}",
                    related_files=[str(afi.path)] + [str(efi.path) for efi in exception_files],
                    suggestion="核实开门原因" if has_exception_entry else "闭店后异常开门且无登记，需立即核实",
                    store_id=store_id,
                    date=date,
                    file_path=str(afi.path),
                ))
    return results


def _check_access_after_close(fi: FileInfo) -> List[str]:
    events = []
    try:
        with open(fi.path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return events

    for line in lines:
        if "异常" in line or "未知" in line:
            events.append(line.strip())
    return events


def rule_meter_anomaly(
    store_id: str, date: str, files: List[FileInfo]
) -> List[ExceptionItem]:
    results = []
    for fi in files:
        if fi.material_type != MaterialType.SENSOR_CSV:
            continue
        try:
            anomalies = _check_meter_anomaly(fi)
            for anom in anomalies:
                results.append(ExceptionItem(
                    severity=Severity.REVIEW,
                    rule_id="R007",
                    description=f"门店 {store_id} 日期 {date} 电表异常: {anom}",
                    related_files=[str(fi.path)],
                    suggestion="检查电表读数是否正确",
                    store_id=store_id,
                    date=date,
                    file_path=str(fi.path),
                ))
        except Exception:
            pass
    return results


def _check_meter_anomaly(fi: FileInfo) -> List[str]:
    anomalies = []
    meter_readings: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
    with open(fi.path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            dev = row.get("device_id", "")
            meter_str = row.get("meter_reading", "")
            ts_str = row.get("timestamp", "")
            if "METER" in dev and meter_str:
                try:
                    m = float(meter_str)
                    meter_readings[dev].append((ts_str, m))
                except ValueError:
                    pass

    for dev, readings in meter_readings.items():
        for i in range(1, len(readings)):
            prev_ts, prev_val = readings[i - 1]
            curr_ts, curr_val = readings[i]
            if curr_val < prev_val:
                anomalies.append(
                    f"{dev} 电表倒退: {prev_ts}={prev_val} -> {curr_ts}={curr_val}"
                )
            if prev_val > 0 and curr_val / prev_val > THRESHOLDS["METER"]["spike_ratio"]:
                anomalies.append(
                    f"{dev} 电表突增: {prev_ts}={prev_val} -> {curr_ts}={curr_val} (比率={curr_val/prev_val:.1f})"
                )
    return anomalies


def rule_photo_sensor_time_consistency(
    store_id: str, date: str, files: List[FileInfo]
) -> List[ExceptionItem]:
    results = []
    photos = [fi for fi in files if fi.material_type in (
        MaterialType.DOOR_PHOTO, MaterialType.COUNTER_PHOTO, MaterialType.WAREHOUSE_PHOTO
    )]
    sensors = [fi for fi in files if fi.material_type == MaterialType.SENSOR_CSV]

    if not photos or not sensors:
        return results

    sensor_time = None
    for sfi in sensors:
        if sfi.timestamp:
            sensor_time = sfi.timestamp
            break

    if sensor_time is None:
        return results

    for pfi in photos:
        if pfi.timestamp is None:
            continue
        diff = abs((pfi.timestamp - sensor_time).total_seconds())
        if diff > PHOTO_TOLERANCE_MINUTES * 60:
            results.append(ExceptionItem(
                severity=Severity.REMINDER,
                rule_id="R008",
                description=f"门店 {store_id} 日期 {date} 照片时间与传感器时间不一致: 照片={pfi.timestamp}, 传感器={sensor_time}",
                related_files=[str(pfi.path), str(sensors[0].path)],
                suggestion="确认照片是否属于该时段",
                store_id=store_id,
                date=date,
                file_path=str(pfi.path),
            ))
    return results


def rule_exception_register_correlation(
    grouped: Dict[Tuple[str, str], List[FileInfo]],
) -> List[ExceptionItem]:
    results = []
    for (store_id, date), file_list in grouped.items():
        exception_files = [fi for fi in file_list if fi.material_type == MaterialType.EXCEPTION_REGISTER]
        sensor_files = [fi for fi in file_list if fi.material_type == MaterialType.SENSOR_CSV]

        for efi in exception_files:
            try:
                with open(efi.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    continue
                for item in data:
                    if "冰柜" in item.get("type", "") or "freezer" in item.get("type", "").lower():
                        has_sensor_evidence = False
                        for sfi in sensor_files:
                            freezer_anomalies = _check_freezer_consecutive(sfi)
                            if freezer_anomalies:
                                has_sensor_evidence = True
                                break
                        if not has_sensor_evidence:
                            single_check = False
                            for sfi in sensor_files:
                                single_anomalies = _check_sensor_single_freezer_overtemp(sfi)
                                if single_anomalies:
                                    single_check = True
                                    break
                            if not single_check:
                                results.append(ExceptionItem(
                                    severity=Severity.REVIEW,
                                    rule_id="R009",
                                    description=f"门店 {store_id} 日期 {date} 异常登记表记录冰柜报警，但传感器无对应异常",
                                    related_files=[str(efi.path)] + [str(sfi.path) for sfi in sensor_files],
                                    suggestion="核实冰柜报警是否真实，检查传感器是否正常",
                                    store_id=store_id,
                                    date=date,
                                    file_path=str(efi.path),
                                ))
            except Exception:
                pass
    return results


def _check_sensor_single_freezer_overtemp(fi: FileInfo) -> List[str]:
    anomalies = []
    try:
        with open(fi.path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                dev = row.get("device_id", "")
                ft_str = row.get("freezer_temp", "")
                if "FREEZER" in dev and ft_str:
                    try:
                        ft = float(ft_str)
                        if ft > THRESHOLDS["FREEZER"]["temp_max"]:
                            anomalies.append(f"{dev} 温度={ft}")
                    except ValueError:
                        pass
    except Exception:
        pass
    return anomalies


def rule_device_id_conflict(
    device_stores: Dict[str, Set[str]],
) -> List[ExceptionItem]:
    results = []
    for dev, stores in device_stores.items():
        if len(stores) > 1:
            results.append(ExceptionItem(
                severity=Severity.BLOCKING,
                rule_id="R010",
                description=f"设备编号冲突: {dev} 出现在多家门店: {', '.join(sorted(stores))}",
                related_files=[],
                suggestion="核实设备编号归属，修正错误记录",
                store_id=None,
                date=None,
            ))
    return results


def rule_duplicate_content(files: List[FileInfo]) -> List[ExceptionItem]:
    results = []
    hash_map: Dict[str, List[FileInfo]] = defaultdict(list)
    for fi in files:
        if fi.content_hash:
            hash_map[fi.content_hash].append(fi)

    for chash, fis in hash_map.items():
        if len(fis) > 1:
            names = [fi.original_filename for fi in fis]
            results.append(ExceptionItem(
                severity=Severity.REMINDER,
                rule_id="R011",
                description=f"内容重复文件(哈希={chash[:12]}...): {', '.join(names)}",
                related_files=[str(fi.path) for fi in fis],
                suggestion="重复文件已自动去重，仅保留一份",
            ))
    return results
