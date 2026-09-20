"""Explicit language output contract shared by report tracks."""

import json
from pathlib import Path


def generated_languages(root: Path) -> tuple[str, ...]:
    path = root / "report-languages.json"
    if not path.exists():
        return ("zh", "en")
    value = json.loads(path.read_text(encoding="utf-8"))
    languages = value.get("languages")
    if languages not in (["zh"], ["zh", "en"]):
        raise ValueError("unsupported generated report languages")
    return tuple(languages)


def write_language_contract(root: Path, *, include_english: bool) -> None:
    (root / "report-languages.json").write_text(
        json.dumps({"languages": ["zh", "en"] if include_english else ["zh"],
                    "english_status": "generated" if include_english else "not_generated"}) + "\n",
        encoding="utf-8",
    )
    if include_english:
        return
    folder = root / "en"
    folder.mkdir(exist_ok=True)
    if any(folder.iterdir()):
        raise ValueError("English output must not be generated in Chinese-only mode")
    (folder / "index.html").write_text(
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<script>(function(){const p=location.pathname.split("/");'
        'const i=p.indexOf("preview");if(i<1||p[i-1]!=="api")return;'
        'const b=document.querySelector("base")||document.createElement("base");'
        'b.href=location.origin+location.pathname;'
        'if(!b.isConnected)document.head.prepend(b);})();</script>'
        '<title>英文报告未生成</title></head><body><main data-english-not-generated>'
        '<h1>英文报告未生成</h1><p>English report has not been generated.</p>'
        '<p>当前报告仅提供中文版。英文版需单独生成。</p>'
        '<a href="../index.html">返回中文报告</a></main></body></html>',
        encoding="utf-8",
    )


def check_language_contract(root: Path) -> None:
    if "en" in generated_languages(root):
        return
    files = {p.relative_to(root / "en").as_posix() for p in (root / "en").rglob("*") if p.is_file()}
    if files != {"index.html"}:
        raise ValueError("Chinese-only report contains stale English output")
    notice = (root / "en/index.html").read_text(encoding="utf-8")
    if "data-english-not-generated" not in notice or 'href="../index.html"' not in notice:
        raise ValueError("English not-generated notice is missing")
