from __future__ import annotations
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from .models import (
    ExceptionItem,
    FileInfo,
    MaterialType,
    REQUIRED_MATERIALS,
    MATERIAL_LABELS,
    Severity,
)
from .rules import run_all_rules
from .scanner import scan_directory


def generate_package(
    input_dir: str,
    output_dir: str,
    cutoff_hour: int = 6,
) -> Dict:
    import shutil

    pkg_dir = Path(output_dir) / "handover_package"
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    organized_dir = Path(output_dir)
    files = scan_directory(input_dir, cutoff_hour=cutoff_hour)
    exceptions = run_all_rules(files, cutoff_hour=cutoff_hour)

    grouped = _group_by_store_date(files)

    _copy_organized_materials(organized_dir, pkg_dir)
    _generate_summary_csv(grouped, exceptions, pkg_dir)
    _generate_exceptions_json(exceptions, pkg_dir)
    _copy_manifest(organized_dir, pkg_dir)
    _generate_report(grouped, exceptions, pkg_dir)

    print(f"交接包已生成: {pkg_dir}")
    return {"package_dir": str(pkg_dir), "stores": len(grouped)}


def _group_by_store_date(files: List[FileInfo]) -> Dict:
    groups: Dict = defaultdict(lambda: defaultdict(list))
    for fi in files:
        if fi.store_id and fi.date:
            groups[fi.store_id][fi.date].append(fi)
    return groups


def _copy_organized_materials(organized_dir: Path, pkg_dir: Path):
    import shutil

    manifest_path = organized_dir / "manifest.json"
    if not manifest_path.exists():
        print("  跳过材料复制: 未找到整理输出，请先运行 organize")
        return

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    organized_dir = organized_dir.resolve()
    count = 0
    for entry in manifest.get("entries", []):
        src = Path(entry["target_path"]).resolve()
        if not src.exists():
            continue
        try:
            rel = src.relative_to(organized_dir)
        except ValueError:
            continue
        dst = pkg_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        count += 1

    print(f"  复制 {count} 个材料文件到交接包")


def _generate_summary_csv(
    grouped: Dict, exceptions: List[ExceptionItem], pkg_dir: Path
):
    exc_by_store_date: Dict = defaultdict(list)
    for e in exceptions:
        key = (e.store_id or "未知", e.date or "未知")
        exc_by_store_date[key].append(e)

    rows = []
    for store_id in sorted(grouped.keys()):
        for date in sorted(grouped[store_id].keys()):
            file_list = grouped[store_id][date]
            present = {fi.material_type for fi in file_list}
            missing = [MATERIAL_LABELS.get(mt, mt.value) for mt in REQUIRED_MATERIALS if mt not in present]
            store_exc = exc_by_store_date.get((store_id, date), [])
            blocking = len([e for e in store_exc if e.severity == Severity.BLOCKING])
            review = len([e for e in store_exc if e.severity == Severity.REVIEW])
            reminder = len([e for e in store_exc if e.severity == Severity.REMINDER])
            max_sev = "无"
            if blocking:
                max_sev = "阻塞"
            elif review:
                max_sev = "需复核"
            elif reminder:
                max_sev = "提醒"

            rows.append({
                "门店": store_id,
                "日期": date,
                "文件数": len(file_list),
                "材料完成度": f"{len(present & set(REQUIRED_MATERIALS))}/{len(REQUIRED_MATERIALS)}",
                "缺失材料": "、".join(missing) if missing else "无",
                "阻塞异常": blocking,
                "复核异常": review,
                "提醒异常": reminder,
                "最高风险": max_sev,
            })

    out_path = pkg_dir / "summary.csv"
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
    print(f"  生成 summary.csv")


def _generate_exceptions_json(exceptions: List[ExceptionItem], pkg_dir: Path):
    data = []
    for e in exceptions:
        data.append({
            "severity": e.severity.value,
            "rule_id": e.rule_id,
            "description": e.description,
            "related_files": e.related_files,
            "suggestion": e.suggestion,
            "store_id": e.store_id,
            "date": e.date,
        })

    out_path = pkg_dir / "exceptions.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  生成 exceptions.json")


def _copy_manifest(organized_dir: Path, pkg_dir: Path):
    src = organized_dir / "manifest.json"
    if src.exists():
        import shutil
        shutil.copy2(str(src), str(pkg_dir / "manifest.json"))
        print(f"  复制 manifest.json")


