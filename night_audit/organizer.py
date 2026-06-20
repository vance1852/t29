from __future__ import annotations
import json
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import (
    ExceptionItem,
    FileInfo,
    ManifestEntry,
    MaterialType,
    Severity,
)
from .rules import run_all_rules
from .scanner import scan_directory
from .utils import file_hash, stable_filename


MANIFEST_FILENAME = "manifest.json"
MAPPING_FILENAME = "name_mapping.json"


def organize(
    input_dir: str,
    output_dir: str,
    cutoff_hour: int = 6,
    force: bool = False,
) -> Dict:
    inp = Path(input_dir).resolve()
    out = Path(output_dir).resolve()

    if not inp.exists():
        raise FileNotFoundError(f"输入目录不存在: {inp}")

    files = scan_directory(str(inp), cutoff_hour=cutoff_hour)
    exceptions = run_all_rules(files, cutoff_hour=cutoff_hour)

    blocking = [e for e in exceptions if e.severity == Severity.BLOCKING]
    if blocking and not force:
        print(f"发现 {len(blocking)} 个阻塞级异常，使用 --force 强制执行或先处理异常")
        print("阻塞异常:")
        for e in blocking:
            print(f"  [{e.rule_id}] {e.description}")
        return {"status": "blocked", "blocking_count": len(blocking)}

    manifest_data = _build_manifest(files, out, cutoff_hour)
    deduped = _dedup_manifest(manifest_data)

    _save_manifest(deduped, out)
    _copy_files(deduped, inp, out)
    _save_mapping(deduped, out)
    _save_exceptions(exceptions, out)

    return {
        "status": "success",
        "files_organized": len(deduped["entries"]),
        "exceptions_count": len(exceptions),
        "blocking_count": len(blocking),
    }


def _build_manifest(
    files: List[FileInfo], output_dir: Path, cutoff_hour: int
) -> Dict:
    entries: List[Dict] = []
    counter: Dict[Tuple[str, str, str], int] = defaultdict(int)

    for fi in files:
        if not fi.store_id or not fi.date:
            continue

        key = (fi.store_id, fi.date, fi.material_type.value)
        counter[key] += 1
        idx = counter[key]

        ext = fi.path.suffix.lower()
        new_name = stable_filename(fi.store_id, fi.date, fi.material_type, idx, ext)
        target_rel = f"{fi.store_id}/{fi.date}/{fi.material_type.value}/{new_name}"
        target_abs = str(output_dir / target_rel)

        entries.append({
            "source_path": str(fi.path),
            "target_path": target_abs,
            "target_rel": target_rel,
            "content_hash": fi.content_hash or "",
            "store_id": fi.store_id,
            "date": fi.date,
            "material_type": fi.material_type.value,
            "original_filename": fi.original_filename,
            "new_filename": new_name,
        })

    return {"entries": entries, "timestamp": datetime.now().isoformat(), "cutoff_hour": cutoff_hour}


def _dedup_manifest(manifest: Dict) -> Dict:
    seen_hashes: Dict[str, str] = {}
    deduped_entries = []

    for entry in manifest["entries"]:
        chash = entry["content_hash"]
        if chash and chash in seen_hashes:
            continue
        if chash:
            seen_hashes[chash] = entry["target_path"]
        deduped_entries.append(entry)

    result = dict(manifest)
    result["entries"] = deduped_entries
    result["original_count"] = len(manifest["entries"])
    result["deduped_count"] = len(deduped_entries)
    return result


def _save_manifest(manifest: Dict, output_dir: Path):
    out_path = output_dir / MANIFEST_FILENAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"清单已保存: {out_path}")


def _copy_files(manifest: Dict, input_dir: Path, output_dir: Path):
    for entry in manifest["entries"]:
        src = Path(entry["source_path"])
        dst = Path(entry["target_path"])
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
    print(f"已复制 {len(manifest['entries'])} 个文件到 {output_dir}")


def _save_mapping(manifest: Dict, output_dir: Path):
    mapping = {}
    for entry in manifest["entries"]:
        mapping[entry["new_filename"]] = entry["original_filename"]

    out_path = output_dir / MAPPING_FILENAME
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    print(f"映射表已保存: {out_path}")


def _save_exceptions(exceptions: List[ExceptionItem], output_dir: Path):
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
            "file_path": e.file_path,
        })

    out_path = output_dir / "exceptions.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"异常清单已保存: {out_path}")
