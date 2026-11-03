"""Bilingual research and input-compatibility reports for structured extraction."""

from __future__ import annotations

import json
import re
import struct
from html import escape as esc
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from harness.reporting.presentation import check_presentation
from harness.web_scraper.pipeline import (
    PROFILE,
    digest,
    load_run,
    read_json,
    redact,
    save_json,
    validate_dataset,
)

VERSION = "web-scraper-research/1"


def image_width(raw: bytes):
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
        return struct.unpack(">I", raw[16:20])[0]
    if raw[:2] == b"\xff\xd8":
        offset = 2
        while offset + 4 <= len(raw):
            if raw[offset] != 255:
                break
            marker = raw[offset + 1]
            if marker == 255:
                offset += 1
                continue
            offset += 2
            size = int.from_bytes(raw[offset : offset + 2], "big")
            if size < 2:
                break
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            } and offset + 7 <= len(raw):
                return int.from_bytes(raw[offset + 5 : offset + 7], "big")
            offset += size
    raise ValueError("Unsupported screenshot image header")


LABELS = {
    "en": [
        "Overview",
        "Dataset",
        "Compatibility",
        "Protocol",
        "Candidates",
        "Research",
        "Accounts",
        "Research and compatibility only · no current quality ranking",
        "Theme",
        "Evidence",
        "Close",
        "Blocked",
        "Structured output",
        "All",
        "Case",
        "Method",
        "Status",
        "HTTP",
        "Seconds",
        "Reference",
        "Output",
        "Raw evidence",
        "Historical reproduction · 140 pages",
        "Price F1",
        "SKU F1",
        "Availability F1",
        "Pending",
    ],
    "zh": [
        "概览",
        "数据集",
        "兼容性实测",
        "评测协议",
        "候选选手",
        "相关研究",
        "账号与额度",
        "调研与输入兼容验证 · 尚无当前质量排名",
        "切换主题",
        "查看证据",
        "关闭",
        "受阻",
        "返回结构化字段",
        "全部",
        "题目",
        "方法",
        "状态",
        "HTTP",
        "秒",
        "标注",
        "输出",
        "原始证据",
        "历史结果复现 · 140 页",
        "价格 F1",
        "SKU F1",
        "库存 F1",
        "待验证",
    ],
}
PAGES = ("index", "dataset", "results", "protocol", "candidates", "research", "accounts")
CSS = """*{box-sizing:border-box}html{color-scheme:light;--bg:#f8f9fb;--panel:white;--ink:#17283b;--muted:#516277;--line:#d7dfe8;--accent:#075f79;--soft:#e1f3ed}html.dark{color-scheme:dark;--bg:#101923;--panel:#172431;--ink:#e8f0f7;--muted:#afbfce;--line:#354b60;--accent:#7cdbed;--soft:#174638}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.75 system-ui,-apple-system,"PingFang SC",sans-serif}header,main,footer{max-width:1190px;margin:auto;padding:24px 32px}header{padding-bottom:0}.eyebrow{font-size:13px;letter-spacing:.06em;color:var(--muted)}h1{font-size:clamp(28px,4vw,44px);line-height:1.25;letter-spacing:-.025em;margin:18px 0}h2{font-size:25px;margin:34px 0 12px}h3{font-size:19px;margin:26px 0 10px}p{max-width:900px}a{color:var(--accent);text-underline-offset:3px}nav{display:flex;flex-wrap:wrap;gap:7px;margin:20px 0}nav a,button,select{font:inherit;border:1px solid var(--line);border-radius:7px;background:var(--panel);color:var(--ink);padding:7px 12px;cursor:pointer;text-decoration:none}nav a[aria-current]{background:var(--ink);color:var(--bg)}.notice{border-left:4px solid var(--accent);padding:10px 16px;background:var(--panel);font-size:14px}.table-wrap{overflow:auto;max-width:100%;border:1px solid var(--line);border-radius:10px;background:var(--panel)}.inventory{max-height:540px}table{border-collapse:collapse;min-width:650px;width:100%;font-size:14px}th,td{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:var(--panel);position:sticky;top:0;z-index:1;color:var(--muted);white-space:nowrap}td{max-width:440px}td code{overflow-wrap:anywhere}.comparison-winner{background:var(--soft);font-weight:700;padding:3px 7px;border-radius:5px}section{margin:24px 0 40px}.flow{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;list-style:none;padding:0;counter-reset:stage}.flow li{counter-increment:stage;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}.flow li:before{content:counter(stage,decimal-leading-zero);display:block;color:var(--accent);font-size:24px;font-weight:700}code,pre{font:12px/1.6 ui-monospace,monospace}pre{overflow:auto;padding:18px;background:var(--panel);border:1px solid var(--line);border-radius:8px;max-height:540px;white-space:pre-wrap;overflow-wrap:anywhere}code{overflow-wrap:anywhere}dialog{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:12px;width:min(950px,94vw);max-height:90vh;overflow:auto}dialog::backdrop{background:#0008}dialog pre{max-height:62vh}details{margin:12px 0}summary{cursor:pointer;color:var(--accent)}footer{font-size:13px;color:var(--muted);border-top:1px solid var(--line)}.controls{display:flex;flex-wrap:wrap;gap:12px;margin:14px 0}.status{font-weight:600}small{color:var(--muted)}@media(max-width:650px){header,main,footer{padding:20px 16px}.flow{grid-template-columns:1fr 1fr}nav{gap:5px}nav a{font-size:13px;padding:6px 9px}td,th{padding:10px}}"""
CSS += ".diagram{overflow:auto;background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px}.diagram img{display:block;width:100%;min-width:850px;height:auto}"
JS = """document.querySelector('#theme').onclick=()=>{document.documentElement.classList.toggle('dark')};for(const b of document.querySelectorAll('[data-call-log-open]'))b.onclick=async()=>{const d=document.getElementById(b.dataset.callLogOpen);d.showModal();const p=d.querySelector('pre');try{const r=await fetch(b.dataset.src);if(!r.ok)throw Error('HTTP '+r.status);p.textContent=JSON.stringify(await r.json(),null,2)}catch(e){p.textContent=String(e)}};for(const b of document.querySelectorAll('[data-call-log-close]'))b.onclick=()=>b.closest('dialog').close();const f=document.querySelector('#filter');if(f){const update=()=>{for(const r of document.querySelectorAll('tr[data-status]'))r.hidden=f.value!=='all'&&r.dataset.status!==f.value};f.onchange=update;update()}"""


