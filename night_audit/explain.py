from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, List, Optional

from .models import ExceptionItem, Severity, FileInfo, MaterialType, REQUIRED_MATERIALS, MATERIAL_LABELS
from .rules import (
    run_all_rules,
    rule_missing_store,
    rule_missing_date,
    rule_required_materials,
    rule_sensor_thresholds,
    rule_freezer_consecutive_overtemp,
    rule_access_after_close,
    rule_meter_anomaly,
    rule_photo_sensor_time_consistency,
    rule_exception_register_correlation,
    rule_device_id_conflict,
    rule_duplicate_content,
)
from .scanner import scan_directory


RULE_REGISTRY = {
    "R001": ("缺少门店编号", rule_missing_store),
    "R002": ("缺少日期", rule_missing_date),
    "R003": ("缺少必交材料", rule_required_materials),
    "R004": ("传感器超阈值", rule_sensor_thresholds),
    "R005": ("冰柜连续超温", rule_freezer_consecutive_overtemp),
    "R006": ("闭店后门禁异常", rule_access_after_close),
    "R007": ("电表读数异常", rule_meter_anomaly),
    "R008": ("照片传感器时间不一致", rule_photo_sensor_time_consistency),
    "R009": ("异常登记表与传感器不一致", rule_exception_register_correlation),
    "R010": ("设备编号冲突", rule_device_id_conflict),
    "R011": ("内容重复文件", rule_duplicate_content),
}

SEVERITY_EXPLANATIONS = {
    Severity.BLOCKING: "阻塞级: 必须处理才能完成交接",
    Severity.REVIEW: "复核级: 需要人工确认但不阻塞交接",
    Severity.REMINDER: "提醒级: 仅供参考，不影响交接",
}


def explain_file(file_path: str, input_dir: str, cutoff_hour: int = 6) -> Dict:
    path = Path(file_path)
    if not path.exists():
        path = Path(input_dir) / file_path

    files = scan_directory(input_dir, cutoff_hour=cutoff_hour)
    target = None
    for fi in files:
        if str(fi.path) == str(path) or fi.original_filename == Path(file_path).name:
            target = fi
            break

    if target is None:
        return {"error": f"未找到文件: {file_path}"}

    explanations = []

    if target.store_id is None:
        if target.inferred_store:
            explanations.append({
                "rule_id": "R001",
                "rule_name": "缺少门店编号",
                "reason": "文件名中未包含门店编号，但已从文件内容推断",
                "inferred": target.inferred_store,
                "severity": "review",
                "action": "确认推断的门店编号是否正确",
            })
        else:
            explanations.append({
                "rule_id": "R001",
                "rule_name": "缺少门店编号",
                "reason": "文件名和内容中均未找到门店编号",
                "severity": "blocking",
                "action": "手动补充门店编号后重新扫描",
            })

    if target.date is None:
        if target.inferred_date:
            explanations.append({
                "rule_id": "R002",
                "rule_name": "缺少日期",
                "reason": "文件名中未包含日期，但已从文件内容推断",
                "inferred": target.inferred_date,
                "severity": "review",
                "action": "确认推断的日期是否正确",
            })
        else:
            explanations.append({
                "rule_id": "R002",
                "rule_name": "缺少日期",
                "reason": "文件名和内容中均未找到日期",
                "severity": "blocking",
                "action": "手动补充日期后重新扫描",
            })

    all_exceptions = run_all_rules(files, cutoff_hour=cutoff_hour)
    for exc in all_exceptions:
        if str(target.path) in exc.related_files or exc.file_path == str(target.path):
            rule_name = RULE_REGISTRY.get(exc.rule_id, (exc.rule_id, None))[0]
            explanations.append({
                "rule_id": exc.rule_id,
                "rule_name": rule_name,
                "reason": exc.description,
                "severity": exc.severity.value,
                "action": exc.suggestion,
                "related_files": exc.related_files,
            })

    return {
        "file": str(target.path),
        "original_filename": target.original_filename,
        "store_id": target.store_id,
        "date": target.date,
        "material_type": target.material_type.value,
        "content_hash": target.content_hash[:16] + "..." if target.content_hash else None,
        "explanations": explanations,
    }


def explain_exception(
    rule_id: str, input_dir: str, cutoff_hour: int = 6
) -> Dict:
    files = scan_directory(input_dir, cutoff_hour=cutoff_hour)
    all_exceptions = run_all_rules(files, cutoff_hour=cutoff_hour)

    matched = [e for e in all_exceptions if e.rule_id == rule_id]
    if not matched:
        return {"error": f"未找到规则 {rule_id} 的异常记录"}

    rule_name = RULE_REGISTRY.get(rule_id, (rule_id, None))[0]
    results = []
    for exc in matched:
        results.append({
            "rule_id": exc.rule_id,
            "rule_name": rule_name,
            "severity": exc.severity.value,
            "severity_explanation": SEVERITY_EXPLANATIONS.get(exc.severity, ""),
            "description": exc.description,
            "related_files": exc.related_files,
            "suggestion": exc.suggestion,
        })

    return {
        "rule_id": rule_id,
        "rule_name": rule_name,
        "count": len(results),
        "exceptions": results,
    }
