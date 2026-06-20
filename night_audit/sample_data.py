from __future__ import annotations
import csv
import io
import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

STORES = ["S001", "S002", "S003"]
DATES = ["2026-06-15", "2026-06-16"]

DEVICE_IDS = {
    "S001": ["DEV-A01", "DEV-A02", "FREEZER-A01", "METER-A01"],
    "S002": ["DEV-B01", "DEV-B02", "FREEZER-B01", "METER-B01"],
    "S003": ["DEV-C01", "DEV-C02", "FREEZER-C01", "METER-C01"],
}


def _touch(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def _write_csv(path: Path, rows: list, header: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def _write_txt(path: Path, lines: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _sensor_rows(store: str, date: str, inject_freezer_alarm: bool = False):
    devices = DEVICE_IDS[store]
    rows = []
    base = datetime.strptime(date, "%Y-%m-%d") + timedelta(hours=22)
    for i in range(12):
        ts = base + timedelta(minutes=30 * i)
        for dev in devices:
            temp = round(random.uniform(18, 26), 1)
            humidity = round(random.uniform(40, 70), 1)
            meter = round(random.uniform(1000, 2000) + i * 12, 1)
            freezer_temp = round(random.uniform(-22, -16), 1)
            if "FREEZER" in dev:
                if inject_freezer_alarm and i in (5, 6):
                    freezer_temp = round(random.uniform(-4, 0), 1)
                rows.append([
                    ts.strftime("%Y-%m-%d %H:%M:%S"), store, dev,
                    "", "", round(meter, 1), freezer_temp,
                ])
            elif "METER" in dev:
                rows.append([
                    ts.strftime("%Y-%m-%d %H:%M:%S"), store, dev,
                    temp, humidity, round(meter, 1), "",
                ])
            else:
                rows.append([
                    ts.strftime("%Y-%m-%d %H:%M:%S"), store, dev,
                    temp, humidity, "", "",
                ])
    return rows


def _access_lines(store: str, date: str, inject_after_close: bool = False):
    lines = [f"门禁日志 门店:{store} 日期:{date}", "=" * 40]
    base = datetime.strptime(date, "%Y-%m-%d") + timedelta(hours=22)
    for i in range(6):
        ts = base + timedelta(minutes=30 * i)
        lines.append(f"{ts.strftime('%Y-%m-%d %H:%M:%S')} | {store} | 正常进入 | 员工{i:03d}")
    if inject_after_close:
        close_time = datetime.strptime(date, "%Y-%m-%d") + timedelta(hours=30)
        lines.append(f"{close_time.strftime('%Y-%m-%d %H:%M:%S')} | {store} | 异常开门 | 未知")
    return lines


def _exception_data(store: str, date: str, inject_freezer: bool = False, inject_door: bool = False):
    items = []
    if inject_freezer:
        items.append({
            "store_id": store,
            "date": date,
            "type": "冰柜报警",
            "description": "冰柜温度异常升高",
            "device_id": f"FREEZER-{store[1:3]}01",
            "reported_by": "值班员张三",
        })
    if inject_door:
        items.append({
            "store_id": store,
            "date": date,
            "type": "门禁异常",
            "description": "闭店后检测到开门记录",
            "device_id": "",
            "reported_by": "值班员李四",
        })
    return items


def generate(output_dir: str):
    base = Path(output_dir)
    if base.exists():
        import shutil
        shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)

    for store in STORES:
        for date in DATES:
            inject_freezer = (store == "S001" and date == "2026-06-15")
            inject_door = (store == "S002" and date == "2026-06-16")
            inject_meter_regress = (store == "S003" and date == "2026-06-15")
            inject_meter_spike = (store == "S003" and date == "2026-06-16")

            _touch(base / f"{store}_{date}_door.jpg")
            _touch(base / f"{store}_{date}_counter.jpg")
            _touch(base / f"{store}_{date}_warehouse.png")

            rows = _sensor_rows(store, date, inject_freezer_alarm=inject_freezer)
            if inject_meter_regress and len(rows) > 3:
                rows[2][5] = "500.0"
                rows[3][5] = "450.0"
            if inject_meter_spike and len(rows) > 5:
                rows[4][5] = "99999.0"
            _write_csv(
                base / f"{store}_{date}_sensor.csv",
                rows,
                ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"],
            )

            lines = _access_lines(store, date, inject_after_close=inject_door)
            _write_txt(base / f"{store}_{date}_access.txt", lines)

            _write_txt(base / f"{store}_{date}_duty.txt", [
                f"值班备注 门店:{store} 日期:{date}",
                "夜班巡检完成，一切正常。",
            ])

            exc = _exception_data(store, date, inject_freezer=inject_freezer, inject_door=inject_door)
            if exc:
                _write_json(base / f"{store}_{date}_exception.json", exc)

    _touch(base / "S001_2026-06-15_door.jpg")
    _touch(base / "store002_20260616_counter.jpg")

    _touch(base / "photo_night_2026-06-16_0100.jpg")

    _touch(base / "random_file_2026-06-15.dat")

    _write_csv(
        base / "sensor_2026-06-16.csv",
        _sensor_rows("S001", "2026-06-16"),
        ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"],
    )

    conflicting_rows = _sensor_rows("S002", "2026-06-16")
    for row in conflicting_rows:
        row[2] = "DEV-A01"
    _write_csv(
        base / "S002_2026-06-16_sensor_extra.csv",
        conflicting_rows,
        ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"],
    )

    _write_txt(base / "S001_2026-06-15_duty.txt", [
        "值班备注 门店:S001 日期:2026-06-15",
        "夜班巡检完成，一切正常。",
    ])

    print(f"样例数据已生成到 {base}")
    file_count = len(list(base.iterdir()))
    print(f"共 {file_count} 个文件")