def table(headers, rows, *, inventory=False, attrs=""):
    return (
        '<div class="table-wrap'
        + (" inventory" if inventory else "")
        + '"><table '
        + attrs
        + "><thead><tr>"
        + "".join("<th>" + h + "</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join("<td>" + str(c) + "</td>" for c in row) + "</tr>" for row in rows
        )
        + "</tbody></table></div>"
    )


def page(language, route, title, body, *, detail=False, languages=("en", "zh")):
    labels = LABELS[language]
    other = "zh" if language == "en" else "en"
    nav = "".join(
        f'<a href="{p}.html"'
        + (' aria-current="page"' if route == p else "")
        + ">"
        + labels[i]
        + "</a>"
        for i, p in enumerate(PAGES)
    )
    other_route = route if not detail else "results"
    if other not in languages:
        other_route = "index"
    html = f'<!doctype html><html lang="{language}"><head><base href="../"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Web Scraper</title><link rel="stylesheet" href="../style.css"></head><body><header><div class="eyebrow">WEB SCRAPER / STRUCTURED EXTRACTION</div><h1>{esc(title)}</h1><p class="notice" data-evidence-state="compatibility">{labels[7]}</p><nav>{nav}<a href="../{other}/{other_route}.html">{"中文" if other == "zh" else "English"}</a><button id="theme">{labels[8]}</button></nav></header><main>{body}</main><footer>Web Scraper · {VERSION} · <a href="../report-manifest.json">{"输入与文件校验" if language == "zh" else "Input and file bindings"}</a></footer><script src="../app.js"></script></body></html>'
    # Artifact Site injects a site-root base even on nested pages. Use the same
    # root-relative references locally, where our own base supplies that root.
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select("a[href],link[href],script[src],img[src],[data-src]"):
        for attr in ("href", "src", "data-src"):
            value = element.get(attr)
            if value is not None and not urlsplit(value).scheme and not urlsplit(value).netloc:
                element[attr] = urljoin(f"/{language}/{route}.html", value).lstrip("/")
    return str(soup)


