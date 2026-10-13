"""An explicitly supplied historical article, separate from current measurements."""
import hashlib
import json
import re
import shutil
from pathlib import Path

from .safety import contained_path


def copy_archived_reading(source: Path, out: Path) -> None:
    manifest = source / "archived-reading.json"
    if not manifest.is_file():
        return
    data = json.loads(manifest.read_text())
    for item in [*data["languages"].values(), *([data["translations"]] if data.get("translations") else [])]:
        origin = contained_path(source, item["path"])
        if origin.is_symlink() or hashlib.sha256(origin.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Archived article content differs from its manifest")
        target = contained_path(out, item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, target)
    shutil.copy2(manifest, out / "archived-reading.json")


def render_archived_reading(out: Path, language: str = "zh") -> str | None:
    manifest = out / "archived-reading.json"
    if not manifest.is_file():
        return None
    data = json.loads(manifest.read_text())
    item = data["languages"][language]
    raw = contained_path(out, item["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError("Archived article hash mismatch")
    text = raw.decode()
    body = re.search(r"<main[^>]*>(.*?)</main>", text, re.S)
    if not body:
        raise ValueError("Archived article has no main content")
    styles = "".join(re.findall(r"<style[^>]*>.*?</style>", text, re.S))
    date = data.get("source_data_date")
    if language == "zh":
        date_note = f"（截至{date}）" if date else ""
        note = f"本文沿用旧报告文章与原有数据{date_note}，不代表本轮评测结果。"
    else:
        date_note = f" through {date}" if date else ""
        note = f"This article retains the older report and its data{date_note}; it does not describe the current benchmark results."
    return (styles + '<div class="wrap"><p class="source-note">' + note
            + '</p></div><div class="wrap essay">' + body[1] + '</div>')


def archived_translations(out: Path) -> dict[str, str]:
    manifest = out / "archived-reading.json"
    if not manifest.is_file():
        return {}
    item = json.loads(manifest.read_text()).get("translations")
    if not item:
        return {}
    raw = contained_path(out, item["path"], reject_symlinks=True).read_bytes()
    if hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError("Archived translations hash mismatch")
    text = json.loads(raw)["text"]
    if not isinstance(text, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in text.items()):
        raise ValueError("Report translations require text pairs")
    return text
