from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, List, Optional


def rollback(output_dir: str) -> Dict:
    out = Path(output_dir).resolve()
    manifest_path = out / "manifest.json"

    if not manifest_path.exists():
        return {"status": "no_manifest", "message": "未找到清单文件，无法回滚"}

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    entries = manifest.get("entries", [])
    timestamp = manifest.get("timestamp", "未知")

    if not entries:
        return {"status": "empty_manifest", "message": "清单为空，无需回滚"}

    removed_files: List[str] = []
    removed_dirs: List[str] = []
    errors: List[str] = []

    existing_files_in_output = set()
    for p in out.rglob("*"):
        if p.is_file() and p.name not in (
            "manifest.json",
            "name_mapping.json",
            "exceptions.json",
            "summary.csv",
            "report.md",
        ):
            existing_files_in_output.add(str(p))

    target_paths_in_manifest = {e["target_path"] for e in entries}

    for entry in entries:
        target = Path(entry["target_path"])
        if not target.exists():
            continue

        in_manifest = str(target) in target_paths_in_manifest
        is_metadata_file = target.name in (
            "manifest.json",
            "name_mapping.json",
            "exceptions.json",
            "summary.csv",
            "report.md",
        )

        if is_metadata_file:
            continue

        if not in_manifest:
            continue

        try:
            target.unlink()
            removed_files.append(str(target))
        except OSError as e:
            errors.append(f"删除文件失败 {target}: {e}")

    for entry in entries:
        target = Path(entry["target_path"])
        parent = target.parent
        while parent != out and parent.exists():
            try:
                if not any(parent.iterdir()):
                    parent.rmdir()
                    removed_dirs.append(str(parent))
                else:
                    break
            except OSError:
                break
            parent = parent.parent

    for meta_name in ("manifest.json", "name_mapping.json", "exceptions.json"):
        meta_path = out / meta_name
        if meta_path.exists():
            try:
                meta_path.unlink()
            except OSError:
                pass

    pkg_dir = out / "handover_package"
    if pkg_dir.exists():
        import shutil
        shutil.rmtree(str(pkg_dir))

    return {
        "status": "success",
        "timestamp": timestamp,
        "removed_files": len(removed_files),
        "removed_dirs": len(removed_dirs),
        "errors": errors,
    }