def build(output: Path, content: Path, *, home: Path, run=None, language="zh", profile=PROFILE):
    if language not in LABELS:
        raise ValueError("Unsupported language")
    languages = ("zh",) if language == "zh" else ("en", "zh")
    model = read_json(content)
    validate_dataset(home, profile)
    binding = read_json(profile).get("research_report", {}).get("content", {}).get("sha256")
    if binding and digest(content.read_bytes()) != binding:
        raise ValueError("Report prose differs from the profile-bound source")
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    history = read_json(home / "historical-reproduction.json")
    if history["dataset_manifest_sha256"] != digest((home / "dataset/manifest.json").read_bytes()):
        raise ValueError("Reproduction belongs to different inputs")
    selections = (
        model.get("evidence_runs", []) if run is None else [{"run": str(run), "methods": None}]
    )
    rows = []
    bindings = []
    for selection in selections:
        source = Path(selection["run"])
        source = source if source.is_absolute() else home / "runs" / source
        contract, records = load_run(source)
        if contract.get("kind") not in ("input_compatibility_only", "input_size_boundary_only"):
            raise ValueError(
                "Research report accepts compatibility evidence only; full quality cohorts need their own certified report mode"
            )
        if contract["dataset_manifest_sha256"] != history["dataset_manifest_sha256"]:
            raise ValueError("Run belongs to different inputs")
        records = [
            r
            for r in records
            if selection.get("methods") is None or r["method"] in selection["methods"]
        ]
        for record in records:
            r = json.loads(json.dumps(record))
            r["run_id"] = source.name
            if "request" in r:
                r["request"]["endpoint"] = re.sub(
                    r"/accounts/[^/]+/", "/accounts/[REDACTED]/", r["request"]["endpoint"]
                )
            rows.append(redact(r))
        bindings.append(
            {
                "run_id": source.name,
                "manifest_sha256": digest((source / "manifest.json").read_bytes()),
                "methods": selection.get("methods"),
                "contract": contract,
            }
        )
    keys = [(r["method"], r["case_id"]) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Select exactly one compatibility observation per method and case")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory")
    output.mkdir(parents=True, exist_ok=True)
    (output / "style.css").write_text(CSS)
    (output / "app.js").write_text(JS)
    for asset in model.get("assets", []):
        source = content.parent / asset["source"]
        target = output / asset["path"]
        if (
            Path(asset["path"]).is_absolute()
            or ".." in Path(asset["path"]).parts
            or source.is_symlink()
            or digest(source.read_bytes()) != asset["sha256"]
        ):
            raise ValueError("Unsafe or changed report asset")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    for r in rows:
        save_json(output / "evidence" / f"{r['method']}-{r['case_id']}.json", r, exclusive=True)
    for lang in languages:
        lab = LABELS[lang]
        directory = output / lang
        directory.mkdir()
        bodies = {p: model["pages"].get(p, {}).get("body", {}).get(lang, "") for p in PAGES}
        titles = {
            p: model["pages"].get(p, {}).get("title", {}).get(lang, lab[i])
            for i, p in enumerate(PAGES)
        }
        # Scope and observed current interfaces lead; historical scores remain separate.
        methods = sorted({r["method"] for r in rows})
        summary = []
        for method in methods:
            subset = [r for r in rows if r["method"] == method]
            called = sum(r["state"] in ("response_received", "call_error") for r in subset)
            structured = sum(r.get("compatibility") == "structured_output" for r in subset)
            summary.append(
                [
                    esc(method),
                    str(len(subset)),
                    str(called),
                    str(structured),
                    '<a href="results.html">' + lab[2] + "</a>",
                ]
            )
        bodies["index"] = (
            table(
                [
                    lab[15],
                    "计划样本" if lang == "zh" else "Planned cases",
                    "实际调用" if lang == "zh" else "API calls",
                    lab[12],
                    lab[9],
                ],
                summary,
                attrs='data-core-metrics="compatibility"',
            )
            + bodies["index"]
        )
        hr = []
        fields = ("price", "sku", "availability")
        historical_fields = (*fields, "OutOfStock")
        for method, scores in history["scores"].items():
            cells = [esc(method)]
            for field in historical_fields:
                v = scores[field]["f1"]
                best = max(s[field]["f1"] for s in history["scores"].values())
                unique = sum(s[field]["f1"] == best for s in history["scores"].values()) == 1
                text = f"{v:.3f}"
                if unique and v == best:
                    text = '<span class="comparison-winner">' + text + "</span>"
                cells.append(f'<span data-history="{field}">{text}</span>')
            hr.append(cells)
        names = [*lab[23:26], "缺货 F1" if lang == "zh" else "Out-of-stock F1"]
        headers = [
            lab[15],
            *[
                f"{name}<br><small>{sum(bool(gt.get(field)) for gt in truth.values())}/{len(truth)} annotated</small>"
                for name, field in zip(names, historical_fields, strict=True)
            ],
        ]
        if lang == "zh":
            headers = [x.replace("annotated", "有标注") for x in headers]
        hist = table(headers, hr, attrs='data-history-table="true"')
        soup = BeautifulSoup(hist, "html.parser")
        for row in soup.select("tbody tr"):
            method = row.select_one("td").get_text()
            for field, cell in zip(historical_fields, row.select("td")[1:], strict=True):
                cell.attrs.update(
                    {
                        "data-metric": field,
                        "data-metric-direction": "max",
                        "data-sort": str(history["scores"][method][field]["f1"]),
                    }
                )
        historical_title = lab[22].replace("140", str(len(truth)))
        bodies["index"] += "<section><h2>" + historical_title + "</h2>" + str(soup) + "</section>"
        dr = []
        pin = read_json(profile)["dataset"]
        for cid, gt in sorted(truth.items()):
            url = f"https://github.com/{pin['repository']}/blob/{pin['commit']}/dataset/html/{cid}.html.gz"
            ref = {f: gt.get(f, []) for f in fields}
            dr.append(
                [
                    f"<code>{cid}</code>",
                    f'<a href="{esc(gt["url"], quote=True)}">{esc(urlsplit(gt["url"]).netloc)}</a>',
                    f'<a href="{url}">HTML.gz</a>',
                    f"<details><summary>{lab[19]}</summary><pre>{esc(json.dumps(ref, ensure_ascii=False, indent=2))}</pre></details>",
                ]
            )
        bodies["dataset"] += table(
            [lab[14], "来源" if lang == "zh" else "Source", "Snapshot", lab[19]], dr, inventory=True
        )
        inventory = []
        for r in rows:
            cid = r["case_id"]
            key = r["method"] + "-" + cid
            ok = r.get("compatibility") == "structured_output"
            status = lab[12] if ok else lab[11]
            url = "result-" + key + ".html"
            error = r.get("error") or ("Missing: " + ", ".join(r.get("missing_variables", [])))
            if r.get("response") and not ok:
                error = json.dumps(r["response"], ensure_ascii=False)
            if ok:
                error = ""
            evidence = "../evidence/" + key + ".json"
            detail = f'<article data-report-detail="{"passed" if ok else "failed"}"><div data-result-summary><h2>{status}</h2><p>{esc(r["method"])} · {esc(r["state"])} · HTTP {r.get("http_status", "—")}</p><p><code>{cid}</code></p><small>{esc(r["run_id"])}</small></div>'
            if not ok:
                detail += f'<p data-failure-diagnosis>{esc(error)}</p><details id="result-logs" open><summary>{lab[21]}</summary><pre data-kind="error">{esc(error)}</pre></details>'
            ref = {f: truth[cid].get(f, []) for f in fields}
            detail += (
                table(
                    [lab[19], lab[20]],
                    [
                        [
                            f"<pre>{esc(json.dumps(ref, ensure_ascii=False, indent=2))}</pre>",
                            f"<pre>{esc(json.dumps(r.get('normalized_fields'), ensure_ascii=False, indent=2))}</pre>",
                        ]
                    ],
                )
                .replace("<pre>", "<details open><summary>JSON</summary><pre>")
                .replace("</pre>", "</pre></details>")
            )
            detail += f'<p><button data-call-log-open="evidence" data-src="{evidence}">{lab[9]}</button> · <a href="{evidence}">JSON</a></p><dialog id="evidence" aria-labelledby="evidence-title"><h2 id="evidence-title">{lab[21]}</h2><button data-call-log-close>{lab[10]}</button><pre></pre></dialog></article>'
            (directory / url).write_text(
                page(
                    lang,
                    "results",
                    r["method"] + " · " + cid[:10],
                    detail,
                    detail=True,
                    languages=languages,
                )
            )
            elapsed = f"{r['elapsed_seconds']:.2f}" if r.get("elapsed_seconds") is not None else "—"
            inventory.append(
                f'<tr data-status="{"pass" if ok else "fail"}"><td>{esc(r["method"])}</td><td><a href="{url}"><code>{cid[:12]}</code></a></td><td>{status}</td><td>{r.get("http_status") or "—"}</td><td>{elapsed}</td><td><a href="{url}">{lab[9]}</a></td></tr>'
            )
        initial = (
            "fail" if any(r.get("compatibility") != "structured_output" for r in rows) else "all"
        )
        filt = f'<label>{lab[16]} <select id="filter"><option value="fail" {"selected" if initial == "fail" else ""}>{lab[11]}</option><option value="pass">{lab[12]}</option><option value="all" {"selected" if initial == "all" else ""}>{lab[13]}</option></select></label>'
        bodies["results"] += (
            '<div class="controls">'
            + filt
            + '</div><div class="table-wrap inventory"><table><thead><tr>'
            + "".join(
                "<th>" + x + "</th>" for x in [lab[15], lab[14], lab[16], lab[17], lab[18], lab[9]]
            )
            + "</tr></thead><tbody>"
            + "".join(inventory)
            + "</tbody></table></div>"
        )
        for p in PAGES:
            (directory / (p + ".html")).write_text(
                page(lang, p, titles[p], bodies[p], languages=languages)
            )
    if "en" not in languages:
        (output / "en").mkdir()
        (output / "en/index.html").write_text(
            '<!doctype html><html lang="en"><head><base href="../"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>English edition not generated</title><link rel="stylesheet" href="style.css">'
            "</head><body><main data-language-notice><h1>English edition not generated</h1>"
            "<p>This report is available in Chinese. Ask to generate the English edition when needed.</p>"
            '<p><a href="zh/index.html">Return to the Chinese report / 返回中文报告</a></p>'
            '<button id="theme">Theme</button></main><script src="app.js"></script></body></html>'
        )
    (output / "index.html").write_text(
        f'<!doctype html><html lang="{language}"><head><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={language}/index.html"><title>Web Scraper</title></head><body><a href="{language}/index.html">Web Scraper</a></body></html>'
    )
    files = {
        p.relative_to(output).as_posix(): digest(p.read_bytes())
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    save_json(
        output / "report-manifest.json",
        {
            "template": VERSION,
            "renderer_sha256": digest(Path(__file__).read_bytes()),
            "content_sha256": digest(content.read_bytes()),
            "profile_sha256": digest(profile.read_bytes()),
            "dataset_manifest_sha256": history["dataset_manifest_sha256"],
            "primary_language": language,
            "languages": list(languages),
            "case_count": len(truth),
            "observations": len(rows),
            "source_runs": bindings,
            "files": files,
        },
        exclusive=True,
    )
    check(output)


def check(output: Path, *, release=False, visual_receipt=None):
    manifest = read_json(output / "report-manifest.json")
    if manifest["template"] != VERSION:
        raise ValueError("Wrong template")
    if manifest["renderer_sha256"] != digest(Path(__file__).read_bytes()):
        raise ValueError("Renderer identity changed; rebuild required")
    actual = {
        p.relative_to(output).as_posix()
        for p in output.rglob("*")
        if p.is_file() and p.name != "report-manifest.json"
    }
    if actual != set(manifest["files"]):
        raise ValueError("Report file inventory differs")
    for name, sha in manifest["files"].items():
        path = output / name
        if path.is_symlink() or digest(path.read_bytes()) != sha:
            raise ValueError("Report file changed: " + name)
        if path.suffix != ".html":
            continue
        soup = check_presentation(path.read_text())
        if soup.head is None:
            raise ValueError("Missing explicit head for hosted preview")
        if name != "index.html" and not soup.select_one('base[href="../"]'):
            raise ValueError("Missing local site-root base")
        notice = bool(soup.select_one("[data-language-notice]"))
        if notice and (name != "en/index.html" or "en" in manifest["languages"]):
            raise ValueError("Unexpected language notice")
        if (
            name != "index.html"
            and not notice
            and not soup.select_one('[data-evidence-state="compatibility"]')
        ):
            raise ValueError("Missing evidence state")
        if (
            name.endswith("/index.html")
            and not notice
            and not soup.select_one("[data-core-metrics]")
        ):
            raise ValueError("Missing core metrics")
        for element in soup.select("a[href],link[href],script[src],img[src],[data-src]"):
            link = element.get("href") or element.get("src") or element.get("data-src")
            u = urlsplit(link)
            if u.scheme or u.netloc:
                continue
            if u.path.startswith("/") or ".." in Path(unquote(u.path)).parts:
                raise ValueError("Reference must be relative to the hosted site root: " + link)
            target = output / unquote(u.path) if u.path else path
            if not target.resolve().is_relative_to(output.resolve()) or not target.is_file():
                raise ValueError("Broken local target: " + link)
            if u.fragment and not BeautifulSoup(target.read_text(), "html.parser").find(
                id=u.fragment
            ):
                raise ValueError("Broken evidence anchor")
    en = {p.relative_to(output / "en").as_posix() for p in (output / "en").glob("*.html")}
    zh = {p.relative_to(output / "zh").as_posix() for p in (output / "zh").glob("*.html")}
    if "en" in manifest["languages"] and en != zh:
        raise ValueError("Language route mismatch")
    if "en" not in manifest["languages"]:
        if en != {"index.html"} or not BeautifulSoup(
            (output / "en/index.html").read_text(), "html.parser"
        ).select_one("[data-language-notice] a[href='zh/index.html']"):
            raise ValueError("Missing English notice or stale English pages")
    if release:
        if visual_receipt is None:
            raise ValueError("Release requires actual visual coverage")
        visual = read_json(visual_receipt)
        if visual["report_manifest_sha256"] != digest(
            (output / "report-manifest.json").read_bytes()
        ):
            raise ValueError("Visual receipt belongs to a different report")
        required = {
            (lang, route, viewport, theme)
            for lang in manifest["languages"]
            for route in (*PAGES, "success", "failure")
            for viewport in ("desktop", "narrow")
            for theme in ("light", "dark")
        }
        if "en" not in manifest["languages"]:
            required |= {
                ("en", "english-notice", viewport, theme)
                for viewport in ("desktop", "narrow")
                for theme in ("light", "dark")
            }
        observed = set()
        for item in visual["observations"]:
            width = item["viewport_width"]
            if (item["viewport"] == "narrow" and not 320 <= width <= 480) or (
                item["viewport"] == "desktop" and width < 1000
            ):
                raise ValueError("Visual viewport dimensions do not match the declared class")
            screenshot = Path(item["screenshot"])
            if (
                screenshot.is_symlink()
                or digest(screenshot.read_bytes()) != item["screenshot_sha256"]
            ):
                raise ValueError("Visual evidence changed")
            raw = screenshot.read_bytes()
            if image_width(raw) != width:
                raise ValueError("Screenshot width does not match the observed viewport")
            if not item["heading"] or item["document_width"] > item["viewport_width"] + 1:
                raise ValueError("Missing heading or overflowing report viewport")
            observed.add((item["language"], item["route_class"], item["viewport"], item["theme"]))
        if observed != required:
            raise ValueError("Incomplete language/theme/viewport/route coverage")
    return {
        "pages_per_language": len(zh),
        "observations": manifest["observations"],
        "state": "compatibility",
        "files": len(actual),
        "release": release,
    }