def _generate_report(
    grouped: Dict, exceptions: List[ExceptionItem], pkg_dir: Path
):
    lines = ["# 夜间巡检报告\n"]
    lines.append(f"生成时间: {_now_str()}\n")

    exc_by_store_date: Dict = defaultdict(list)
    for e in exceptions:
        if e.store_id and e.date:
            exc_by_store_date[(e.store_id, e.date)].append(e)

    total_stores = len(grouped)
    total_dates = sum(len(dates) for dates in grouped.values())
    total_exc = len(exceptions)
    blocking = len([e for e in exceptions if e.severity == Severity.BLOCKING])
    review = len([e for e in exceptions if e.severity == Severity.REVIEW])
    reminder = len([e for e in exceptions if e.severity == Severity.REMINDER])

    lines.append("## 总览\n")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"| --- | --- |")
    lines.append(f"| 门店数 | {total_stores} |")
    lines.append(f"| 日期数 | {total_dates} |")
    lines.append(f"| 异常总数 | {total_exc} |")
    lines.append(f"| 阻塞 | {blocking} |")
    lines.append(f"| 需复核 | {review} |")
    lines.append(f"| 提醒 | {reminder} |")
    lines.append("")

    for store_id in sorted(grouped.keys()):
        lines.append(f"## 门店 {store_id}\n")
        for date in sorted(grouped[store_id].keys()):
            file_list = grouped[store_id][date]
            present = {fi.material_type for fi in file_list}
            missing = [MATERIAL_LABELS.get(mt, mt.value) for mt in REQUIRED_MATERIALS if mt not in present]
            completed = len(present & set(REQUIRED_MATERIALS))
            total_required = len(REQUIRED_MATERIALS)
            pct = int(completed / total_required * 100) if total_required else 0

            store_exc = exc_by_store_date.get((store_id, date), [])
            blocking_count = len([e for e in store_exc if e.severity == Severity.BLOCKING])
            max_risk = "无"
            if blocking_count:
                max_risk = "阻塞"
            elif any(e.severity == Severity.REVIEW for e in store_exc):
                max_risk = "需复核"
            elif any(e.severity == Severity.REMINDER for e in store_exc):
                max_risk = "提醒"

            lines.append(f"### {date}\n")
            lines.append(f"- **完成度**: {completed}/{total_required} ({pct}%)")
            lines.append(f"- **异常数量**: {len(store_exc)}")
            lines.append(f"- **最高风险项**: {max_risk}")
            if missing:
                lines.append(f"- **缺失材料**: {', '.join(missing)}")
            lines.append("")

            sensor_files = [fi for fi in file_list if fi.material_type == MaterialType.SENSOR_CSV]
            overtemp_segments = _extract_overtemp_segments(sensor_files)
            if overtemp_segments:
                lines.append("**传感器超阈值片段**:\n")
                for seg in overtemp_segments:
                    lines.append(f"- {seg}")
                lines.append("")

            if store_exc:
                lines.append("**异常列表**:\n")
                for e in store_exc:
                    icon = {"blocking": "[X]", "review": "[!]", "reminder": "[i]"}.get(e.severity.value, "[?]")
                    lines.append(f"- {icon} [{e.rule_id}] {e.description}")
                    if e.suggestion:
                        lines.append(f"  - 建议: {e.suggestion}")
                lines.append("")

    out_path = pkg_dir / "report.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  生成 report.md")


def _extract_overtemp_segments(sensor_files: List[FileInfo]) -> List[str]:
    import csv as csv_mod
    from .rules import THRESHOLDS
    segments = []
    for fi in sensor_files:
        try:
            with open(fi.path, "r", encoding="utf-8-sig") as f:
                reader = csv_mod.DictReader(f)
                for row in reader:
                    dev = row.get("device_id", "")
                    ts = row.get("timestamp", "")
                    if "FREEZER" in dev:
                        ft = row.get("freezer_temp", "")
                        if ft:
                            try:
                                val = float(ft)
                                if val > THRESHOLDS["FREEZER"]["temp_max"]:
                                    segments.append(f"{ts} {dev} 冰柜温度={val}°C (阈值<={THRESHOLDS['FREEZER']['temp_max']}°C)")
                            except ValueError:
                                pass
                    elif "METER" not in dev:
                        temp = row.get("temperature", "")
                        hum = row.get("humidity", "")
                        if temp:
                            try:
                                t = float(temp)
                                if t > THRESHOLDS["default"]["temp_max"] or t < THRESHOLDS["default"]["temp_min"]:
                                    segments.append(f"{ts} {dev} 温度={t}°C (阈值{THRESHOLDS['default']['temp_min']}-{THRESHOLDS['default']['temp_max']}°C)")
                            except ValueError:
                                pass
                        if hum:
                            try:
                                h = float(hum)
                                if h > THRESHOLDS["default"]["humidity_max"] or h < THRESHOLDS["default"]["humidity_min"]:
                                    segments.append(f"{ts} {dev} 湿度={h}% (阈值{THRESHOLDS['default']['humidity_min']}-{THRESHOLDS['default']['humidity_max']}%)")
                            except ValueError:
                                pass
        except Exception:
            pass
    return segments


def _now_str():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
