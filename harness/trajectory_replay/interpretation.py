"""Render the same authored replay article inside the report and offline."""

import base64
import html
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from harness.trajectory_replay.failure_matrix import (
    browser_failure_profile,
    failure_category_matrix,
    failure_signature_cards,
)


def _report_page_url(report_url: str, relative: str) -> str:
    base = urlsplit(report_url)
    target = urlsplit(urljoin(report_url, relative))
    query = urlencode(parse_qsl(base.query) + parse_qsl(target.query))
    return urlunsplit((target.scheme, target.netloc, target.path, query, target.fragment))


def _outcome_chart(methods: dict, rows: list[dict], lang: str) -> str:
    """Render exact pass counts as a slide-friendly visual, not another data table."""
    zh = lang == "zh"
    entries = []
    for key, method in methods.items():
        selected = [row for row in rows if row["participant_id"] == key]
        passed = sum(row["status"] == "pass" for row in selected)
        rate = passed / len(selected) * 100 if selected else 0
        entries.append(
            f'<li data-method="{html.escape(key)}"><div><strong>{html.escape(method["label"])}</strong>'
            f'<span>{passed}/{len(selected)} · {rate:.1f}%</span></div>'
            f'<div class="outcome-track" aria-label="{rate:.1f}%"><i style="width:{rate:.2f}%"></i></div></li>'
        )
    caption = "官方通过数／812 题" if zh else "Official passes / 812 tasks"
    return f'<figure class="outcome-chart" data-outcome-chart><ol>{"".join(entries)}</ol><figcaption>{caption}</figcaption></figure>'


def render_interpretation(*, source: Path, contract_audit: Path, results_sha256: str,
                          methods: dict, rows: list[dict], lang: str, image_dir: Path,
                          image_prefix: str, standalone: bool = False,
                          report_url: str = "") -> tuple[str, str]:
    article = json.loads(source.read_text())
    audit = json.loads(contract_audit.read_text())
    if (article.get("schema") != "lex_replay_interpretation/1"
            or article["results_sha256"] != results_sha256
            or audit["bindings"]["results_sha256"] != results_sha256):
        raise ValueError("interpretation is not bound to the measured cohort")
    if lang not in {"zh", "en"}:
        raise ValueError("unsupported article language")
    e = html.escape
    title = article["title"][lang]
    body = [f'<article class="interpretation"><header class="hero"><h1>{e(title)}</h1>'
            f'<p>{e(article["intro"][lang])}</p></header>']
    seen = set()
    for section in article["sections"]:
        identity = section["id"]
        if identity in seen:
            raise ValueError("duplicate article section")
        seen.add(identity)
        body.append(
            f'<section class="presentation-slide" id="{e(identity)}"><h2>{e(section["title"][lang])}</h2>'
        )
        if section.get("data") == "outcomes":
            body.append(_outcome_chart(methods, rows, lang))
        elif section.get("data") == "failure_matrix":
            body.append(failure_category_matrix(methods, rows, lang, embedded=True))
        elif section.get("data") == "failure_signatures":
            participants = tuple(section.get("participants") or ())
            body.append(
                failure_signature_cards(
                    methods, rows, lang, participants=participants
                ).replace('href="interpretation.html#browser-diagnoses"', 'href="#browser-diagnoses"')
            )
        elif section.get("data") == "browser_failure_profile":
            body.append(
                browser_failure_profile(methods, rows, lang, section["participant"])
            )
        elif section.get("data") == "contract_changes":
            headers = ("题目", "原合同", "改动及影响") if lang == "zh" else ("Task", "Original contract", "Change and effect")
            body.append(_table(headers, [
                [f'#{row["task_id"]}', row[f"before_{lang}"], row[f"after_{lang}"]]
                for row in audit["task_contract_changes"]
            ]))
        elif section.get("data"):
            raise ValueError("unknown article data block")
        if section.get("figure"):
            filename = f'{section["figure"]}-{lang}.png'
            if Path(filename).name != filename:
                raise ValueError("article figure path escapes asset directory")
            image = image_dir / filename
            if not image.is_file() or any(path.is_symlink() for path in (image, *image.parents)):
                raise ValueError("article figure is missing or symlinked")
            src = ("data:image/png;base64," + base64.b64encode(image.read_bytes()).decode()
                   if standalone else image_prefix + filename)
            figure_link = "下载原尺寸图" if lang == "zh" else "Download full-size diagram"
            scroll_label = "机制图，可横向滚动查看" if lang == "zh" else "Mechanism diagram, scroll horizontally to read"
            download_href = (
                _report_page_url(report_url, image_prefix + filename)
                if standalone else src
            )
            download = f' <a href="{e(download_href)}" download="{e(filename)}">{figure_link}</a>'
            body.append(f'<figure><div class="diagram-scroll" tabindex="0" role="region" aria-label="{scroll_label}">'
                        f'<img src="{e(src)}" alt="{e(section["title"][lang])}"></div>'
                        f'<figcaption>{e(section["caption"][lang])}{download}</figcaption></figure>')
        body.extend(f'<p>{e(paragraph[lang])}</p>' for paragraph in section["paragraphs"])
        if specimen := section.get("specimen"):
            headers = specimen["headers"]
            values = specimen["rows"]
            if not headers or not values or any(len(row) != len(headers) for row in values):
                raise ValueError("article specimen requires a nonempty rectangular table")
            def localized(value):
                if isinstance(value, dict):
                    if set(value) != {"zh", "en"}:
                        raise ValueError("article specimen requires both languages")
                    return value[lang]
                if not isinstance(value, (str, int, float)):
                    raise ValueError("unsupported article specimen value")
                return value
            caption = localized(specimen["caption"])
            body.append('<figure class="article-specimen">'
                        + _table([localized(value) for value in headers],
                                 [[localized(value) for value in row] for row in values])
                        + f'<figcaption>{e(caption)}</figcaption></figure>')
        if sources := section.get("sources"):
            links = []
            for citation in sources:
                target = urlsplit(citation["url"])
                if (target.scheme != "https" or not target.hostname
                        or target.username or target.password):
                    raise ValueError("article source requires a public HTTPS URL")
                if set(citation["label"]) != {"zh", "en"}:
                    raise ValueError("article source requires both languages")
                links.append(f'<a href="{e(citation["url"])}">{e(citation["label"][lang])}</a>')
            label = "源码与测试" if lang == "zh" else "Source and tests"
            body.append(f'<p class="article-sources">{label}: ' + " · ".join(links) + '</p>')
        body.append("</section>")
    body.append("</article>")
    result = "".join(body)
    if standalone:
        if not report_url.startswith("https://"):
            raise ValueError("offline article requires the bound HTTPS report URL")
        # Data visuals create report-relative evidence links. Preserve the share token
        # in the script-free standalone article.
        def absolute_result_link(match):
            target = _report_page_url(report_url, html.unescape(match[1]))
            return f'href="{e(target)}"'
        result = re.sub(
            r'href="((?:results\.html|result/[^"?#]+\.html)[^"]*)"',
            absolute_result_link,
            result,
        )
    return title, result


def _table(headers, rows):
    e = lambda value: html.escape(str(value))
    return ('<div class="table-scroll"><table><thead><tr>'
            + "".join(f"<th>{e(value)}</th>" for value in headers)
            + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{e(value)}</td>" for value in row) + "</tr>" for row in rows)
            + "</tbody></table></div>")


def standalone_document(title: str, body: str, lang: str, css: str) -> str:
    return (f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><style>{css}</style></head>'
            f'<body><main>{body}</main></body></html>')
