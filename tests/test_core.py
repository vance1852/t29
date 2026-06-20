from __future__ import annotations
import csv
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List

import pytest

from night_audit.models import FileInfo, MaterialType, Severity
from night_audit.utils import (
    adjust_date_for_night_shift,
    classify_material,
    file_hash,
    parse_date_from_filename,
    parse_store_id,
    stable_filename,
)
from night_audit.scanner import scan_directory
from night_audit.rules import (
    run_all_rules,
    rule_missing_store,
    rule_required_materials,
    rule_freezer_consecutive_overtemp,
    rule_access_after_close,
    rule_device_id_conflict,
    rule_duplicate_content,
    rule_exception_register_correlation,
    rule_meter_anomaly,
)
from night_audit.organizer import organize
from night_audit.rollback import rollback
from night_audit.sample_data import generate


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def sample_input(tmp_path):
    d = str(tmp_path / "sample_input")
    generate(d)
    return d


def _write_csv(path, rows, header):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def _write_txt(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class TestCrossDayAttribution:
    def test_before_cutoff_belongs_to_previous_day(self):
        dt = datetime(2026, 6, 16, 3, 30)
        result = adjust_date_for_night_shift(dt, cutoff_hour=6)
        assert result == "2026-06-15"

    def test_after_cutoff_belongs_to_current_day(self):
        dt = datetime(2026, 6, 16, 6, 0)
        result = adjust_date_for_night_shift(dt, cutoff_hour=6)
        assert result == "2026-06-16"

    def test_midnight_belongs_to_previous_day(self):
        dt = datetime(2026, 6, 16, 0, 0)
        result = adjust_date_for_night_shift(dt, cutoff_hour=6)
        assert result == "2026-06-15"

    def test_custom_cutoff_hour(self):
        dt = datetime(2026, 6, 16, 4, 0)
        result = adjust_date_for_night_shift(dt, cutoff_hour=5)
        assert result == "2026-06-15"

    def test_after_custom_cutoff(self):
        dt = datetime(2026, 6, 16, 5, 0)
        result = adjust_date_for_night_shift(dt, cutoff_hour=5)
        assert result == "2026-06-16"

    def test_just_before_custom_cutoff(self):
        dt = datetime(2026, 6, 16, 4, 59)
        result = adjust_date_for_night_shift(dt, cutoff_hour=5)
        assert result == "2026-06-15"

    def test_filename_with_early_morning_time(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        _write_txt(
            base / "S001_2026-06-16_0230_access.txt",
            ["门禁日志 门店:S001 日期:2026-06-16", "2026-06-16 02:30:00 | S001 | 正常进入 | 员工001"],
        )
        files = scan_directory(str(base), cutoff_hour=6)
        assert len(files) == 1
        assert files[0].date == "2026-06-15"


class TestHashDedup:
    def test_same_content_different_name(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        content = "identical content here"
        (base / "S001_2026-06-15_duty.txt").write_text(content, encoding="utf-8")
        (base / "S001_2026-06-15_duty_note.txt").write_text(content, encoding="utf-8")

        files = scan_directory(str(base))
        hashes = [f.content_hash for f in files]
        assert hashes[0] == hashes[1]

        dup_exc = rule_duplicate_content(files)
        assert len(dup_exc) == 1
        assert dup_exc[0].rule_id == "R011"

    def test_different_content_not_deduped(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        (base / "S001_2026-06-15_duty.txt").write_text("content A", encoding="utf-8")
        (base / "S002_2026-06-15_duty.txt").write_text("content B", encoding="utf-8")

        files = scan_directory(str(base))
        dup_exc = rule_duplicate_content(files)
        assert len(dup_exc) == 0

    def test_organizer_dedupes_manifest(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        out = tmp_path / "output"
        content = "identical"
        (base / "S001_2026-06-15_duty.txt").write_text(content, encoding="utf-8")
        (base / "S001_2026-06-15_note.txt").write_text(content, encoding="utf-8")

        result = organize(str(base), str(out), force=True)
        assert result["status"] == "success"
        assert result["files_organized"] == 1


class TestRequiredMaterialsMissing:
    def test_missing_door_photo(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        _write_txt(base / "S001_2026-06-15_counter.jpg", ["placeholder"])
        _write_txt(base / "S001_2026-06-15_warehouse.jpg", ["placeholder"])

        files = scan_directory(str(base))
        exc = rule_required_materials(
            {(f.store_id, f.date): [f] for f in files if f.store_id and f.date}
        )
        door_missing = [e for e in exc if "door_photo" in e.description]
        assert len(door_missing) > 0
        assert door_missing[0].severity == Severity.BLOCKING

    def test_all_required_present_no_exception(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        (base / "S001_2026-06-15_door.jpg").touch()
        (base / "S001_2026-06-15_counter.jpg").touch()
        (base / "S001_2026-06-15_warehouse.png").touch()
        _write_csv(
            base / "S001_2026-06-15_sensor.csv",
            [["2026-06-15 22:00:00", "S001", "DEV-A01", "22.0", "50.0", "", ""]],
            ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"],
        )
        _write_txt(base / "S001_2026-06-15_access.txt", ["门禁日志 门店:S001 日期:2026-06-15"])
        _write_txt(base / "S001_2026-06-15_duty.txt", ["值班备注 门店:S001 日期:2026-06-15"])

        files = scan_directory(str(base))
        grouped = {}
        for f in files:
            if f.store_id and f.date:
                grouped.setdefault((f.store_id, f.date), []).append(f)
        exc = rule_required_materials(grouped)
        assert len(exc) == 0


class TestFreezerConsecutiveOvertemp:
    def test_two_consecutive_overtemp_triggers(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        rows = [
            ["2026-06-15 22:00:00", "S001", "FREEZER-A01", "", "", "", "-18.0"],
            ["2026-06-15 22:30:00", "S001", "FREEZER-A01", "", "", "", "-2.0"],
            ["2026-06-15 23:00:00", "S001", "FREEZER-A01", "", "", "", "-1.0"],
        ]
        csv_path = base / "S001_2026-06-15_sensor.csv"
        _write_csv(csv_path, rows, ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"])

        files = scan_directory(str(base))
        fi = FileInfo(
            path=csv_path, original_filename="S001_2026-06-15_sensor.csv",
            store_id="S001", date="2026-06-15", material_type=MaterialType.SENSOR_CSV,
        )
        exc = rule_freezer_consecutive_overtemp("S001", "2026-06-15", [fi])
        assert len(exc) == 1
        assert exc[0].rule_id == "R005"
        assert exc[0].severity == Severity.BLOCKING

    def test_single_overtemp_no_trigger(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        rows = [
            ["2026-06-15 22:00:00", "S001", "FREEZER-A01", "", "", "", "-18.0"],
            ["2026-06-15 22:30:00", "S001", "FREEZER-A01", "", "", "", "-2.0"],
            ["2026-06-15 23:00:00", "S001", "FREEZER-A01", "", "", "", "-20.0"],
        ]
        csv_path = base / "S001_2026-06-15_sensor.csv"
        _write_csv(csv_path, rows, ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"])

        fi = FileInfo(
            path=csv_path, original_filename="S001_2026-06-15_sensor.csv",
            store_id="S001", date="2026-06-15", material_type=MaterialType.SENSOR_CSV,
        )
        exc = rule_freezer_consecutive_overtemp("S001", "2026-06-15", [fi])
        assert len(exc) == 0

    def test_non_consecutive_overtemp_no_trigger(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        rows = [
            ["2026-06-15 22:00:00", "S001", "FREEZER-A01", "", "", "", "-2.0"],
            ["2026-06-15 22:30:00", "S001", "FREEZER-A01", "", "", "", "-18.0"],
            ["2026-06-15 23:00:00", "S001", "FREEZER-A01", "", "", "", "-2.0"],
        ]
        csv_path = base / "S001_2026-06-15_sensor.csv"
        _write_csv(csv_path, rows, ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"])

        fi = FileInfo(
            path=csv_path, original_filename="S001_2026-06-15_sensor.csv",
            store_id="S001", date="2026-06-15", material_type=MaterialType.SENSOR_CSV,
        )
        exc = rule_freezer_consecutive_overtemp("S001", "2026-06-15", [fi])
        assert len(exc) == 0


class TestAccessAnomalyCorrelation:
    def test_after_close_with_exception_entry(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        access_path = base / "S001_2026-06-15_access.txt"
        _write_txt(access_path, [
            "门禁日志 门店:S001 日期:2026-06-15",
            "2026-06-16 02:00:00 | S001 | 异常开门 | 未知",
        ])
        exc_path = base / "S001_2026-06-15_exception.json"
        _write_json(exc_path, [
            {"store_id": "S001", "date": "2026-06-15", "type": "门禁异常", "description": "闭店后开门"},
        ])

        files = scan_directory(str(base))
        afi = [f for f in files if f.material_type == MaterialType.ACCESS_LOG][0]
        efi = [f for f in files if f.material_type == MaterialType.EXCEPTION_REGISTER]

        exc = rule_access_after_close("S001", "2026-06-15", [afi] + efi)
        assert len(exc) == 1
        assert exc[0].severity == Severity.REVIEW

    def test_after_close_without_exception_entry(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        access_path = base / "S001_2026-06-15_access.txt"
        _write_txt(access_path, [
            "门禁日志 门店:S001 日期:2026-06-15",
            "2026-06-16 02:00:00 | S001 | 异常开门 | 未知",
        ])

        fi = FileInfo(
            path=access_path, original_filename="S001_2026-06-15_access.txt",
            store_id="S001", date="2026-06-15", material_type=MaterialType.ACCESS_LOG,
        )
        exc = rule_access_after_close("S001", "2026-06-15", [fi])
        assert len(exc) == 1
        assert exc[0].severity == Severity.BLOCKING


class TestDeviceIdConflict:
    def test_same_device_in_two_stores(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()

        rows1 = [["2026-06-15 22:00:00", "S001", "DEV-CONFLICT", "22.0", "50.0", "", ""]]
        rows2 = [["2026-06-15 22:00:00", "S002", "DEV-CONFLICT", "23.0", "55.0", "", ""]]

        _write_csv(base / "S001_2026-06-15_sensor.csv", rows1,
                    ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"])
        _write_csv(base / "S002_2026-06-15_sensor.csv", rows2,
                    ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"])

        files = scan_directory(str(base))
        device_map = {}
        for fi in files:
            if fi.material_type == MaterialType.SENSOR_CSV:
                with open(fi.path, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        dev = row.get("device_id", "")
                        sid = row.get("store_id", fi.store_id or "")
                        device_map.setdefault(dev, set()).add(sid)

        exc = rule_device_id_conflict(device_map)
        conflict = [e for e in exc if "DEV-CONFLICT" in e.description]
        assert len(conflict) == 1
        assert conflict[0].rule_id == "R010"
        assert conflict[0].severity == Severity.BLOCKING


class TestManifestRollbackSafety:
    def test_rollback_removes_organized_files(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        (base / "S001_2026-06-15_door.jpg").touch()
        (base / "S001_2026-06-15_counter.jpg").touch()
        (base / "S001_2026-06-15_warehouse.png").touch()
        _write_txt(base / "S001_2026-06-15_access.txt", ["门禁日志 门店:S001 日期:2026-06-15"])
        _write_txt(base / "S001_2026-06-15_duty.txt", ["值班备注 门店:S001 日期:2026-06-15"])
        _write_csv(
            base / "S001_2026-06-15_sensor.csv",
            [["2026-06-15 22:00:00", "S001", "DEV-A01", "22.0", "50.0", "", ""]],
            ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"],
        )

        out = tmp_path / "output"
        result = organize(str(base), str(out), force=True)
        assert result["status"] == "success"
        assert result["files_organized"] > 0

        import json as json_mod
        manifest = json_mod.loads((out / "manifest.json").read_text(encoding="utf-8"))
        for entry in manifest["entries"]:
            assert Path(entry["target_path"]).exists()

        rb = rollback(str(out))
        assert rb["status"] == "success"

        for entry in manifest["entries"]:
            assert not Path(entry["target_path"]).exists()

    def test_rollback_preserves_manual_files(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        (base / "S001_2026-06-15_door.jpg").touch()
        _write_txt(base / "S001_2026-06-15_access.txt", ["门禁日志 门店:S001 日期:2026-06-15"])
        _write_txt(base / "S001_2026-06-15_duty.txt", ["值班备注 门店:S001 日期:2026-06-15"])
        (base / "S001_2026-06-15_counter.jpg").touch()
        (base / "S001_2026-06-15_warehouse.png").touch()
        _write_csv(
            base / "S001_2026-06-15_sensor.csv",
            [["2026-06-15 22:00:00", "S001", "DEV-A01", "22.0", "50.0", "", ""]],
            ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"],
        )

        out = tmp_path / "output"
        organize(str(base), str(out), force=True)

        manual_dir = out / "S001" / "2026-06-15" / "manual"
        manual_dir.mkdir(parents=True, exist_ok=True)
        (manual_dir / "hand_added.txt").write_text("manual content", encoding="utf-8")

        rb = rollback(str(out))
        assert rb["status"] == "success"
        assert (manual_dir / "hand_added.txt").exists()


class TestExceptionRegisterCorrelation:
    def test_freezer_alarm_without_sensor_evidence(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()

        rows = [
            ["2026-06-15 22:00:00", "S001", "FREEZER-A01", "", "", "", "-18.0"],
            ["2026-06-15 22:30:00", "S001", "FREEZER-A01", "", "", "", "-19.0"],
            ["2026-06-15 23:00:00", "S001", "FREEZER-A01", "", "", "", "-20.0"],
        ]
        sensor_path = base / "S001_2026-06-15_sensor.csv"
        _write_csv(sensor_path, rows,
                    ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"])

        exc_path = base / "S001_2026-06-15_exception.json"
        _write_json(exc_path, [
            {"store_id": "S001", "date": "2026-06-15", "type": "冰柜报警", "description": "冰柜温度异常", "device_id": "FREEZER-A01"},
        ])

        fi_sensor = FileInfo(
            path=sensor_path, original_filename="S001_2026-06-15_sensor.csv",
            store_id="S001", date="2026-06-15", material_type=MaterialType.SENSOR_CSV,
        )
        fi_exc = FileInfo(
            path=exc_path, original_filename="S001_2026-06-15_exception.json",
            store_id="S001", date="2026-06-15", material_type=MaterialType.EXCEPTION_REGISTER,
        )

        grouped = {("S001", "2026-06-15"): [fi_sensor, fi_exc]}
        exc = rule_exception_register_correlation(grouped)
        assert len(exc) == 1
        assert exc[0].rule_id == "R009"

    def test_freezer_alarm_with_sensor_evidence(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()

        rows = [
            ["2026-06-15 22:00:00", "S001", "FREEZER-A01", "", "", "", "-2.0"],
            ["2026-06-15 22:30:00", "S001", "FREEZER-A01", "", "", "", "-1.0"],
        ]
        sensor_path = base / "S001_2026-06-15_sensor.csv"
        _write_csv(sensor_path, rows,
                    ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"])

        exc_path = base / "S001_2026-06-15_exception.json"
        _write_json(exc_path, [
            {"store_id": "S001", "date": "2026-06-15", "type": "冰柜报警", "description": "冰柜温度异常"},
        ])

        fi_sensor = FileInfo(
            path=sensor_path, original_filename="S001_2026-06-15_sensor.csv",
            store_id="S001", date="2026-06-15", material_type=MaterialType.SENSOR_CSV,
        )
        fi_exc = FileInfo(
            path=exc_path, original_filename="S001_2026-06-15_exception.json",
            store_id="S001", date="2026-06-15", material_type=MaterialType.EXCEPTION_REGISTER,
        )

        grouped = {("S001", "2026-06-15"): [fi_sensor, fi_exc]}
        exc = rule_exception_register_correlation(grouped)
        assert len(exc) == 0


class TestMeterAnomaly:
    def test_meter_regression(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        rows = [
            ["2026-06-15 22:00:00", "S001", "METER-A01", "22.0", "50.0", "1500.0", ""],
            ["2026-06-15 22:30:00", "S001", "METER-A01", "22.0", "50.0", "500.0", ""],
        ]
        csv_path = base / "S001_2026-06-15_sensor.csv"
        _write_csv(csv_path, rows,
                    ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"])

        fi = FileInfo(
            path=csv_path, original_filename="S001_2026-06-15_sensor.csv",
            store_id="S001", date="2026-06-15", material_type=MaterialType.SENSOR_CSV,
        )
        exc = rule_meter_anomaly("S001", "2026-06-15", [fi])
        assert len(exc) == 1
        assert "倒退" in exc[0].description

    def test_meter_spike(self, tmp_path):
        base = tmp_path / "input"
        base.mkdir()
        rows = [
            ["2026-06-15 22:00:00", "S001", "METER-A01", "22.0", "50.0", "1500.0", ""],
            ["2026-06-15 22:30:00", "S001", "METER-A01", "22.0", "50.0", "99999.0", ""],
        ]
        csv_path = base / "S001_2026-06-15_sensor.csv"
        _write_csv(csv_path, rows,
                    ["timestamp", "store_id", "device_id", "temperature", "humidity", "meter_reading", "freezer_temp"])

        fi = FileInfo(
            path=csv_path, original_filename="S001_2026-06-15_sensor.csv",
            store_id="S001", date="2026-06-15", material_type=MaterialType.SENSOR_CSV,
        )
        exc = rule_meter_anomaly("S001", "2026-06-15", [fi])
        assert len(exc) >= 1
        assert any("突增" in e.description for e in exc)


class TestEndToEndWorkflow:
    def test_full_workflow_with_sample_data(self, sample_input, tmp_path):
        out = tmp_path / "output"
        result = organize(sample_input, str(out), force=True)
        assert result["status"] == "success"
        assert result["files_organized"] > 0

        assert (out / "manifest.json").exists()
        assert (out / "name_mapping.json").exists()
        assert (out / "exceptions.json").exists()

        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert "entries" in manifest
        assert len(manifest["entries"]) > 0

        mapping = json.loads((out / "name_mapping.json").read_text(encoding="utf-8"))
        assert len(mapping) > 0

        for entry in manifest["entries"]:
            target = Path(entry["target_path"])
            assert target.exists(), f"Missing: {target}"

    def test_scan_sample_data(self, sample_input):
        files = scan_directory(sample_input, cutoff_hour=6)
        assert len(files) > 20

        stores = {f.store_id for f in files if f.store_id}
        assert len(stores) >= 3

    def test_exceptions_in_sample_data(self, sample_input):
        files = scan_directory(sample_input, cutoff_hour=6)
        exceptions = run_all_rules(files, cutoff_hour=6)
        assert len(exceptions) > 0

        rule_ids = {e.rule_id for e in exceptions}
        assert "R011" in rule_ids or "R003" in rule_ids or "R010" in rule_ids
