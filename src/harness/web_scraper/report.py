"""Bilingual research and input-compatibility reports for structured extraction."""

from __future__ import annotations

import json
import math
import re
import statistics
import struct
from html import escape as esc
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from harness.distribution import repository_root
from harness.env_file import local_setting
from harness.report_languages import resolve_report_language
from harness.reporting.release_check import check_report_identity, checked_page, open_report
from harness.reporting.templates import template_identity
from harness.reporting.versions import bind_build_version
from harness.web_fetch.publication_privacy import project_evidence_view, publication_risk
from harness.web_scraper.analysis import failure_stage, field_results, native_error
from harness.web_scraper.messages import message, messages_sha256, validate_catalog
from harness.web_scraper.pipeline import (
    PROFILE,
    digest,
    execution_identity,
    load_run,
    normalize,
    read_json,
    require_external_output,
    save_json,
    score_rows,
    score_run,
    validate_dataset,
)
from harness.web_scraper.report_ui import JS

VERSION = "web-scraper-research/1"
STAGES = {
    "client_transport": (
        message("stages_client_transport", "zh"),
        message("stages_client_transport", "en"),
    ),
    "model_capacity": (
        message("stages_model_capacity", "zh"),
        message("stages_model_capacity", "en"),
    ),
    "navigation": (message("stages_navigation", "zh"), message("stages_navigation", "en")),
    "provider_schema": (
        message("stages_provider_schema", "zh"),
        message("stages_provider_schema", "en"),
    ),
    "provider_fetch": (
        message("stages_provider_fetch", "zh"),
        message("stages_provider_fetch", "en"),
    ),
    "provider_response": (
        message("stages_provider_response", "zh"),
        message("stages_provider_response", "en"),
    ),
    "fields": (message("stages_fields", "zh"), message("stages_fields", "en")),
}
OUTCOMES = {
    "correct": (message("outcomes_correct", "zh"), message("outcomes_correct", "en")),
    "missing": (message("outcomes_missing", "zh"), message("outcomes_missing", "en")),
    "unexpected": (message("outcomes_unexpected", "zh"), message("outcomes_unexpected", "en")),
    "wrong": (message("outcomes_wrong", "zh"), message("outcomes_wrong", "en")),
}
FIELD_LABELS = {
    "price": (message("field_labels_price", "zh"), message("field_labels_price", "en")),
    "sku": (message("field_labels_sku", "zh"), message("field_labels_sku", "en")),
    "availability": (
        message("field_labels_availability", "zh"),
        message("field_labels_availability", "en"),
    ),
    "OutOfStock": (
        message("field_labels_OutOfStock", "zh"),
        message("field_labels_OutOfStock", "en"),
    ),
}


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


LABELS = {"zh": message("navigation_labels", "zh"), "en": message("navigation_labels", "en")}
PAGES = (
    "index",
    "dataset",
    "results",
    "protocol",
    "candidates",
    "research",
    "accounts",
    "review",
    "interpretation",
)
EXTRA_LABELS = {
    "review": {
        "zh": message("extra_labels_review", "zh"),
        "en": message("extra_labels_review", "en"),
    },
    "interpretation": {
        "zh": message("extra_labels_interpretation", "zh"),
        "en": message("extra_labels_interpretation", "en"),
    },
}
ASSETS = Path(__file__).with_name("assets")
CSS = (ASSETS / "report.css").read_text(encoding="utf-8")


def table(headers, rows, *, inventory=False, attrs="", sort_values=None):
    sort_values = sort_values or {}
    if any(len(values) != len(rows) for values in sort_values.values()):
        raise ValueError("Numeric sort population differs from table rows")

    def cell(value, row, column):
        number = sort_values.get(column, [None] * len(rows))[row]
        attribute = ' data-sort="' + esc(str(number)) + '"' if number is not None else ""
        return "<td" + attribute + ">" + str(value) + "</td>"

    return (
        '<div class="table-wrap'
        + (" inventory" if inventory else "")
        + '"><table '
        + attrs
        + ' data-highlight-ties="all"><thead><tr>'
        + "".join("<th>" + h + "</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join(cell(c, i, j) for j, c in enumerate(row)) + "</tr>"
            for i, row in enumerate(rows)
        )
        + "</tbody></table></div>"
    )


def page(
    language, route, title, body, *, detail=False, languages=("en", "zh"), state="compatibility"
):
    labels = LABELS[language]
    if state != "compatibility":
        labels = list(labels)
        labels[7] = message(
            "records_notice"
            if state == "field-extraction-cohort"
            else "frozen_product_page_extraction_machine_scoring_failed_calls_retai",
            language,
        )
        labels[2] = message("results_and_failures", language)
    other = "zh" if language == "en" else "en"
    nav = "".join(
        f'<a href="{p}.html"'
        + (' aria-current="page"' if route == p else "")
        + ">"
        + (EXTRA_LABELS[p][language] if p in EXTRA_LABELS else labels[i])
        + "</a>"
        for i, p in enumerate(PAGES)
    )
    other_route = route if not detail else "results"
    if other not in languages:
        other_route = "index"
    html = '<!doctype html><html lang="{part0}"><head><base href="../"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{part1} · Web Scraper</title><link rel="stylesheet" href="../style.css"></head><body><header><div class="eyebrow">WEB SCRAPER / STRUCTURED EXTRACTION</div><h1>{part2}</h1><p class="notice" data-evidence-state="{part3}">{part4}</p><nav>{part5}<a href="../{part6}/{part7}.html">{part8}</a><button id="theme">{part9}</button></nav></header><main>{part10}</main><footer>Web Scraper · {part11} · <a href="../report-manifest.json">{part12}</a></footer><script src="../app.js"></script></body></html>'.format(
        part0=language,
        part1=esc(title),
        part2=esc(title),
        part3=state,
        part4=labels[7],
        part5=nav,
        part6=other,
        part7=other_route,
        part8=message("english", other),
        part9=labels[8],
        part10=body,
        part11=VERSION,
        part12=message("input_and_file_bindings", language),
    )
    # Artifact Site injects a site-root base even on nested pages. Use the same
    # root-relative references locally, where our own base supplies that root.
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.select("a[href],link[href],script[src],img[src],[data-src]"):
        for attr in ("href", "src", "data-src"):
            value = element.get(attr)
            if value is not None and not urlsplit(value).scheme and not urlsplit(value).netloc:
                element[attr] = urljoin(f"/{language}/{route}.html", value).lstrip("/")
    return str(soup)


def cohort_source(source, selection, home, truth):
    """Validate a complete certified run or an immutable journal checkpoint."""
    contract = read_json(source / "contract.json")
    plan_path = Path(selection["plan"])
    plan = read_json(plan_path)
    precall = read_json(source / "certification-precall.json")
    if (
        digest(plan_path.read_bytes()) != selection["plan_sha256"]
        or precall["profile_sha256"] != selection["plan_sha256"]
        or set(contract["case_ids"]) != set(truth)
        or contract["kind"] != "full_archived_extraction"
        or plan["case_count"] != len(truth)
        or plan["case_ids_sha256"] != digest(("\n".join(sorted(truth)) + "\n").encode())
        or contract["methods"] != [precall["method"]]
        or precall["method"] not in plan["participants"]
        or precall["dataset_manifest_sha256"] != contract["dataset_manifest_sha256"]
        or precall["pipeline_sha256"] != contract["pipeline_sha256"]
    ):
        raise ValueError("Full cohort plan, population or execution binding differs")
    for relative, sha in [
        ("execution/pipeline.py", contract["pipeline_sha256"]),
    ]:
        if sha and digest((source / relative).read_bytes()) != sha:
            raise ValueError("Frozen execution changed")
    certificate = source / "certificate.json"
    if certificate.exists():
        contract, rows = load_run(source)
        cert = read_json(certificate)
        for name, key in [
            ("manifest.json", "manifest_sha256"),
            ("scores.json", "scores_sha256"),
            ("certification-precall.json", "precall_sha256"),
        ]:
            if digest((source / name).read_bytes()) != cert[key]:
                raise ValueError("Certified evidence changed")
        if cert["profile_sha256"] != selection["plan_sha256"] or cert["case_count"] != len(truth):
            raise ValueError("Certificate population or profile differs")
        saved = read_json(source / "scores.json")
        # Recompute through the original scoring boundary, without provider calls.
        recalculated = score_run(home, source)
        if saved != recalculated:
            raise ValueError("Certified scores do not reproduce")
        physical = read_json(source / "manifest.json").get("physical_calls", [])
        attempts_by_key = {}
        for entry in physical:
            attempt = read_json(source / entry["path"])
            attempts_by_key.setdefault((attempt["method"], attempt["case_id"]), []).append(attempt)
        for row in rows:
            attempts = attempts_by_key.get((row["method"], row["case_id"]), [])
            if len(attempts) > 1:
                row["physical_attempts"] = attempts
        assessment = None
        if selection.get("assessment"):
            assessment = selection["assessment"]
            assessment_path = Path(assessment["path"])
            if digest(assessment_path.read_bytes()) != assessment["sha256"]:
                raise ValueError("Field projection assessment changed")
            saved = score_run(home, source, reproject=True, output=assessment_path)
            rows = [
                {
                    **r,
                    "collected_normalized_fields": r.get("normalized_fields"),
                    "normalized_fields": normalize(r["method"], r.get("response", {})),
                    "projection_revision": saved["projection_revision"],
                }
                for r in rows
            ]
        repair_binding = []
        if selection.get("repair"):
            from harness.web_scraper.repair import load_selection

            revisions = selection["repair"]
            _, rows = load_selection(home, selection)
            if selection.get("assessment"):
                rows = [
                    {**r, "normalized_fields": normalize(r["method"], r.get("response", {}))}
                    for r in rows
                ]
            saved = {**saved, "scores": score_rows(home, truth, rows, contract["methods"])}
            for revision in revisions:
                directory = Path(revision["path"])
                receipt = read_json(directory / "certificate.json")
                repair_binding.append(
                    {
                        "artifact_id": directory.name,
                        "certificate_sha256": revision["sha256"],
                        "profile_sha256": receipt["profile_sha256"],
                        "repaired_cases": receipt["case_count"],
                        "inherited_cases": len(truth) - receipt["case_count"],
                    }
                )
        return (
            contract,
            rows,
            {
                "certified": True,
                "scores": saved["scores"],
                "certificate_sha256": digest(certificate.read_bytes()),
                "repair": repair_binding,
                "assessment": {"artifact_id": assessment_path.name, "sha256": assessment["sha256"]}
                if assessment
                else None,
            },
        )
    journal = source / "calls.journal.jsonl"
    raw = journal.read_bytes()
    if not raw.endswith(b"\n"):
        raise ValueError("Checkpoint contains an unfinished journal entry; retry after the write")
    latest, physical = {}, []
    for line in raw.splitlines():
        entry = json.loads(line)
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe checkpoint evidence path")
        path = source / relative
        row = read_json(path)
        if digest(path.read_bytes()) != entry["sha256"]:
            raise ValueError("Checkpoint call changed")
        key = (row["method"], row["case_id"])
        if (
            key[0] not in contract["methods"]
            or key[1] not in truth
            or row["input_sha256"]
            != digest((home / "dataset/html" / (key[1] + ".html")).read_bytes())
        ):
            raise ValueError("Checkpoint call is outside the frozen input scope")
        physical.append(row)
        latest[key] = row
    rows = []
    for key, value in latest.items():
        row = dict(value)
        row["physical_attempts"] = [r for r in physical if (r["method"], r["case_id"]) == key]
        rows.append(row)
    return (
        contract,
        rows,
        {
            "access_blocked": read_json(source / "access-blocked.json")
            if (source / "access-blocked.json").exists()
            else None,
            "certified": False,
            "scores": {},
            "checkpoint_sha256": digest(raw),
            "physical_calls": len(physical),
        },
    )


def reference_populations(truth):
    total = len(truth)
    stock = sum("OutOfStock" in r for r in truth.values())
    return {
        **{
            f: {
                "total": total,
                "present": sum(bool(r.get(f)) for r in truth.values()),
                "missing": sum(not r.get(f) for r in truth.values()),
                "label": "population_values",
            }
            for f in ("price", "sku")
        },
        "availability": {
            "total": total,
            "present": total - stock,
            "missing": stock,
            "label": "population_stock",
        },
        "OutOfStock": {
            "total": total,
            "present": stock,
            "missing": total - stock,
            "label": "population_out",
        },
    }


def price_quote(value, lang):
    bounds = value.get("usd_per_1000_range")
    if bounds is None:
        return (
            message("usage_priced", lang) if value.get("pricing_formula") else value["usd_per_1000"]
        )
    if (
        not isinstance(bounds, list)
        or len(bounds) != 2
        or any(type(n) not in (int, float) or not math.isfinite(n) or n < 0 for n in bounds)
        or bounds[0] > bounds[1]
    ):
        raise ValueError("Invalid USD per 1,000 calls range")
    low, high = (f"{n:.3f}".rstrip("0").rstrip(".") for n in bounds)
    return "$" + low + ("–" + high if bounds[0] != bounds[1] else "")


def cohort_metrics(methods, rows, bindings, count, lang, labels=None, *, truth=None):
    """One full-population summary, with incomplete quality left unavailable."""
    labels = labels or {}
    scores = {m: b["scores"][m] for b in bindings if b.get("certified") for m in b["scores"]}
    fields = ("price", "sku", "availability", "OutOfStock")
    blocked = {b["contract"]["methods"][0] for b in bindings if b.get("access_blocked")}
    records = []
    for method in methods:
        subset = [r for r in rows if r["method"] == method]
        complete = method in scores
        cells = [
            esc(labels.get(method, method)),
            str(count),
            str(len(subset)),
            str(sum(r.get("compatibility") == "structured_output" for r in subset)),
            (message("complete", lang))
            if complete
            else (
                (message("access_limited", lang))
                if method in blocked
                else (message("incomplete", lang))
            ),
        ]
        for field in fields:
            value = scores.get(method, {}).get(field, {}).get("f1")
            cells.append("—" if value is None else f"{value:.3f}")
        records.append(cells)
    result = BeautifulSoup(
        table(
            (message("method_planned_pages_executed_structured_output_full_scope_price", lang)),
            records,
            attrs='data-core-metrics="full-cohort"',
        ),
        "html.parser",
    )
    if truth is not None:
        for field, heading in zip(fields, result.select("thead th")[5:], strict=True):
            population = reference_populations(truth)[field]
            heading.append(result.new_tag("br"))
            small = result.new_tag("small", attrs={"data-reference-population": field})
            small.string = message(
                population["label"], lang, **{k: v for k, v in population.items() if k != "label"}
            )
            heading.append(small)
    for method, row in zip(methods, result.select("tbody tr"), strict=True):
        row["data-method"] = method
        for field, cell in zip(fields, row.select("td")[5:], strict=True):
            if method not in scores:
                continue
            # F1 comes from integer counts; discard only floating arithmetic noise.
            value = round(scores[method][field]["f1"], 12)
            values = [round(score[field]["f1"], 12) for score in scores.values()]
            cell.attrs.update(
                {"data-metric": field, "data-metric-direction": "max", "data-sort": str(value)}
            )
            if value == max(values):
                cell.string = ""
                span = result.new_tag("span", attrs={"class": "comparison-winner"})
                span.string = f"{value:.3f}"
                cell.append(span)
            link = result.new_tag(
                "a", href=f"results.html?method={method}&field={field.lower()}&status=all"
            )
            for child in list(cell.contents):
                link.append(child.extract())
            cell.append(link)
    note = message("f1_ranges_from_0_to_1_higher_is_better_quality_is_shown_only_for", lang)
    latency = []
    for method in methods:
        for successful in (False, True):
            selected = [
                r
                for r in rows
                if r["method"] == method
                and (not successful or r.get("compatibility") == "structured_output")
            ]
            values = sorted(
                r["elapsed_seconds"] for r in selected if r.get("elapsed_seconds") is not None
            )
            label = message("structured_output_calls" if successful else "all_executed_calls", lang)
            retry_time = f"{sum(r.get('retry_execution_seconds', 0) for r in selected):.2f}"
            unknown_retries = sum(r.get("retry_missing_timers", 0) for r in selected)
            if unknown_retries:
                retry_time = message(
                    "additional_attempts_untimed",
                    lang,
                    value0=f"{retry_time}",
                    value1=f"{unknown_retries}",
                )
            latency.append(
                [
                    esc(labels.get(method, method)),
                    label,
                    str(len(values)),
                    str(len(selected) - len(values)),
                    retry_time,
                    f"{statistics.mean(values):.3f}" if values else "—",
                    f"{statistics.median(values):.3f}" if values else "—",
                    f"{values[max(0, math.ceil(len(values) * 0.95) - 1)]:.3f}" if values else "—",
                ]
            )
    timing = BeautifulSoup(
        table(
            message("method_population_timed_n_untimed_n_extra_retry_time_s_mean_s_p50", lang),
            latency,
            attrs='data-latency-table="true"',
        ),
        "html.parser",
    )
    groups = {}
    for ordinal, row in enumerate(timing.select("tbody tr")):
        population = "structured" if ordinal % 2 else "all"
        for metric, cell in zip(("mean", "p50", "p95"), row.select("td")[5:], strict=True):
            method = methods[ordinal // 2]
            selected = [
                r["elapsed_seconds"]
                for r in rows
                if r["method"] == method
                and r.get("elapsed_seconds") is not None
                and (population == "all" or r.get("compatibility") == "structured_output")
            ]
            if not selected:
                continue
            value = (
                statistics.mean(selected)
                if metric == "mean"
                else statistics.median(selected)
                if metric == "p50"
                else sorted(selected)[max(0, math.ceil(len(selected) * 0.95) - 1)]
            )
            name = population + "-" + metric
            cell.attrs.update(
                {"data-metric": name, "data-metric-direction": "min", "data-sort": str(value)}
            )
            groups.setdefault(name, []).append((value, cell))
    for values in groups.values():
        lowest = min(v for v, _ in values)
        for value, cell in values:
            if value != lowest:
                continue
            text = cell.get_text()
            cell.clear()
            span = timing.new_tag("span", attrs={"class": "comparison-winner"})
            span.string = text
            cell.append(span)
    return str(result) + "<p>" + note + "</p>", str(timing)


def price_table(methods, prices, lang, labels=None):
    labels = labels or {}
    records = []
    for method in methods:
        value = prices.get(method, {})
        sources = value.get("sources", [])
        if (
            not value.get("usd_per_1000")
            or not value.get("terms", {}).get(lang)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.get("verified_at", ""))
            or not sources
            or any(urlsplit(u).scheme != "https" for u in sources)
        ):
            raise ValueError("Every participant requires sourced cost/pricing terms: " + method)
        records.append(
            [
                esc(labels.get(method, method)),
                esc(price_quote(value, lang)),
                esc(value["terms"][lang]),
                esc(value["verified_at"]),
                " · ".join(
                    f'<a href="{esc(u, quote=True)}">{i}</a>' for i, u in enumerate(sources, 1)
                ),
            ]
        )
    return table(
        message("method_usd_1_000_requests_billing_terms_and_commitment_verified_o", lang),
        records,
        attrs='data-price-table="true"',
    )


def usage_table(methods, rows, lang, labels=None):
    labels = labels or {}
    records = []
    for method in methods:
        subset = [r for r in rows if r["method"] == method]
        physical = [a for r in subset for a in r.get("physical_attempts", [r])]
        observed = []
        totals = {}
        for r in physical:
            headers = {k.lower(): v for k, v in r.get("provider_usage", {}).items()}
            response = r.get("response")
            response = response if isinstance(response, dict) else {}
            data = response.get("data")
            metadata = data.get("metadata") if isinstance(data, dict) else None
            metadata = metadata if isinstance(metadata, dict) else {}
            receipts = {}
            parse_metadata = headers.get("parse_metadata", {})
            parse_metadata = parse_metadata if isinstance(parse_metadata, dict) else {}
            value = headers.get(
                "x-scrapfly-api-cost",
                metadata.get("creditsUsed", parse_metadata.get("creditsUsed")),
            )
            if value is not None:
                receipts["credits"] = value
            custom = headers.get("custom_attributes", {})
            custom = custom if isinstance(custom, dict) else {}
            tokens = response.get("usage", {})
            tokens = tokens if isinstance(tokens, dict) else {}
            for unit, native in (
                ("input_tokens", custom.get("inputTokens", tokens.get("promptTokens"))),
                ("output_tokens", custom.get("outputTokens", tokens.get("completionTokens"))),
            ):
                if native is not None:
                    receipts[unit] = native
            for key in ("x-browser-ms-used", "x-browser-time", "x-browser-usage"):
                if key in headers:
                    receipts[key] = headers[key]
            if receipts:
                observed.append(receipts)
            for unit, value in receipts.items():
                totals.setdefault(unit, []).append(value)
        usages = []
        for unit, values in totals.items():
            try:
                total = sum(float(value) for value in values)
                if not math.isfinite(total):
                    raise ValueError("Nonfinite usage")
            except (TypeError, ValueError):
                usages.append(unit + ": " + esc(json.dumps(values, ensure_ascii=False)))
            else:
                label = "ms (x-browser-ms-used)" if unit == "x-browser-ms-used" else unit
                usages.append(f"{total:g} {label}")
        records.append(
            [
                esc(labels.get(method, method)),
                str(len(physical)),
                str(len(observed)),
                "; ".join(usages) if observed else "—",
            ]
        )
    return table(
        message("method_physical_attempts_usage_receipts_reported_usage", lang),
        records,
        attrs='data-usage-table="true"',
    )


def operating_metrics(methods, rows, prices, lang, labels, *, compared=None):
    """Home-page latency and price, with the measured population kept explicit."""
    price_table(methods, prices, lang, labels)
    compared = set(methods if compared is None else compared)
    records = []
    for method in methods:
        selected = [r for r in rows if r["method"] == method]
        values = sorted(
            r["elapsed_seconds"] for r in selected if r.get("elapsed_seconds") is not None
        )
        stats = (
            [
                statistics.mean(values),
                statistics.median(values),
                values[max(0, math.ceil(len(values) * 0.95) - 1)],
            ]
            if values
            else [None] * 3
        )
        records.append(
            [
                esc(labels.get(method, method)),
                str(len(values)),
                str(len(selected) - len(values)),
                *[f"{v:.3f}" if v is not None else "—" for v in stats],
                esc(price_quote(prices[method], lang)),
                esc(
                    prices[method]
                    .get("billing_summary", {})
                    .get(lang, prices[method]["terms"][lang])
                )
                + '<br><a href="accounts.html">'
                + message("terms_and_sources", lang)
                + "</a>",
            ]
        )
    soup = BeautifulSoup(
        table(
            message("method_timed_n_untimed_n_mean_s_p50_s_p95_s_quoted_usd_1_000_call", lang)[:6]
            + [message("price_range_heading", lang), message("price_remarks_heading", lang)],
            records,
            attrs='data-operating-metrics="true" data-sortable',
            sort_values={column: [int(row[column]) for row in records] for column in (1, 2)},
        ),
        "html.parser",
    )
    for index, metric in enumerate(("mean", "p50", "p95"), 3):
        observed = []
        for method, row in zip(methods, soup.select("tbody tr"), strict=True):
            cell = row.select("td")[index]
            if cell.get_text() != "—":
                # Use unrounded values for direction and ties.
                values = sorted(
                    r["elapsed_seconds"]
                    for r in rows
                    if r["method"] == method and r.get("elapsed_seconds") is not None
                )
                value = (
                    statistics.mean(values)
                    if metric == "mean"
                    else statistics.median(values)
                    if metric == "p50"
                    else values[max(0, math.ceil(len(values) * 0.95) - 1)]
                )
                cell.attrs.update(
                    {
                        "data-sort": str(value),
                        "data-metric": "home-" + metric,
                        "data-metric-direction": "min" if method in compared else "neutral",
                    }
                )
                if method in compared:
                    observed.append((value, cell))
        if observed:
            low = min(v for v, _ in observed)
            for value, cell in observed:
                if value != low:
                    continue
                span = soup.new_tag("span", attrs={"class": "comparison-winner"})
                span.string = cell.get_text()
                cell.clear()
                cell.append(span)
    note = (
        message("latency_includes_all_executed_cases_including_failures_using_the", lang)
        + " "
        + message("pricing_scope_note", lang)
    )
    return (
        "<section data-home-operations><h2>"
        + (message("latency_and_pricing", lang))
        + "</h2><p>"
        + note
        + "</p>"
        + str(soup)
        + "</section>"
    )


def field_table(row, lang):
    labels = {
        "price": message("diagnostic_labels_price", "zh"),
        "sku": message("diagnostic_labels_sku", "zh"),
        "availability": message("diagnostic_labels_availability", "zh"),
        "OutOfStock": message("diagnostic_labels_OutOfStock", "zh"),
    }
    outcomes = {
        "correct": message("diagnostic_outcomes_correct", "zh"),
        "missing": message("diagnostic_outcomes_missing", "zh"),
        "unexpected": message("diagnostic_outcomes_unexpected", "zh"),
        "wrong": message("diagnostic_outcomes_wrong", "zh"),
    }
    records = []
    for field, value in row["field_results"].items():
        records.append(
            [
                labels[field] if lang == "zh" else field,
                esc(json.dumps(value["expected"], ensure_ascii=False)),
                esc(json.dumps(value["actual"], ensure_ascii=False)),
                outcomes[value["status"]] if lang == "zh" else value["status"],
                str(value["tp"]),
                str(value["fp"]),
                str(value["fn"]),
            ]
        )
    return table(
        message("field_accepted_reference_values_output_outcome_tp_fp_fn", lang),
        records,
        attrs='data-field-diagnostics="true"',
    )


def result_controls(methods, rows, names, lang):
    def select(key, title, options):
        return (
            "<label>"
            + title
            + f'<select data-result-filter="{key}">'
            + "".join(
                f'<option value="{esc(value, quote=True)}">{esc(label)}</option>'
                for value, label in options
            )
            + "</select></label>"
        )

    zh = lang == "zh"
    return (
        '<form class="controls" data-result-controls onsubmit="return false">'
        + select(
            "status",
            message("outcome", lang),
            [
                ("fail", message("mismatch_or_call_failure", lang)),
                ("pass", message("all_fields_match", lang)),
                ("all", message("all", lang)),
            ],
        )
        + select(
            "method",
            message("method", lang),
            [("", message("all_methods", lang)), *[(m, names.get(m, m)) for m in methods]],
        )
        + select(
            "field",
            message("field", lang),
            [
                ("", message("any_field", lang)),
                ("price", message("field_labels_price", lang)),
                ("sku", "SKU"),
                ("availability", message("field_labels_availability", lang)),
                ("outofstock", message("field_labels_OutOfStock", lang)),
            ],
        )
        + select(
            "stage",
            message("stage", lang),
            [
                ("", message("all_stages", lang)),
                *[
                    (s, STAGES[s][0 if zh else 1])
                    for s in sorted({r["failure_stage"] for r in rows})
                ],
            ],
        )
        + (
            "<label>"
            + (message("search", lang))
            + '<input type="search" data-result-filter="query"'
            + (message("aria_label_search_case_domain_or_error", lang))
            + '></label><output data-result-count aria-live="polite"></output></form>'
        )
    )


def audited_value(metrics):
    return metrics["f1"] if metrics["cases"] else None


def check_audited_quality(soup, methods, scores, total, lang):
    rows = soup.select("[data-audited-quality] tbody tr")
    if len(rows) != len(methods):
        raise ValueError("Missing audited quality population")
    for method, row in zip(methods, rows, strict=True):
        cells = row.select("td")[1:]
        if len(cells) != 4:
            raise ValueError("Audited field inventory differs")
        for field, cell in zip(("price", "sku", "availability", "OutOfStock"), cells, strict=True):
            metrics = scores[method][field]
            scored = metrics["cases"]
            if not isinstance(scored, int) or not 0 <= scored <= total:
                raise ValueError("Invalid audited population")
            value = audited_value(metrics)
            expected = "—" if value is None else f"{value:.3f}"
            note = cell.select_one("[data-audited-population]")
            label = message(
                "audited_population", lang, scored=scored, total=total, excluded=total - scored
            )
            if (
                note is None
                or note.get("data-audited-population") != str(scored)
                or list(cell.stripped_strings) != [expected, label]
            ):
                raise ValueError("Audited F1 or field population differs")


def audit_explanation(audit, rows, methods, labels, lang, *, core_html=None):
    """Reader-facing review scope and source-qualified correction inventory."""
    zh = lang == "zh"
    count = audit["result_count"]
    changed = len(audit["reference_changes"])
    review = (
        "<section data-complete-review><h2>{part0}</h2>".format(
            part0=message("complete_audit_scope", lang)
        )
        + (
            message(
                "all_pages_and_current_results_are_covered_the_original_results_we",
                lang,
                value0=f"{audit['case_count']}",
                value1=f"{count}",
                value2=f"{audit['original_results_reviewed']}",
                value3=f"{audit['supplemental_results_reviewed']}",
            )
        )
        + "<p>"
        + (
            message(
                "the_model_audited_frozen_sources_annotations_and_complete_native",
                lang,
                **dict(value0=esc(audit["reviewer"]["model"])) if zh else dict(),
            )
        )
        + "</p></section>"
    )
    fields = ("price", "sku", "availability", "OutOfStock")
    comparison = BeautifulSoup(
        table(
            [
                message("method", lang),
                *[FIELD_LABELS[f][0 if zh else 1] + " F1" for f in fields],
            ],
            [
                [
                    esc(labels.get(method, method)),
                    *[
                        "—"
                        if audited_value(audit["audited_scores"][method][field]) is None
                        else f"{audit['audited_scores'][method][field]['f1']:.3f}"
                        for field in fields
                    ],
                ]
                for method in methods
            ],
            attrs='data-audited-quality="true" data-sortable',
        ),
        "html.parser",
    )
    for method, row in zip(methods, comparison.select("tbody tr"), strict=True):
        for field, cell in zip(fields, row.select("td")[1:], strict=True):
            value = audited_value(audit["audited_scores"][method][field])
            values = [audited_value(audit["audited_scores"][m][field]) for m in methods]
            values = [round(v, 12) for v in values if v is not None]
            value = round(value, 12) if value is not None else None
            cell.attrs.update(
                {
                    "data-metric": "audited-" + field,
                    "data-metric-direction": "max",
                }
            )
            if value is not None:
                cell["data-sort"] = str(value)
            if value is not None and value == max(values):
                span = comparison.new_tag("span", attrs={"class": "comparison-winner"})
                span.string = cell.get_text()
                cell.clear()
                cell.append(span)
    for method, row in zip(methods, comparison.select("tbody tr"), strict=True):
        for field, cell in zip(fields, row.select("td")[1:], strict=True):
            scored = audit["audited_scores"][method][field]["cases"]
            note = comparison.new_tag("small", attrs={"data-audited-population": str(scored)})
            note.string = message(
                "audited_population",
                lang,
                scored=scored,
                total=audit["case_count"],
                excluded=audit["case_count"] - scored,
            )
            cell.append(comparison.new_tag("br"))
            cell.append(note)
    review += "<h2>" + (message("diagnostic_f1_after_reference_audit", lang)) + "</h2><p>"
    review += (
        (
            message(
                "each_method_still_covers_pages_the_same_native_outputs_and_offici",
                lang,
                value0=f"{audit['case_count']}",
            )
        )
        + "</p>"
        + "<p>"
        + message("audited_population_scope", lang)
        + "</p>"
        + str(comparison)
    )
    review += (
        "<h2>" + (message("reference_amendments_fields", lang, value0=f"{changed}")) + "</h2><p>"
    )
    review += (
        message("original_annotations_and_scores_are_retained_these_source_qualifi", lang)
    ) + "</p>"
    review += table(
        message("case_field_original_audited_evidence_and_adjudication", lang),
        [
            [
                f'<a href="source-{r["case_id"]}.html"><code>{r["case_id"][:12]}</code></a>',
                FIELD_LABELS[r["field"]][0 if zh else 1],
                esc(json.dumps(r["original"], ensure_ascii=False)),
                esc(json.dumps(r["accepted"], ensure_ascii=False)),
                esc(r["reason"]),
            ]
            for r in audit["reference_changes"]
        ],
        inventory=True,
        attrs='data-reference-revisions="true" data-sortable',
    )
    excluded = audit.get("excluded_fields", {})
    if excluded:
        review += "<h2>" + (message("conflicting_source_fields", lang)) + "</h2><p>"
        review += (
            message("these_fields_remain_in_official_scores_audited_diagnostics_identi", lang)
        ) + "</p>"
        review += table(
            [LABELS[lang][14], message("field", lang)],
            [
                [
                    f'<a href="source-{cid}.html">{cid[:12]}</a>',
                    ", ".join(FIELD_LABELS[f][0 if zh else 1] for f in fields),
                ]
                for cid, fields in excluded.items()
            ],
        )
    interpretation = (
        "<section data-interpretation-summary><h2>"
        + (message("reading_this_comparison", lang))
        + "</h2><p>"
    )
    structured = sum(r.get("compatibility") == "structured_output" for r in rows)
    interpretation += (
        message(
            "methods_each_executed_pages_results_structured_responses_failure",
            lang,
            value0=f"{len(methods)}",
            value1=f"{audit['case_count']}",
            value2=f"{count}",
            value3=f"{structured}",
        )
    ) + "</p></section>"
    if core_html:
        compact = BeautifulSoup(core_html, "html.parser").select_one("table")
        for row in compact.select("tr"):
            for index, cell in enumerate(list(row.find_all(["th", "td"], recursive=False))):
                if index not in (0, 3, 5, 6, 7, 8):
                    cell.decompose()
        compact.attrs = {
            "data-interpretation-comparison": "true",
            "data-highlight-ties": "all",
        }
        interpretation += (
            "<h2>"
            + (message("compact_original_reference_comparison", lang))
            + "</h2>"
            + '<div class="table-wrap">'
            + str(compact)
            + "</div>"
        )
    interpretation += "<h2>" + (message("scoring_checks_field_matches", lang)) + "</h2><p>"
    interpretation += (
        message("each_field_allows_multiple_accepted_reference_values_but_one_pred", lang)
    ) + "</p>"
    if audit["reference_changes"]:
        example = audit["reference_changes"][0]
        interpretation += (
            '<p><a href="source-'
            + example["case_id"]
            + '.html">'
            + (message("inspect_a_source_annotation_and_correction_example", lang))
            + '</a> · <a href="case-'
            + example["case_id"]
            + '.html">'
            + (message("compare_every_method_on_that_case", lang))
            + "</a></p>"
        )
    for method in methods:
        selected = [r for r in rows if r["method"] == method]
        counts = []
        for field in ("price", "sku", "availability"):
            missing = sum(r["field_results"][field]["status"] == "missing" for r in selected)
            wrong = sum(
                r["field_results"][field]["status"] in ("wrong", "unexpected") for r in selected
            )
            counts.append(
                [
                    FIELD_LABELS[field][0 if zh else 1],
                    str(missing),
                    str(wrong),
                    f'<a href="results.html?method={method}&field={field}&status=fail">'
                    + (message("inspect_all_differences", lang))
                    + "</a>",
                ]
            )
        failures = [r for r in selected if r.get("compatibility") != "structured_output"]
        interpretation += (
            '<section data-method-interpretation="'
            + method
            + '"><h2>'
            + esc(labels.get(method, method))
            + "</h2><p>"
        )
        interpretation += (
            message(
                "pages_returned_structured_fields_all_original_reference_differenc",
                lang,
                value0=f"{len(selected)}",
                value1=f"{len(selected) - len(failures)}",
            )
        ) + "</p>"
        interpretation += table(
            message("field_missing_wrong_or_unexpected_cases", lang),
            counts,
        )
        repaired = [r for r in selected if r.get("diagnosis")]
        representative = (
            failures[:1]
            or repaired[:1]
            or [
                r
                for r in selected
                if any(f["status"] != "correct" for f in r["field_results"].values())
            ][:1]
        )
        if representative:
            row = representative[0]
            diagnosis = row.get("diagnosis", {})
            interpretation += (
                "<p>"
                + esc(
                    diagnosis.get("cause")
                    or row.get("native_error")
                    or (
                        message("no_recognizable_product_fields_returned", lang)
                        if row.get("compatibility") != "structured_output"
                        else ""
                    )
                    or (
                        message(
                            "fields_were_returned_these_differences_concern_extracted_content", lang
                        )
                    )
                )
                + "</p><p>"
            )
            interpretation += (
                f'<a href="result-{method}-{row["case_id"]}.html">'
                + (message("representative_result_and_complete_response", lang))
                + "</a></p>"
            )
        interpretation += "</section>"
    interpretation += (
        '<p><a href="review.html">'
        + (message("complete_audit_scope_and_reference_amendments", lang))
        + '</a> · <a href="protocol.html">'
        + (message("scoring_timing_and_execution_protocol", lang))
        + "</a></p>"
    )
    return review, interpretation


def publication_host_names():
    return tuple(
        host.strip()
        for host in local_setting(
            "WEB_SCRAPER_PUBLICATION_HOSTS", repository_root() / ".env"
        ).split(",")
        if host.strip()
    )


def public_risk(raw):
    return publication_risk(raw) or any(
        host.lower().encode() in raw.lower() for host in publication_host_names()
    )


def copy_report_assets(output, content, model):
    """Materialize only content-bound reusable assets."""
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


def build(output: Path, content: Path, *, home: Path, run=None, language=None, profile=PROFILE):
    from harness.web_scraper.dataset import require_product_reference

    require_external_output(output)
    if content.is_file() and read_json(content).get("mode") == "field-extraction-cohort":
        return build_record_cohort(output, content, home=home, language=language, profile=profile)
    require_product_reference(home)
    validate_catalog()
    language = resolve_report_language(output, language)
    if language not in LABELS:
        raise ValueError("Unsupported language")
    languages = ("zh",) if language == "zh" else ("en", "zh")
    model = read_json(content)
    names = model.get("method_labels", {})
    if language == "en" and any(
        page.get("body", {}).get("zh") and not page.get("body", {}).get("en")
        for page in model["pages"].values()
    ):
        raise ValueError("English content is missing; generate the English edition first")
    cohort = model.get("mode") == "full-cohort-progress"
    state = "full-cohort-progress" if cohort else "compatibility"
    cohort_plan = None
    if cohort:
        cohort_plan = read_json(Path(model["cohort_plan"]["path"]))
        if (
            digest(Path(model["cohort_plan"]["path"]).read_bytes())
            != model["cohort_plan"]["sha256"]
        ):
            raise ValueError("Report cohort plan changed")
    validate_dataset(home, profile)
    distribution = None
    if model.get("dataset_distribution"):
        from harness.web_scraper.dataset import distribution_identity

        distribution = distribution_identity(
            home, read_json(profile)["dataset"], model["dataset_distribution"]
        )
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
        extra = {}
        if cohort:
            if selection.get("methods") is not None and set(selection["methods"]) != set(
                read_json(source / "contract.json")["methods"]
            ):
                raise ValueError("Full-cohort filters must retain the declared method")
            selected_plan = selection["plan_sha256"]
            for method in read_json(source / "contract.json")["methods"]:
                authorized_plan = model.get("source_cohorts", {}).get(
                    method, model["cohort_plan"]["sha256"]
                )
                if selected_plan != authorized_plan:
                    raise ValueError(
                        "Selected source cohort is not bound by the report composition"
                    )
            contract, records, extra = cohort_source(source, selection, home, truth)
        else:
            contract, records = load_run(source)
        if not cohort and contract.get("kind") not in (
            "input_compatibility_only",
            "input_size_boundary_only",
        ):
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
        if (
            cohort
            and extra.get("certified")
            and (len(records) != len(truth) or {r["case_id"] for r in records} != set(truth))
        ):
            raise ValueError("Certified report observations must cover every case")
        for record in records:
            r = json.loads(json.dumps(record))
            r["source_logical_sha256"] = digest(
                json.dumps(record, sort_keys=True, ensure_ascii=False).encode()
            )
            r["review_execution_sha256"] = execution_identity(record)
            r["run_id"] = source.name
            if "request" in r:
                r["request"]["endpoint"] = re.sub(
                    r"/accounts/[^/]+/", "/accounts/[REDACTED]/", r["request"]["endpoint"]
                )
            rows.append(r)
        bindings.append(
            {
                "run_id": source.name,
                "manifest_sha256": digest((source / "manifest.json").read_bytes())
                if (source / "manifest.json").exists()
                else None,
                **extra,
                "source_plan_sha256": selection.get("plan_sha256"),
                "methods": selection.get("methods"),
                "contract": contract,
            }
        )
    if cohort:
        expected_cases = digest(("\n".join(sorted(truth)) + "\n").encode())
        if (
            cohort_plan["case_count"] != len(truth)
            or cohort_plan["case_ids_sha256"] != expected_cases
            or set(cohort_plan["participants"])
            != {m for b in bindings for m in b["contract"]["methods"]}
            or len(bindings) != len(cohort_plan["participants"])
        ):
            raise ValueError("Report is missing a planned method or uses a substituted population")
    keys = [(r["method"], r["case_id"]) for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Select exactly one compatibility observation per method and case")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty output directory")
    diagnoses = {}
    if model.get("diagnoses"):
        binding = model["diagnoses"]
        source = content.parent / binding["path"]
        if digest(source.read_bytes()) != binding["sha256"]:
            raise ValueError("Failure diagnoses changed")
        diagnoses = read_json(source)["results"]
    audit = None
    if model.get("audit"):
        binding = model["audit"]
        source = content.parent / binding["path"]
        if digest(source.read_bytes()) != binding["sha256"]:
            raise ValueError("Complete review binding changed")
        audit = read_json(source)
        expected = {r["method"] + ":" + r["case_id"] for r in rows}
        if (
            audit["review_scope"] != "all"
            or audit["unresolved"] != 0
            or audit["accepted_unreviewed_passes"] != 0
            or set(audit["results"]) != expected
            or audit["case_count"] != len(truth)
        ):
            raise ValueError("Full review is incomplete")
    if read_json(profile).get("independent_result_review_scope") == "all" and audit is None:
        raise ValueError("Full-review profile requires its completed audit")
    for row in rows:
        row["field_results"] = field_results(home, truth[row["case_id"]], row, profile=profile)
        row["failure_stage"] = failure_stage(row)
        row["native_error"] = native_error(row)
        row["method_label"] = names.get(row["method"], row["method"])
        key = row["method"] + ":" + row["case_id"]
        if key in diagnoses:
            if diagnoses[key]["current_call_sha256"] != row["source_logical_sha256"]:
                raise ValueError("Failure diagnosis belongs to a different current call")
            row["diagnosis"] = diagnoses[key]
        if audit:
            row["review"] = audit["results"][key]
            if row["review"].get("execution_sha256") != row["review_execution_sha256"]:
                raise ValueError("Review belongs to a different current execution")
            if row["review"]["native_response_sha256"] != digest(
                json.dumps(row.get("response"), sort_keys=True, ensure_ascii=False).encode()
            ):
                raise ValueError("Review belongs to a different native response")
    output.mkdir(parents=True, exist_ok=True)
    (output / "style.css").write_text(CSS, encoding="utf-8")
    (output / "app.js").write_text(JS, encoding="utf-8")
    copy_report_assets(output, content, model)
    buckets = (
        {
            m: {r["case_id"]: r for r in rows if r["method"] == m}
            for m in cohort_plan["participants"]
        }
        if cohort
        else {f"{r['method']}-{r['case_id']}": r for r in rows}
    )
    public_receipts, public_rows = [], []
    for key, bucket in buckets.items():
        name = f"public-evidence/{key}.json"
        raw = json.dumps(bucket, ensure_ascii=False, indent=2, allow_nan=False).encode()
        public, receipt = project_evidence_view(
            name,
            raw,
            role="native_response",
            content_format="json",
            host_names=publication_host_names(),
        )
        projected = json.loads(public)
        before = list(bucket.values()) if cohort else [bucket]
        after = list(projected.values()) if cohort else [projected]
        for original, view in zip(before, after, strict=True):
            method = original["method"]
            if normalize(method, original.get("response", {})) != normalize(
                method, view.get("response", {})
            ):
                raise ValueError("Public evidence projection changed extracted fields")
        target = output / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(public)
        public_receipts.append(receipt)
        public_rows.extend(after)
    rows = public_rows
    for lang in languages:
        lab = LABELS[lang]
        directory = output / lang
        directory.mkdir()
        bodies = {p: model["pages"].get(p, {}).get("body", {}).get(lang, "") for p in PAGES}
        titles = {
            p: model["pages"]
            .get(p, {})
            .get("title", {})
            .get(lang, EXTRA_LABELS[p][lang] if p in EXTRA_LABELS else lab[i])
            for i, p in enumerate(PAGES)
        }
        # Scope and observed current interfaces lead; historical scores remain separate.
        methods = (
            list(cohort_plan["participants"]) if cohort else sorted({r["method"] for r in rows})
        )
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
                    message("planned_cases", lang),
                    message("api_calls", lang),
                    lab[12],
                    lab[9],
                ],
                summary,
                attrs='data-core-metrics="compatibility"',
            )
            + bodies["index"]
        )
        if not cohort:
            bodies["index"] += operating_metrics(
                methods, rows, model.get("prices", {}), lang, names
            )
            bodies["accounts"] += price_table(methods, model.get("prices", {}), lang, names)
        if cohort:
            core, timings = cohort_metrics(
                methods, rows, bindings, len(truth), lang, names, truth=truth
            )
            bodies["index"] = (
                core
                + operating_metrics(methods, rows, model.get("prices", {}), lang, names)
                + model["pages"].get("index", {}).get("body", {}).get(lang, "")
            )
            bodies["protocol"] += "<h2>" + (message("call_latency", lang)) + "</h2>" + timings
            bodies["accounts"] += (
                "<h2>"
                + (message("prices_by_method", lang))
                + "</h2>"
                + price_table(methods, model.get("prices", {}), lang, names)
            )
        if audit:
            review_body, interpretation_body = audit_explanation(
                audit, rows, methods, names, lang, core_html=core if cohort else None
            )
            bodies["review"] = review_body
            bodies["interpretation"] = interpretation_body
            bodies["index"] += (
                '<p><a href="interpretation.html">'
                + (message("read_the_interpretation", lang))
                + '</a> · <a href="review.html">'
                + (message("inspect_complete_audit_and_reference_changes", lang))
                + "</a></p>"
            )
        if cohort:
            bodies["protocol"] += (
                "<h2>"
                + (message("reported_usage", lang))
                + "</h2>"
                + usage_table(methods, rows, lang, names)
            )
            bodies["protocol"] += message(
                "usage_comes_from_native_response_metadata_or_billing_headers_incl", lang
            )
        hr = []
        fields = ("price", "sku", "availability")
        historical_fields = (*fields, "OutOfStock")
        for method, scores in history["scores"].items():
            cells = [esc(method)]
            for field in historical_fields:
                v = scores[field]["f1"]
                best = max(s[field]["f1"] for s in history["scores"].values())
                text = f"{v:.3f}"
                if v == best:
                    text = '<span class="comparison-winner">' + text + "</span>"
                cells.append(f'<span data-history="{field}">{text}</span>')
            hr.append(cells)
        historical_names = [*lab[23:26], message("out_of_stock_f1", lang)]
        headers = [
            lab[15],
            *[
                f"{name}<br><small>{sum(bool(gt.get(field)) for gt in truth.values())}/{len(truth)} annotated</small>"
                for name, field in zip(historical_names, historical_fields, strict=True)
            ],
        ]
        if lang == "zh":
            headers = [x.replace("annotated", message("annotated", lang)) for x in headers]
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
        historical_title = message("historical_reproduction", lang, case_count=len(truth))
        bodies["research" if cohort else "index"] += (
            "<section><h2>" + historical_title + "</h2>" + str(soup) + "</section>"
        )
        dr = []
        pin = read_json(profile)["dataset"]
        for cid, gt in sorted(truth.items()):
            url = f"https://github.com/{pin['repository']}/blob/{pin['commit']}/dataset/html/{cid}.html.gz"
            ref = {f: gt.get(f, []) for f in fields}
            dr.append(
                [
                    f'<a href="source-{cid}.html"><code>{cid}</code></a>',
                    f'<a href="{esc(gt["url"], quote=True)}">{esc(urlsplit(gt["url"]).netloc)}</a>',
                    f'<a href="{url}">HTML.gz</a>',
                    f"<details><summary>{lab[19]}</summary><pre>{esc(json.dumps(ref, ensure_ascii=False, indent=2))}</pre></details>",
                ]
            )
            raw_html = (home / "dataset/html" / (cid + ".html")).read_text(encoding="utf-8")
            source_soup = BeautifulSoup(raw_html, "html.parser")
            structured = [
                str(e)
                for e in source_soup.select('script[type="application/ld+json"],meta[content]')
            ]
            outline = source_soup.get_text("\n", strip=True) + "\n\n" + "\n".join(structured)
            source_body = (
                f'<article data-source-case="{cid}"><p><code>{cid}</code></p>'
                f'<p><a href="{esc(gt["url"], quote=True)}">{esc(gt["url"])}</a> · '
                f'<a href="{url}">HTML.gz</a> · <a href="case-{cid}.html">'
                + (message("compare_all_methods", lang))
                + "</a></p>"
                + (
                    message(
                        "original_annotations_follow_an_empty_array_contains_no_accepted_v", lang
                    )
                )
                + "<pre data-source-reference>"
                + esc(json.dumps(ref, ensure_ascii=False, indent=2))
                + "</pre>"
            )
            image = model.get("source_screenshots", {}).get(cid)
            if image:
                source = content.parent / image["source"]
                target = output / "source-images" / (cid + ".jpeg")
                if source.is_symlink() or digest(source.read_bytes()) != image["sha256"]:
                    raise ValueError("Source screenshot changed")
                if not target.exists():
                    target.parent.mkdir(exist_ok=True)
                    target.write_bytes(source.read_bytes())
                source_body += (
                    "<details><summary>"
                    + (message("author_screenshot_captured_before_html", lang))
                    + "</summary>"
                )
                source_body += f'<figure><img class="source-screenshot" loading="lazy" src="../source-images/{cid}.jpeg" alt="'
                source_body += (
                    message("product_screenshot_saved_by_the_dataset_author", lang)
                ) + '"><figcaption>'
                source_body += (
                    message(
                        "the_author_captured_this_screenshot_before_the_html_snapshot_it_m", lang
                    )
                ) + "</figcaption></figure></details>"
            if audit:
                source_review = audit["sources"][cid]
                source_body += (
                    "<section data-source-audit><h2>"
                    + (message("source_evidence_and_reference_audit", lang))
                    + "</h2>"
                )
                source_body += table(
                    message("field_audited_values_source_quote_reference_adjudication", lang),
                    [
                        [
                            FIELD_LABELS[field][0 if lang == "zh" else 1],
                            esc(
                                json.dumps(
                                    audit["audited_reference"][cid].get(field, []),
                                    ensure_ascii=False,
                                )
                            ),
                            "<blockquote>"
                            + esc(source_review["source_fields"][field]["source_quote"])
                            + "</blockquote>",
                            esc(source_review["reference_review"][field]["reason"]),
                        ]
                        for field in fields
                    ],
                )
                source_body += "</section>"
            source_body += message(
                "frozen_html_text_and_structured_data_includes_hidden_dom_text_not", lang
            )
            source_body += (
                '<pre class="source-preview" data-source-outline>'
                + esc(outline)
                + "</pre></article>"
            )
            (directory / ("source-" + cid + ".html")).write_text(
                page(
                    lang,
                    "dataset",
                    (message("source_and_annotations", lang)) + cid[:12],
                    source_body,
                    detail=True,
                    languages=languages,
                    state=state,
                ),
                encoding="utf-8",
            )
            compared = []
            case_rows = []
            for method in methods:
                row = next((r for r in rows if r["case_id"] == cid and r["method"] == method), None)
                if row is None:
                    continue
                case_rows.append(row)
                cells = [
                    f'<a href="result-{method}-{cid}.html">{esc(names.get(method, method))}</a>'
                ]
                for field in fields:
                    result = row["field_results"][field]
                    cells.append(
                        '<span class="field-'
                        + result["status"]
                        + '">'
                        + esc(json.dumps(result["actual"], ensure_ascii=False))
                        + "</span><br><small>"
                        + OUTCOMES[result["status"]][0 if lang == "zh" else 1]
                        + "</small>"
                    )
                cells.extend(
                    [
                        esc(STAGES[row["failure_stage"]][0 if lang == "zh" else 1]),
                        f"{row['elapsed_seconds']:.2f}"
                        if row.get("elapsed_seconds") is not None
                        else "—",
                    ]
                )
                compared.append(cells)
            case_body = (
                f'<article data-same-case="{cid}"><p><a href="source-{cid}.html">'
                + (message("source_and_annotations_a71c1ae7", lang))
                + "</a></p>"
            )
            case_body += "<pre>" + esc(json.dumps(ref, ensure_ascii=False, indent=2)) + "</pre>"
            case_body += (
                table(
                    [
                        lab[15],
                        message("field_labels_price", lang),
                        "SKU",
                        message("field_labels_availability", lang),
                        message("stage", lang),
                        message("seconds", lang),
                    ],
                    compared,
                    attrs='data-case-comparison="true" data-sortable',
                    sort_values={5: [row.get("elapsed_seconds") for row in case_rows]},
                )
                + "</article>"
            )
            (directory / ("case-" + cid + ".html")).write_text(
                page(
                    lang,
                    "results",
                    (message("same_case_comparison", lang)) + cid[:12],
                    case_body,
                    detail=True,
                    languages=languages,
                    state=state,
                ),
                encoding="utf-8",
            )
        bodies["dataset"] += table(
            [lab[14], message("source", lang), message("snapshot", lang), lab[19]],
            dr,
            inventory=True,
        )
        if distribution:
            bodies["dataset"] = (
                '<p data-dataset-distribution><a href="'
                + esc(distribution["url"])
                + '">'
                + esc(
                    message("download_pinned_dataset", lang, revision=distribution["revision"][:12])
                )
                + "</a></p>"
                + bodies["dataset"]
            )
        inventory = []
        for r in rows:
            cid = r["case_id"]
            key = r["method"] + "-" + cid
            ok = r.get("compatibility") == "structured_output"
            status = lab[12] if ok else lab[11]
            url = "result-" + key + ".html"
            error = (
                ""
                if ok
                else (r["native_error"] or message("no_recognizable_product_fields_returned", lang))
            )
            evidence = "../public-evidence/" + (r["method"] if cohort else key) + ".json"
            evidence_key = f' data-key="{cid}"' if cohort else ""
            detail = f'<article data-report-detail="{"passed" if ok else "failed"}"><div data-result-summary><h2>{status}</h2><p>{esc(names.get(r["method"], r["method"]))} · {esc(r["state"])} · HTTP {r.get("http_status", "—")}</p><p><code>{cid}</code></p><small>{esc(r["run_id"])}</small></div>'
            detail += (
                f'<p class="case-links"><a href="case-{cid}.html">'
                + (message("compare_all_methods", lang))
                + f'</a><a href="source-{cid}.html">'
                + (message("source_and_annotations_cf191383", lang))
                + "</a></p>"
            )
            detail += field_table(r, lang)
            if r.get("review"):
                reviewed = {**r, "field_results": r["review"]["audited_fields"]}
                detail += (
                    "<section data-audited-result><h2>"
                    + (message("field_diagnosis_after_reference_audit", lang))
                    + "</h2>"
                )
                detail += (
                    "<p>"
                    + (
                        message(
                            "original_scores_remain_unchanged_this_diagnostic_uses_adjudicated",
                            lang,
                        )
                    )
                    + "</p>"
                    + field_table(reviewed, lang).replace(
                        'data-field-diagnostics="true"', 'data-audited-fields="true"'
                    )
                    + "</section>"
                )
            if r.get("review"):
                findings = r["review"]
                review_rows = []
                for field, assessment in findings["reviewer_fields"].items():
                    review_rows.append(
                        [
                            esc(field),
                            esc(assessment["status"]),
                            esc(assessment["reason"]),
                            esc(assessment["source_quote"]),
                            esc(findings["field_reconciliation"][field]["matcher_status"]),
                        ]
                    )
                detail += (
                    "<section data-reviewer-findings><h2>"
                    + message("reviewer_findings", lang)
                    + "</h2>"
                )
                detail += "<p>" + message("reviewer_reconciliation", lang) + "</p>"
                detail += table(message("reviewer_findings_columns", lang), review_rows)
                detail += "<p>" + esc(findings["reviewer_projection_issue"]) + "</p></section>"
            if r.get("diagnosis"):
                diagnosis = r["diagnosis"]
                detail += (
                    "<section data-reviewed-diagnosis><h2>"
                    + (message("original_failure_and_treatment", lang))
                    + "</h2><p>"
                    + esc(diagnosis["cause"])
                    + "</p><p>"
                    + esc(diagnosis["treatment"])
                    + "</p></section>"
                )
            if not ok:
                detail += '<p data-failure-diagnosis>{part0}</p><details id="result-logs" open><summary>{part1}</summary><pre data-kind="error">{part2}</pre></details>'.format(
                    part0=message("no_scoreable_fields_returned_see_the_program_error_below", lang),
                    part1=lab[21],
                    part2=esc(error),
                )
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
            detail += f'<p><button data-call-log-open="evidence" data-src="{evidence}"{evidence_key}>{lab[9]}</button> · <a href="{evidence}">JSON</a></p><dialog id="evidence" aria-labelledby="evidence-title"><h2 id="evidence-title">{lab[21]}</h2><button data-call-log-close>{lab[10]}</button><pre></pre></dialog></article>'
            (directory / url).write_text(
                page(
                    lang,
                    "results",
                    names.get(r["method"], r["method"]) + " · " + cid[:10],
                    detail,
                    detail=True,
                    languages=languages,
                    state=state,
                ),
                encoding="utf-8",
            )
            elapsed = f"{r['elapsed_seconds']:.2f}" if r.get("elapsed_seconds") is not None else "—"
            field_cells = ""
            outcomes = {
                "correct": message("compact_outcome_correct", "zh"),
                "missing": message("compact_outcome_missing", "zh"),
                "unexpected": message("compact_outcome_unexpected", "zh"),
                "wrong": message("compact_outcome_wrong", "zh"),
            }
            for field in fields:
                outcome = r["field_results"][field]["status"]
                field_cells += (
                    f'<td><button class="field-button field-{outcome}" data-call-log-open="diagnosis" data-src="{evidence}"{evidence_key} data-field="{field}">'
                    + (outcomes[outcome] if lang == "zh" else outcome)
                    + "</button></td>"
                )
            data_fields = " ".join(
                f'data-{field.lower()}="{value["status"]}"'
                for field, value in r["field_results"].items()
            )
            correct = all(v["status"] == "correct" for v in r["field_results"].values())
            search = cid + " " + truth[cid]["url"] + " " + error
            inventory.append(
                f'<tr data-status="{"pass" if ok else "fail"}" data-method="{r["method"]}" data-stage="{r["failure_stage"]}" data-outcome="{"correct" if correct and ok else "wrong"}" data-search="{esc(search, quote=True)}" {data_fields}><td>{esc(names.get(r["method"], r["method"]))}</td><td><a href="{url}"><code>{cid[:12]}</code></a></td><td>{status}</td>{field_cells}<td>{r.get("http_status") or "—"}</td><td data-sort="{r.get("elapsed_seconds", "")}">{elapsed}</td><td><button data-call-log-open="diagnosis" data-src="{evidence}"{evidence_key}>{lab[9]}</button> · <a href="{url}">'
                + (message("full_result", lang))
                + "</a></td></tr>"
            )
        if cohort:
            observed = {(r["method"], r["case_id"]) for r in rows}
            for method in methods:
                for cid in sorted(truth):
                    if (method, cid) not in observed:
                        search = esc(cid + " " + truth[cid].get("url", ""), quote=True)
                        inventory.append(
                            f'<tr data-status="pending" data-method="{method}" data-outcome="pending" data-search="{search}"><td>{esc(names.get(method, method))}</td><td><code>{cid[:12]}</code></td><td>{message("not_executed", lang)}</td>'
                            + "<td>—</td>" * 6
                            + "</tr>"
                        )
        bodies["results"] += (
            result_controls(methods, rows, names, lang)
            + '<div class="table-wrap inventory"><table data-sortable><thead><tr>'
            + "".join(
                "<th>" + x + "</th>"
                for x in [
                    lab[15],
                    lab[14],
                    lab[16],
                    message("field_labels_price", lang),
                    "SKU",
                    message("field_labels_availability", lang),
                    lab[17],
                    lab[18],
                    lab[9],
                ]
            )
            + "</tr></thead><tbody>"
            + "".join(inventory)
            + "</tbody></table></div>"
            + '<dialog id="diagnosis" aria-labelledby="diagnosis-title"><h2 id="diagnosis-title">'
            + (message("field_and_call_evidence", lang))
            + "</h2><button data-call-log-close>"
            + lab[10]
            + "</button><div data-diagnosis-body></div></dialog>"
        )
        for p in PAGES:
            (directory / (p + ".html")).write_text(
                page(lang, p, titles[p], bodies[p], languages=languages, state=state),
                encoding="utf-8",
            )
    if "en" not in languages:
        (output / "en").mkdir()
        (output / "en/index.html").write_text(
            message("english_not_generated_notice", "en"), encoding="utf-8"
        )
    (output / "index.html").write_text(
        f'<!doctype html><html lang="{language}"><head><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={language}/index.html"><title>Web Scraper</title></head><body><a href="{language}/index.html">Web Scraper</a></body></html>',
        encoding="utf-8",
    )
    build_version = bind_build_version(output)
    files = {
        p.relative_to(output).as_posix(): digest(p.read_bytes())
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    public_bindings, metadata_receipt = project_evidence_view(
        "public-evidence/metadata.json",
        json.dumps(bindings, ensure_ascii=False).encode(),
        role="native_response",
        content_format="json",
        host_names=publication_host_names(),
    )
    save_json(
        output / "report-manifest.json",
        {
            "template": template_identity("web-scraper"),
            "template_version": VERSION,
            "browser_eval": build_version,
            "asset_sha256": {
                p.name: digest(p.read_bytes()) for p in ASSETS.iterdir() if p.is_file()
            },
            "dataset_kind": "published-upstream"
            if not read_json(profile).get("local_example", False)
            else "local-example",
            "mode": state,
            "planned_methods": list(cohort_plan["participants"]) if cohort else None,
            "method_labels": names,
            "prices": model.get("prices", {}),
            "renderer_sha256": digest(Path(__file__).read_bytes()),
            "interaction_sha256": digest(Path(__file__).with_name("report_ui.py").read_bytes()),
            "messages_sha256": messages_sha256(),
            "analysis_sha256": digest(Path(__file__).with_name("analysis.py").read_bytes()),
            "review": {
                k: audit[k]
                for k in (
                    "review_scope",
                    "result_count",
                    "case_count",
                    "original_results_reviewed",
                    "supplemental_results_reviewed",
                    "accepted_unreviewed_passes",
                    "unresolved",
                )
            }
            if audit
            else None,
            "required_review_scope": read_json(profile).get("independent_result_review_scope"),
            "audited_scores": audit.get("audited_scores") if audit else None,
            "content_sha256": digest(content.read_bytes()),
            "profile_sha256": digest(profile.read_bytes()),
            "dataset_manifest_sha256": history["dataset_manifest_sha256"],
            "dataset_distribution": distribution,
            "primary_language": language,
            "languages": list(languages),
            "reference_populations": reference_populations(truth),
            "case_count": len(truth),
            "observations": len(rows),
            "source_runs": json.loads(public_bindings),
            "source_metadata_view": metadata_receipt,
            "public_evidence_views": public_receipts,
            "files": files,
        },
        exclusive=True,
    )
    check(output)


def check(output: Path, *, release=False, visual_receipt=None):
    output = open_report(output)
    manifest = read_json(output / "report-manifest.json")
    if manifest.get("mode") == "field-extraction-cohort":
        return check_record_cohort(output, release=release, visual_receipt=visual_receipt)
    check_report_identity(output, manifest, "web-scraper")
    if public_risk((output / "report-manifest.json").read_bytes()):
        raise ValueError("Report metadata has a publication privacy risk")
    validate_catalog()
    if manifest.get("messages_sha256") != messages_sha256():
        raise ValueError("Report message catalog changed; rebuild required")
    if manifest["template_version"] != VERSION:
        raise ValueError("Wrong template")
    if manifest.get("asset_sha256") != {
        p.name: digest(p.read_bytes()) for p in ASSETS.iterdir() if p.is_file()
    }:
        raise ValueError("Report assets changed; rebuild required")
    if (
        release
        and manifest.get("dataset_kind") == "published-upstream"
        and not manifest.get("dataset_distribution")
    ):
        raise ValueError("Published benchmark releases require a pinned dataset download binding")
    if release and manifest.get("dataset_kind") == "published-upstream":
        if manifest.get("mode") != "full-cohort-progress":
            raise ValueError("Formal releases require a complete certified cohort")
        methods = manifest["planned_methods"]
        sources = manifest["source_runs"]
        if (
            len(sources) != len(methods)
            or not all(source.get("certified") for source in sources)
            or {m for source in sources for m in source["contract"]["methods"]} != set(methods)
            or manifest["observations"] != len(methods) * manifest["case_count"]
            or any(
                len(set(source["contract"]["case_ids"])) != manifest["case_count"]
                for source in sources
            )
        ):
            raise ValueError("Formal releases require a complete certified cohort")
        for value in manifest["prices"].values():
            if "usd_per_1000_range" not in value and not value.get("pricing_formula"):
                raise ValueError(
                    "Formal releases require normalized price ranges or a billing formula"
                )
    if manifest["renderer_sha256"] != digest(Path(__file__).read_bytes()):
        raise ValueError("Renderer identity changed; rebuild required")
    if manifest.get("interaction_sha256") != digest(
        Path(__file__).with_name("report_ui.py").read_bytes()
    ):
        raise ValueError("Interaction identity changed; rebuild required")
    if manifest.get("analysis_sha256") != digest(
        Path(__file__).with_name("analysis.py").read_bytes()
    ):
        raise ValueError("Field analysis identity changed; rebuild required")
    review = manifest.get("review")
    if manifest.get("required_review_scope") == "all" and (
        not review
        or review["review_scope"] != "all"
        or review["unresolved"] != 0
        or review["accepted_unreviewed_passes"] != 0
        or review["result_count"] != manifest["observations"]
        or review["original_results_reviewed"] != manifest["observations"]
    ):
        raise ValueError("Required full review is incomplete")
    actual = {
        p.relative_to(output).as_posix()
        for p in output.rglob("*")
        if p.is_file() and p != output / "report-manifest.json"
    }
    if actual != set(manifest["files"]):
        raise ValueError("Report file inventory differs")
    views = manifest.get("public_evidence_views", [])
    if {v["path"] for v in views} != {n for n in actual if n.startswith("public-evidence/")}:
        raise ValueError("Public evidence view receipts are incomplete")
    for view in views:
        public = (output / view["path"]).read_bytes()
        if digest(public) != view["published_sha256"] or public_risk(public):
            raise ValueError("Public evidence privacy receipt failed")
    for name, sha in manifest["files"].items():
        path = output / name
        if path.is_symlink() or digest(path.read_bytes()) != sha:
            raise ValueError("Report file changed: " + name)
        if public_risk(path.read_bytes()):
            raise ValueError("Report artifact has a publication privacy risk: " + name)
        if path.suffix != ".html":
            continue
        soup = checked_page(path)
        if (
            name.endswith("/index.html")
            and name.split("/")[0] in manifest["languages"]
            and manifest.get("mode") == "full-cohort-progress"
        ):
            for field, population in manifest["reference_populations"].items():
                node = soup.select_one(
                    '[data-core-metrics] [data-reference-population="' + field + '"]'
                )
                expected = message(
                    population["label"],
                    name.split("/")[0],
                    **{k: v for k, v in population.items() if k != "label"},
                )
                if node is None or node.get_text() != expected:
                    raise ValueError("Field scoring/reference population is missing or changed")
        if name.endswith("/dataset.html") and manifest.get("dataset_distribution"):
            links = soup.select("[data-dataset-distribution] a")
            if len(links) != 1 or links[0].get("href") != manifest["dataset_distribution"]["url"]:
                raise ValueError("Pinned dataset download link is missing or changed")
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
            and not soup.select_one(
                '[data-evidence-state="' + manifest.get("mode", "compatibility") + '"]'
            )
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
            if u.fragment and not BeautifulSoup(
                target.read_text(encoding="utf-8"), "html.parser"
            ).find(id=u.fragment):
                raise ValueError("Broken evidence anchor")
    if manifest.get("mode") != "full-cohort-progress":
        rows = [read_json(output / view["path"]) for view in views]
        methods = sorted({row["method"] for row in rows})
        names = manifest.get("method_labels", {})
        for lang in manifest["languages"]:
            home_view = BeautifulSoup(
                (output / lang / "index.html").read_text(encoding="utf-8"), "html.parser"
            )
            expected = BeautifulSoup(
                operating_metrics(methods, rows, manifest.get("prices", {}), lang, names),
                "html.parser",
            ).select_one("[data-operating-metrics]")
            for anchor in expected.select('a[href="accounts.html"]'):
                anchor["href"] = lang + "/accounts.html"
            if home_view.select_one("[data-operating-metrics]") != expected:
                raise ValueError("Home latency/pricing differs from retained calls")
            account_view = BeautifulSoup(
                (output / lang / "accounts.html").read_text(encoding="utf-8"), "html.parser"
            )
            expected_price = BeautifulSoup(
                price_table(methods, manifest.get("prices", {}), lang, names), "html.parser"
            ).select_one("[data-price-table]")
            if account_view.select_one("[data-price-table]") != expected_price:
                raise ValueError("Cost/pricing coverage differs from bound source")
    if manifest.get("mode") == "full-cohort-progress":
        methods = manifest["planned_methods"]
        names = manifest.get("method_labels", {})
        data = {m: read_json(output / "public-evidence" / (m + ".json")) for m in methods}
        scores = {
            m: b["scores"][m]
            for b in manifest["source_runs"]
            if b.get("certified")
            for m in b["scores"]
        }
        if sum(len(v) for v in data.values()) != manifest["observations"]:
            raise ValueError("Published observations differ from evidence")
        for lang in manifest["languages"]:
            soup = BeautifulSoup(
                (output / lang / "index.html").read_text(encoding="utf-8"), "html.parser"
            )
            summary = soup.select('[data-core-metrics="full-cohort"] tbody tr')
            if [r.get("data-method") for r in summary] != methods:
                raise ValueError("Missing or reordered planned participant")
            for method, row in zip(methods, summary, strict=True):
                cells = row.select("td")
                expected = [
                    manifest["case_count"],
                    len(data[method]),
                    sum(
                        r.get("compatibility") == "structured_output" for r in data[method].values()
                    ),
                ]
                if [c.get_text(strip=True) for c in cells[1:4]] != [str(x) for x in expected]:
                    raise ValueError("Full cohort progress count differs")
                for field, cell in zip(
                    ("price", "sku", "availability", "OutOfStock"), cells[5:], strict=True
                ):
                    value = scores.get(method, {}).get(field, {}).get("f1")
                    if cell.get_text(strip=True) != ("—" if value is None else f"{value:.3f}"):
                        raise ValueError("Displayed quality differs from certified scores")
            protocol = BeautifulSoup(
                (output / lang / "protocol.html").read_text(encoding="utf-8"), "html.parser"
            )
            accounts = BeautifulSoup(
                (output / lang / "accounts.html").read_text(encoding="utf-8"), "html.parser"
            )
            expected_prices = BeautifulSoup(
                price_table(methods, manifest.get("prices", {}), lang, names), "html.parser"
            )
            price_views = accounts.select("[data-price-table]")
            if len(price_views) != 1 or price_views[0] != expected_prices.select_one(
                "[data-price-table]"
            ):
                raise ValueError("Cost/pricing coverage or terms differ from the bound source")
            rows = [r for method in methods for r in data[method].values()]
            expected_home = BeautifulSoup(
                operating_metrics(methods, rows, manifest.get("prices", {}), lang, names),
                "html.parser",
            ).select_one("[data-operating-metrics]")
            for anchor in expected_home.select('a[href="accounts.html"]'):
                anchor["href"] = lang + "/accounts.html"
            if soup.select_one("[data-operating-metrics]") != expected_home:
                raise ValueError("Home latency/pricing differs from retained calls")
            _, expected_time = cohort_metrics(
                methods, rows, manifest["source_runs"], manifest["case_count"], lang, names
            )
            for selector, expected_html in [
                ("[data-latency-table]", expected_time),
                ("[data-usage-table]", usage_table(methods, rows, lang, names)),
            ]:
                observed_tables = protocol.select(selector)
                expected_table = BeautifulSoup(expected_html, "html.parser").select_one(selector)
                if len(observed_tables) != 1 or [
                    c.get_text(strip=True) for c in observed_tables[0].select("td")
                ] != [c.get_text(strip=True) for c in expected_table.select("td")]:
                    raise ValueError("Timing or usage values differ from retained calls")
            inventory = BeautifulSoup(
                (output / lang / "results.html").read_text(encoding="utf-8"), "html.parser"
            )
            if manifest.get("review"):
                reviewed = BeautifulSoup(
                    (output / lang / "review.html").read_text(encoding="utf-8"), "html.parser"
                )
                interpretations = BeautifulSoup(
                    (output / lang / "interpretation.html").read_text(encoding="utf-8"),
                    "html.parser",
                )
                quality_rows = reviewed.select("[data-audited-quality] tbody tr")
                compact_rows = interpretations.select("[data-interpretation-comparison] tbody tr")
                if [r.get("data-method") for r in compact_rows] != methods:
                    raise ValueError("Missing compact interpretation comparison")
                for method, row in zip(methods, compact_rows, strict=True):
                    if [c.get_text(strip=True) for c in row.select("td")[2:]] != [
                        f"{scores[method][field]['f1']:.3f}"
                        for field in ("price", "sku", "availability", "OutOfStock")
                    ]:
                        raise ValueError(
                            "Interpretation scores differ from original-reference scores"
                        )
                if not reviewed.select_one("[data-complete-review]") or len(quality_rows) != len(
                    methods
                ):
                    raise ValueError("Missing complete audit summary or diagnostic quality")
                check_audited_quality(
                    reviewed, methods, manifest["audited_scores"], manifest["case_count"], lang
                )
                if {
                    s["data-method-interpretation"]
                    for s in interpretations.select("[data-method-interpretation]")
                } != set(methods):
                    raise ValueError("Incomplete method interpretation coverage")
            if not inventory.select_one("[data-result-controls]") or not inventory.select_one(
                "[data-diagnosis-body]"
            ):
                raise ValueError("Missing result filters or diagnosis drilldown")
            for field in ("status", "method", "field", "stage", "query"):
                if not inventory.select_one(f'[data-result-filter="{field}"]'):
                    raise ValueError("Incomplete result filters")
            if len(inventory.select("tr[data-status]")) != len(methods) * manifest["case_count"]:
                raise ValueError("Full planned result inventory is incomplete")
            if (
                len(inventory.select('tr[data-status="pending"]'))
                != len(methods) * manifest["case_count"] - manifest["observations"]
            ):
                raise ValueError("Pending population differs")
            case_ids = {cid for values in data.values() for cid in values}
            for cid in case_ids:
                source = BeautifulSoup(
                    (output / lang / f"source-{cid}.html").read_text(encoding="utf-8"),
                    "html.parser",
                )
                comparison = BeautifulSoup(
                    (output / lang / f"case-{cid}.html").read_text(encoding="utf-8"), "html.parser"
                )
                if not source.select_one("[data-source-reference]") or not source.select_one(
                    "[data-source-outline]"
                ):
                    raise ValueError("Missing inspectable source or annotation")
                expected_methods = [m for m in methods if cid in data[m]]
                if len(comparison.select("[data-case-comparison] tbody tr")) != len(
                    expected_methods
                ):
                    raise ValueError("Incomplete same-case method comparison")
                for method in expected_methods:
                    row = data[method][cid]
                    detail = BeautifulSoup(
                        (output / lang / f"result-{method}-{cid}.html").read_text(encoding="utf-8"),
                        "html.parser",
                    )
                    expected_fields = BeautifulSoup(
                        field_table(row, lang), "html.parser"
                    ).select_one("[data-field-diagnostics]")
                    if detail.select_one("[data-field-diagnostics]") != expected_fields:
                        raise ValueError("Field diagnostics differ from retained result")
                    if manifest.get("review"):
                        expected_audited = BeautifulSoup(
                            field_table(
                                {**row, "field_results": row["review"]["audited_fields"]}, lang
                            ),
                            "html.parser",
                        ).select_one("[data-field-diagnostics]")
                        expected_audited.attrs.pop("data-field-diagnostics")
                        expected_audited["data-audited-fields"] = "true"
                        if detail.select_one("[data-audited-fields]") != expected_audited:
                            raise ValueError("Audited result details differ")
    en = {p.relative_to(output / "en").as_posix() for p in (output / "en").glob("*.html")}
    zh = {p.relative_to(output / "zh").as_posix() for p in (output / "zh").glob("*.html")}
    if "en" in manifest["languages"] and en != zh:
        raise ValueError("Language route mismatch")
    if "en" not in manifest["languages"]:
        if en != {"index.html"} or not BeautifulSoup(
            (output / "en/index.html").read_text(encoding="utf-8"), "html.parser"
        ).select_one("[data-language-notice] a[href='zh/index.html']"):
            raise ValueError("Missing English notice or stale English pages")
    if release:
        check_visual_receipt(output, manifest, visual_receipt)

    return {
        "pages_per_language": len(zh),
        "observations": manifest["observations"],
        "state": manifest.get("mode", "compatibility"),
        "files": len(actual),
        "release": release,
    }


def check_visual_receipt(output, manifest, visual_receipt):
    """Verify the shared reader-pass receipt against this exact report tree."""
    if visual_receipt is None:
        raise ValueError("Release requires actual visual coverage")
    visual = read_json(visual_receipt)
    if visual["report_manifest_sha256"] != digest((output / "report-manifest.json").read_bytes()):
        raise ValueError("Visual receipt belongs to a different report")
    required = {(lang, route, "desktop") for lang in manifest["languages"] for route in PAGES}
    if "en" not in manifest["languages"]:
        required.add(("en", "english-notice", "desktop"))
    observed = set()
    for item in visual["observations"]:
        width = item["viewport_width"]
        if (item["viewport"] == "narrow" and not 320 <= width <= 480) or (
            item["viewport"] == "desktop" and width < 1000
        ):
            raise ValueError("Visual viewport dimensions do not match the declared class")
        screenshot = Path(item["screenshot"])
        if screenshot.is_symlink() or digest(screenshot.read_bytes()) != item["screenshot_sha256"]:
            raise ValueError("Visual evidence changed")
        raw = screenshot.read_bytes()
        if image_width(raw) != width:
            raise ValueError("Screenshot width does not match the observed viewport")
        if not item["heading"] or item["document_width"] > item["viewport_width"] + 1:
            raise ValueError("Missing heading or overflowing report viewport")
        observed.add((item["language"], item["route_class"], item["viewport"]))
    if not required.issubset(observed):
        raise ValueError("Incomplete top-level reader coverage")


def package(output, archive, *, visual_receipt):
    """Package only the release-checked report inventory, with no private siblings."""
    from harness.reporting.publication import package_archive

    require_external_output(archive)
    manifest = read_json(output / "report-manifest.json")
    names = [*sorted(manifest["files"]), "report-manifest.json"]
    return package_archive(
        output,
        archive,
        archive.with_suffix(".receipt.json"),
        inputs=(visual_receipt,),
        validate=lambda: check(output, release=True, visual_receipt=visual_receipt),
        attest=lambda path: {
            "files": len(names),
            "archive_bytes": path.stat().st_size,
            "expanded_bytes": sum((output / name).stat().st_size for name in names),
            "archive_sha256": digest(path.read_bytes()),
            "report_manifest_sha256": digest((output / "report-manifest.json").read_bytes()),
        },
    )


def record_cohort_inputs(home, model, *, profile):
    """Load a field-schema cohort through the existing certified/checkpoint owner."""
    from harness.web_scraper.analysis import record_statistics
    from harness.web_scraper.dataset import benchmark_config
    from harness.web_scraper.pipeline import selected_profile

    adapter = benchmark_config(home)["adapter"]
    if adapter is None:
        raise ValueError("Field-schema reports require a registered record scorer")
    local_example = read_json(profile).get("local_example", False)
    validate_dataset(home, profile if local_example else None)
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    methods = model["participants"]
    selections = model["evidence_runs"]
    if not methods or len(selections) != len(methods):
        raise ValueError("Every planned participant needs its complete source or checkpoint")
    if digest(Path(model["dataset_manifest"]).read_bytes()) != digest(
        (home / "dataset/manifest.json").read_bytes()
    ):
        raise ValueError("Report uses a different dataset manifest")
    result = {
        "cases": truth,
        "methods": {},
        "sources": {},
        "dataset": read_json(profile if local_example else selected_profile(home)),
    }
    seen = set()
    for selection in selections:
        source = Path(selection["run"])
        source = source if source.is_absolute() else home / source
        contract, rows, receipt = cohort_source(source, selection, home, truth)
        method = contract["methods"][0]
        if method not in methods or method in seen:
            raise ValueError("Unexpected or duplicated report participant")
        seen.add(method)
        if contract["dataset_manifest_sha256"] != digest(
            (home / "dataset/manifest.json").read_bytes()
        ):
            raise ValueError("Measured source belongs to a different dataset")
        stats = record_statistics(truth, rows, certified=receipt["certified"], scorer=adapter.score)
        if stats["certified"] and receipt["scores"][method] != stats["scores"]:
            raise ValueError("Record report scores differ from the certified scorer")
        result["methods"][method] = {"statistics": stats, "results": {}}
        result["sources"][method] = {
            "run_id": source.name,
            "contract": contract,
            "receipt": receipt,
            "plan_sha256": selection["plan_sha256"],
        }
        for row in rows:
            current = dict(row)
            current["field_results"] = field_results(home, truth[row["case_id"]], row)
            current["failure_stage"] = failure_stage(row)
            current["native_error"] = native_error(row)
            current["exact_fields"] = sum(
                value["exact"] for value in current["field_results"].values()
            )
            current["referenced_fields"] = sum(
                bool(value["expected"]) for value in current["field_results"].values()
            )
            current["hit_fields"] = sum(value["hit"] for value in current["field_results"].values())
            current["status"] = (
                "call_error"
                if row.get("compatibility") != "structured_output"
                else "exact"
                if all(value["exact"] for value in current["field_results"].values())
                else "field_difference"
            )
            result["methods"][method]["results"][row["case_id"]] = current
    if seen != set(methods):
        raise ValueError("Report omits a planned participant")
    return result


def record_route(kind, *identities):
    return kind + "-" + digest("\0".join(identities).encode())[:24]


def record_comparison_scope(participants):
    """Keep explicit omissions separate from immutable collected evidence."""
    compared, skipped = [], {}
    for method, item in participants.items():
        included = item.get("include_in_comparison", True)
        if type(included) is not bool:
            raise ValueError("Comparison inclusion must be an explicit boolean")
        if included:
            compared.append(method)
        else:
            reason = item.get("exclusion_reason", "")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("A skipped participant requires its reason")
            skipped[method] = reason.strip()
    if not compared:
        raise ValueError("A comparison requires at least one participant")
    return compared, skipped


def record_result_href(method, case_id):
    """One canonical case page, with an addressable panel for each participant."""
    return record_route("c", case_id) + ".html#" + method


def record_comparison(headers, rows, metrics):
    """Use the common table owner and raw-value extrema checks for each metric."""
    soup = BeautifulSoup(table(headers, rows, attrs="data-sortable"), "html.parser")
    for column, (name, direction, values) in metrics.items():
        observed = []
        for row, value in zip(soup.select("tbody tr"), values, strict=True):
            cell = row.select("td")[column]
            cell.attrs.update({"data-metric": name, "data-metric-direction": direction})
            if value is not None:
                cell["data-sort"] = str(value)
                observed.append((float(value), cell))
        if observed and direction != "neutral":
            extreme = (min if direction == "min" else max)(value for value, _ in observed)
            for value, cell in observed:
                if value == extreme:
                    span = soup.new_tag("span", attrs={"class": "comparison-winner"})
                    span.string = cell.get_text()
                    cell.clear()
                    cell.append(span)
    return str(soup)


def build_record_cohort(output, content, *, home, language, profile):
    """Render registered record schemas through the shared report layout and controls."""
    from collections import Counter

    from harness.web_scraper.dataset import benchmark_config

    require_external_output(output)
    model = read_json(content)
    bound_profile = read_json(profile)
    if bound_profile.get("research_report", {}).get("content", {}).get("sha256") != digest(
        content.read_bytes()
    ):
        raise ValueError("Record report content is not bound to its instance profile")
    language = resolve_report_language(output, language)
    if language != "zh":
        raise ValueError(
            "This cohort has Chinese authored content only; generate English on request"
        )
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty report directory")
    data = record_cohort_inputs(home, model, profile=profile)
    methods = list(model["participants"])
    compared, skipped = record_comparison_scope(model["participants"])
    labels = {method: model["participants"][method]["label"] for method in methods}
    truth = data["cases"]
    stats = {method: data["methods"][method]["statistics"] for method in methods}
    title = model["title"]

    def t(key):
        return message("records_" + key, language)

    output.mkdir(parents=True)
    (output / "zh").mkdir()
    (output / "en").mkdir()
    (output / "data").mkdir()
    (output / "public-evidence").mkdir()
    (output / "style.css").write_text(CSS, encoding="utf-8")
    (output / "app.js").write_text(JS, encoding="utf-8")
    copy_report_assets(output, content, model)
    prices = model["prices"]
    if set(prices) != set(methods):
        raise ValueError("Every participant requires a public pricing contract")
    public_receipts = []
    for method in methods:
        bucket = data["methods"][method]["results"]
        for cid, row in bucket.items():
            row["method_label"] = labels[method]
            row["record_schema"] = True
            row["field_labels"] = model.get("field_labels", {})
            row["result_href"] = "zh/" + record_result_href(method, cid)
            row["case_href"] = "zh/" + record_route("c", cid) + ".html"
            if isinstance(row.get("request"), dict):
                row["request"]["endpoint"] = re.sub(
                    r"/accounts/[^/]+/", "/accounts/[REDACTED]/", row["request"].get("endpoint", "")
                )
        raw, receipt = project_evidence_view(
            "public-evidence/" + method + ".json",
            json.dumps(bucket, ensure_ascii=False).encode(),
            role="native_response",
            content_format="json",
            host_names=publication_host_names(),
        )
        after = json.loads(raw)
        (output / "public-evidence" / (method + ".json")).write_bytes(raw)
        data["methods"][method]["results"] = after
        public_receipts.append(receipt)
    reference_raw, reference_receipt = project_evidence_view(
        "public-evidence/reference.json",
        json.dumps(truth, ensure_ascii=False).encode(),
        role="scored_answer",
        content_format="json",
        host_names=publication_host_names(),
    )
    (output / "public-evidence/reference.json").write_bytes(reference_raw)
    public_receipts.append(reference_receipt)
    truth = json.loads(reference_raw)
    from harness.web_scraper.analysis import record_statistics

    adapter = benchmark_config(home)["adapter"]
    for method in methods:
        if (
            record_statistics(
                truth,
                list(data["methods"][method]["results"].values()),
                certified=stats[method]["certified"],
                scorer=adapter.score,
            )
            != stats[method]
        ):
            raise ValueError("Public evidence projection changed measured verdicts or metrics")
    save_json(output / "data/metrics.json", stats)
    source_raw, source_receipt = project_evidence_view(
        "public-evidence/sources.json",
        json.dumps(data["sources"], ensure_ascii=False).encode(),
        role="native_response",
        content_format="json",
        host_names=publication_host_names(),
    )
    (output / "public-evidence/sources.json").write_bytes(source_raw)
    public_receipts.append(source_receipt)

    def route_link(route, label):
        return '<a href="' + route + '.html">' + esc(label) + "</a>"

    def write(route, heading, body):
        (output / "zh" / (route + ".html")).write_text(
            page(
                language,
                route,
                heading,
                body,
                detail=route not in PAGES,
                languages=("zh",),
                state="field-extraction-cohort",
            ),
            encoding="utf-8",
        )

    def value(value):
        return esc(json.dumps(value, ensure_ascii=False))

    def score(method, *keys):
        current = stats[method]["scores"] if method in compared else None
        for key in keys:
            current = current.get(key) if current is not None else None
        return current

    def metric(number, percent=False):
        return "—" if number is None else f"{number * 100:.2f}%" if percent else f"{number:.3f}"

    def evidence_button(method, cid, dialog_id="diagnosis"):
        return (
            '<button data-call-log-open="'
            + esc(dialog_id)
            + '" data-src="../public-evidence/'
            + esc(method)
            + '.json" data-key="'
            + esc(cid)
            + '">'
            + t("open")
            + "</button>"
        )

    dialog = (
        '<dialog id="diagnosis" aria-labelledby="diagnosis-title"><h2 id="diagnosis-title">'
        + t("result")
        + "</h2><button data-call-log-close>"
        + LABELS[language][10]
        + "</button><div data-diagnosis-body></div></dialog>"
    )
    status_labels = {
        "exact": t("exact_page"),
        "field_difference": t("field_difference"),
        "call_error": t("call_error"),
    }
    headers = [
        t(key) for key in ("method", "executed", "returned", "macro", "micro", "exact", "details")
    ]
    core_rows = [
        [
            esc(labels[m]),
            f"{stats[m]['executed']} / {len(truth)}",
            str(stats[m]["structured"]),
            metric(score(m, "page_hit_macro", "f1")),
            metric(score(m, "value_micro", "f1")),
            metric(score(m, "exact_field_sets", "rate"), True),
            route_link("interpretation#" + m, t("interpretation")),
        ]
        for m in methods
    ]
    # Fragment links need the fragment after the filename.
    for m, row in zip(methods, core_rows, strict=True):
        row[-1] = '<a href="interpretation.html#' + esc(m) + '">' + t("interpretation") + "</a>"
    core = record_comparison(
        headers,
        core_rows,
        {
            3: ("macro-f1", "max", [score(m, "page_hit_macro", "f1") for m in methods]),
            4: ("micro-f1", "max", [score(m, "value_micro", "f1") for m in methods]),
            5: ("exact-rate", "max", [score(m, "exact_field_sets", "rate") for m in methods]),
        },
    )
    operations = operating_metrics(
        methods,
        [row for m in methods for row in data["methods"][m]["results"].values()],
        prices,
        language,
        labels,
        compared=compared,
    )
    bodies = {route: model.get("pages", {}).get(route, {}).get("body", "") for route in PAGES}
    bodies["index"] = (
        "<section data-core-metrics>" + core + "</section>" + bodies["index"] + operations
    )
    domains = Counter(ref["domain"] for ref in truth.values())
    sites = Counter((ref["domain"], ref["site"]) for ref in truth.values())
    domain_labels = model.get("domain_labels", {})
    domain_rows = [
        [
            esc(domain_labels.get(d, d)),
            str(n),
            str(sum(category == d for category, _ in sites)),
            esc(" / ".join(next(ref["values"] for ref in truth.values() if ref["domain"] == d))),
        ]
        for d, n in sorted(domains.items())
    ]
    bodies["dataset"] += table([t("domain"), t("count"), t("site"), t("fields")], domain_rows)
    bodies["dataset"] += table(
        [t("case"), t("domain"), t("site"), t("source")],
        [
            [
                esc(cid),
                esc(domain_labels.get(ref["domain"], ref["domain"])),
                esc(ref["site"]),
                route_link(record_route("d", cid), t("source")),
            ]
            for cid, ref in sorted(truth.items())
        ],
        inventory=True,
    )
    domain_scores = []
    for domain, count in sorted(domains.items()):
        domain_scores.append(
            [
                esc(domain_labels.get(domain, domain)) + f" (n={count})",
                *[metric(score(m, "domains", domain, "f1")) for m in methods],
            ]
        )
    bodies["results"] = (
        table([t("domain"), *map(esc, labels.values())], domain_scores) + bodies["results"]
    )
    group_keys = sorted(
        {(ref["domain"], ref["site"], field) for ref in truth.values() for field in ref["values"]}
    )
    group_scores = {
        method: {
            (item["domain"], item["site"], item["field"]): item
            for item in ((stats[method]["scores"] or {}) if method in compared else {}).get(
                "site_fields", []
            )
        }
        for method in methods
    }
    bodies["results"] += (
        "<details><summary>"
        + t("site")
        + " / "
        + t("field")
        + " F1</summary>"
        + table(
            [t("domain"), t("site"), t("field"), t("count"), *labels.values()],
            [
                [
                    esc(domain_labels.get(domain, domain)),
                    esc(site),
                    esc(field),
                    str(
                        sum(
                            ref["domain"] == domain and ref["site"] == site
                            for ref in truth.values()
                        )
                    ),
                    *[
                        (
                            '<a href="results.html?status=all&amp;domain='
                            + domain
                            + "&amp;site="
                            + site
                            + "&amp;field="
                            + field
                            + "&amp;method="
                            + method
                            + '">'
                            + metric(group_scores[method][(domain, site, field)]["f1"])
                            + "</a>"
                        )
                        if (domain, site, field) in group_scores[method]
                        else "—"
                        for method in methods
                    ],
                ]
                for domain, site, field in group_keys
            ],
            inventory=True,
        )
        + "</details>"
    )
    bodies["protocol"] += usage_table(
        methods,
        [row for method in methods for row in data["methods"][method]["results"].values()],
        language,
        labels,
    )
    controls = (
        '<form data-result-controls class="controls"><select data-result-filter="status"><option value="fail" selected>'
        + t("field_difference")
        + '</option><option value="all">'
        + LABELS[language][13]
        + '</option><option value="pass">'
        + t("exact_page")
        + '</option></select><select data-result-filter="method"><option value="">'
        + t("method")
        + "</option>"
        + "".join('<option value="' + esc(m) + '">' + esc(labels[m]) + "</option>" for m in methods)
        + "</select>"
        + "".join(
            '<select data-result-filter="'
            + key
            + '"><option value="">'
            + t(key)
            + "</option>"
            + "".join(
                '<option value="'
                + esc(v)
                + '">'
                + esc(domain_labels.get(v, v) if key == "domain" else v)
                + "</option>"
                for v in values
            )
            + "</select>"
            for key, values in [
                ("domain", sorted(domains)),
                ("site", sorted({r["site"] for r in truth.values()})),
                ("field", sorted({f for r in truth.values() for f in r["values"]})),
            ]
        )
        + '<input type="search" data-result-filter="query" aria-label="'
        + t("case")
        + '"></form><p data-result-count></p>'
    )
    inventory_rows = []
    snapshot_base = model.get("snapshot_base")
    if not snapshot_base:
        raise ValueError("Report requires a hash-bound frozen input preview origin")
    for cid, ref in sorted(truth.items()):
        data_route, comparison = record_route("d", cid), record_route("c", cid)
        schema = benchmark_config(home)["adapter"].task_schema(home, ref)
        source = (
            "<p>"
            + esc(domain_labels.get(ref["domain"], ref["domain"]))
            + " · "
            + esc(ref["site"])
            + "</p>"
        )
        source += table(
            [t("field"), t("reference")],
            [[esc(field), value(gold)] for field, gold in ref["values"].items()],
        )
        source += (
            "<details><summary>"
            + t("schema")
            + "</summary><pre>"
            + value(schema)
            + "</pre></details>"
        )
        source += (
            '<p><a href="'
            + esc(ref["url"])
            + '">'
            + t("original_url")
            + '</a> · <a href="'
            + esc(snapshot_base + "/" + cid)
            + '">'
            + t("download")
            + '</a></p><iframe sandbox class="frozen-preview" loading="lazy" title="'
            + t("preview")
            + '" src="'
            + esc(snapshot_base + "/" + cid)
            + '"></iframe>'
        )
        write(data_route, cid, source)
        comparisons, panels = [], []
        for method in methods:
            row = data["methods"][method]["results"].get(cid)
            result_href = record_result_href(method, cid)
            result_link = '<a href="' + esc(result_href) + '">' + t("details") + "</a>"
            panel_start = (
                '<details data-result-panel id="'
                + esc(method)
                + '"><summary>'
                + esc(labels[method])
                + "</summary>"
            )
            if row is None:
                comparisons.append(
                    [
                        esc(labels[method]),
                        t("pending"),
                        "—",
                        "—",
                        result_link,
                    ]
                )
                panels.append(
                    panel_start
                    + "<section data-result-summary>"
                    + t("pending")
                    + "</section></details>"
                )
                inventory_rows.append(
                    '<tr data-status="pending" data-outcome="pending" data-method="'
                    + esc(method)
                    + '" data-domain="'
                    + esc(ref["domain"])
                    + '" data-site="'
                    + esc(ref["site"])
                    + '" data-search="'
                    + esc(cid + " " + ref["site"])
                    + '"><td>'
                    + esc(cid)
                    + "</td><td>"
                    + esc(labels[method])
                    + "</td><td>"
                    + esc(domain_labels.get(ref["domain"], ref["domain"]))
                    + "</td><td>"
                    + t("pending")
                    + "</td><td>—</td><td>—</td><td>"
                    + result_link
                    + "</td></tr>"
                )
                continue
            field_rows = [
                [
                    esc(field),
                    value(f["expected"]),
                    value(f["actual"]),
                    message("outcomes_" + f["status"], language),
                    t("yes") if f["hit"] else t("no"),
                    t("yes") if f["exact"] else t("no"),
                ]
                for field, f in row["field_results"].items()
            ]
            short = status_labels[row["status"]]
            comparisons.append(
                [
                    esc(labels[method]),
                    short,
                    str(row["exact_fields"]) + "/" + str(len(ref["values"])),
                    metric(row.get("elapsed_seconds")),
                    result_link,
                ]
            )
            body = (
                '<article data-report-detail="'
                + ("failed" if row["status"] != "exact" else "passed")
                + '"><section data-result-summary><p>'
                + esc(short)
                + " · HTTP "
                + esc(str(row.get("http_status")), quote=True)
                + " · "
                + metric(row.get("elapsed_seconds"))
                + " s · "
                + esc(STAGES[row["failure_stage"]][0])
                + "</p></section>"
            )
            body += (
                '<p class="case-links">'
                + route_link(comparison, t("all_methods"))
                + route_link(data_route, t("source"))
                + '<a href="interpretation.html#'
                + esc(method)
                + '">'
                + t("interpretation")
                + "</a>"
                + "</p>"
            )
            body += table(
                [t("field"), t("reference"), t("actual"), t("outcome"), t("hit"), t("equal")],
                field_rows,
            )
            body += (
                '<section data-failure-diagnosis data-result-logs id="result-logs-'
                + esc(method)
                + '"><h2>'
                + t("error")
                + "</h2><details><summary>HTTP "
                + esc(str(row.get("http_status")))
                + '</summary><pre data-kind="error">'
                + esc(
                    row["native_error"]
                    or json.dumps(
                        row.get("response")
                        if row["status"] == "call_error"
                        else {
                            field: f["status"]
                            for field, f in row["field_results"].items()
                            if not f["exact"]
                        },
                        ensure_ascii=False,
                    )
                )
                + "</pre></details></section>"
            )
            body += (
                evidence_button(method, cid, "diagnosis-" + method)
                + '<a href="../public-evidence/'
                + esc(method)
                + '.json">'
                + t("raw")
                + "</a>"
                + dialog.replace('id="diagnosis"', 'id="diagnosis-' + esc(method) + '"').replace(
                    "diagnosis-title", "diagnosis-" + method + "-title"
                )
                + "</article>"
            )
            panels.append(panel_start + body + "</details>")
            attrs = (
                ' data-status="'
                + ("pass" if row["status"] == "exact" else "fail")
                + '" data-outcome="'
                + ("correct" if row["status"] == "exact" else "wrong")
                + '" data-method="'
                + esc(method)
                + '" data-stage="'
                + esc(row["failure_stage"])
                + '" data-search="'
                + esc(" ".join([cid, ref["domain"], ref["site"], labels[method]]))
                + '"'
            )
            attrs += (
                ' data-domain="'
                + esc(ref["domain"])
                + '" data-site="'
                + esc(ref["site"])
                + '"'
                + "".join(
                    " data-" + field + '="' + result["status"] + '"'
                    for field, result in row["field_results"].items()
                )
            )
            inventory_rows.append(
                "<tr"
                + attrs
                + "><td>"
                + esc(cid)
                + "</td><td>"
                + esc(labels[method])
                + "</td><td>"
                + esc(domain_labels.get(ref["domain"], ref["domain"]))
                + "</td><td>"
                + esc(short)
                + "</td><td>"
                + str(row["exact_fields"])
                + "/"
                + str(len(ref["values"]))
                + "</td><td>"
                + metric(row.get("elapsed_seconds"))
                + "</td><td>"
                + evidence_button(method, cid)
                + " "
                + result_link
                + "</td></tr>"
            )
        write(
            comparison,
            cid + " · " + t("all_methods"),
            table([t("method"), t("outcome"), t("exact"), t("timing"), t("details")], comparisons)
            + route_link(data_route, t("source"))
            + "".join(panels),
        )
    bodies["results"] += (
        controls
        + '<div class="table-wrap inventory"><table data-sortable><thead><tr>'
        + "".join(
            "<th>" + t(k) + "</th>"
            for k in ("case", "method", "domain", "outcome", "exact", "timing", "details")
        )
        + "</tr></thead><tbody>"
        + "".join(inventory_rows)
        + "</tbody></table></div>"
        + dialog
    )
    bodies["interpretation"] = (
        "<section data-interpretation-comparison>" + core + "</section>" + bodies["interpretation"]
    )
    for method in methods:
        current = stats[method]
        chapter = '<section id="' + esc(method) + '"><h2>' + esc(labels[method]) + "</h2>"
        chapter += model["participants"][method].get("interpretation", "")
        chapter += (
            "<h3>"
            + t("diagnostics")
            + "</h3>"
            + table(
                [t("outcome"), t("count")],
                [
                    [message("outcomes_" + k, language), str(v)]
                    for k, v in sorted(current["field_outcomes"].items())
                ],
            )
        )
        chapter += table(
            [t("outcome"), t("count")],
            [[STAGES[k][0], str(v)] for k, v in sorted(current["failure_stages"].items())],
        )
        specimen = model["participants"][method].get("specimen")
        if specimen:
            chapter += (
                '<a href="'
                + esc(record_result_href(method, specimen))
                + '">'
                + t("result")
                + "</a>"
            )
        bodies["interpretation"] += chapter + "</section>"
    for route in PAGES:
        write(
            route,
            title
            + " · "
            + (
                message("results_and_failures", language)
                if route == "results"
                else EXTRA_LABELS[route][language]
                if route in EXTRA_LABELS
                else LABELS[language][PAGES.index(route)]
            ),
            bodies[route],
        )
    (output / "en/index.html").write_text(
        message("english_not_generated_notice", "en"), encoding="utf-8"
    )
    (output / "index.html").write_text(
        '<!doctype html><html><head><meta http-equiv="refresh" content="0;url=zh/index.html"></head><body><a href="zh/index.html">'
        + esc(title)
        + "</a></body></html>",
        encoding="utf-8",
    )
    version = bind_build_version(output)
    files = {
        p.relative_to(output).as_posix(): digest(p.read_bytes())
        for p in sorted(output.rglob("*"))
        if p.is_file()
    }
    save_json(
        output / "report-manifest.json",
        {
            "mode": "field-extraction-cohort",
            "local_example": bound_profile.get("local_example", False),
            "template": template_identity("web-scraper"),
            "template_version": VERSION,
            "browser_eval": version,
            "renderer_sha256": digest(Path(__file__).read_bytes()),
            "profile_sha256": digest(profile.read_bytes()),
            "content_sha256": digest(content.read_bytes()),
            "dataset_manifest_sha256": digest((home / "dataset/manifest.json").read_bytes()),
            "dataset_benchmark": read_json(home / "dataset/manifest.json").get("benchmark"),
            "primary_language": "zh",
            "languages": ["zh"],
            "planned_methods": methods,
            "comparison_methods": compared,
            "skipped_methods": skipped,
            "method_labels": labels,
            "prices": prices,
            "case_count": len(truth),
            "observations": sum(s["executed"] for s in stats.values()),
            "complete_methods": [m for m in methods if stats[m]["certified"]],
            "metrics_sha256": digest((output / "data/metrics.json").read_bytes()),
            "public_evidence_views": public_receipts,
            "files": files,
        },
        exclusive=True,
    )
    check_record_cohort(output)


def check_record_cohort(output, *, release=False, visual_receipt=None):
    """Check the public record matrix and shared presentation semantics."""
    from harness.reporting.presentation import check_presentation
    from harness.web_scraper.analysis import record_statistics
    from harness.web_scraper.dataset import benchmark_config

    output = open_report(output)
    manifest = read_json(output / "report-manifest.json")
    check_report_identity(output, manifest, "web-scraper")
    if manifest["renderer_sha256"] != digest(Path(__file__).read_bytes()):
        raise ValueError("Record renderer changed; rebuild required")
    actual = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    if actual != set(manifest["files"]) | {"report-manifest.json"}:
        raise ValueError("Record report contains missing or undeclared files")
    for name, expected in manifest["files"].items():
        path = output / name
        if path.is_symlink() or digest(path.read_bytes()) != expected:
            raise ValueError("Record report bytes changed: " + name)
    views = manifest.get("public_evidence_views", [])
    if {view["path"] for view in views} != {
        name for name in actual if name.startswith("public-evidence/")
    }:
        raise ValueError("Record public evidence view receipts are incomplete")
    for view in views:
        raw = (output / view["path"]).read_bytes()
        if (
            digest(raw) != view["published_sha256"]
            or len(raw) != view["published_bytes"]
            or public_risk(raw)
        ):
            raise ValueError("Record public evidence privacy receipt failed")
    metrics = read_json(output / "data/metrics.json")
    truth = read_json(output / "public-evidence/reference.json")
    if len(truth) != manifest["case_count"] or set(metrics) != set(manifest["planned_methods"]):
        raise ValueError("Record report matrix differs from its declared population")
    adapter = benchmark_config(name=manifest["dataset_benchmark"])["adapter"]
    for method in manifest["planned_methods"]:
        rows = list(read_json(output / "public-evidence" / (method + ".json")).values())
        recomputed = record_statistics(
            truth, rows, certified=method in manifest["complete_methods"], scorer=adapter.score
        )
        if recomputed != metrics[method]:
            raise ValueError("Record report metrics do not reproduce: " + method)
        for cid in truth:
            for route in (
                record_route("d", cid),
                record_route("c", cid),
            ):
                if not (output / "zh" / (route + ".html")).is_file():
                    raise ValueError(
                        "Record report is missing a source, comparison or result detail"
                    )
    expected_operations = BeautifulSoup(
        operating_metrics(
            manifest["planned_methods"],
            [
                row
                for method in manifest["planned_methods"]
                for row in read_json(output / "public-evidence" / (method + ".json")).values()
            ],
            manifest["prices"],
            "zh",
            manifest["method_labels"],
            compared=manifest["comparison_methods"],
        ),
        "html.parser",
    )
    home = BeautifulSoup((output / "zh/index.html").read_text(), "html.parser")
    if [row.get_text(strip=True) for row in home.select("[data-operating-metrics] tbody tr")] != [
        row.get_text(strip=True) for row in expected_operations.select("tbody tr")
    ]:
        raise ValueError("Record timing or pricing differs from measured evidence")
    for route, selector in (
        ("index", "[data-core-metrics]"),
        ("interpretation", "[data-interpretation-comparison]"),
    ):
        soup = BeautifulSoup((output / "zh" / (route + ".html")).read_text(), "html.parser")
        rendered = soup.select(selector + " tbody tr")
        if len(rendered) != len(metrics):
            raise ValueError("Record core comparison participant count differs")
        for method, row in zip(manifest["planned_methods"], rendered, strict=True):
            cells = row.select("td")
            current = metrics[method]
            scores = current["scores"] if method in manifest["comparison_methods"] else None
            expected = [
                manifest["method_labels"][method],
                str(current["executed"]) + " / " + str(len(truth)),
                str(current["structured"]),
                f"{scores['page_hit_macro']['f1']:.3f}" if scores else "—",
                f"{scores['value_micro']['f1']:.3f}" if scores else "—",
                f"{scores['exact_field_sets']['rate'] * 100:.2f}%" if scores else "—",
            ]
            if [cell.get_text(strip=True) for cell in cells[:6]] != expected:
                raise ValueError("Record core comparison values differ from measured evidence")
    inventory = BeautifulSoup((output / "zh/results.html").read_text(), "html.parser")
    rows = inventory.select("tr[data-status]")
    if len(rows) != len(truth) * len(metrics) or len(
        inventory.select('tr[data-status="pending"]')
    ) != sum(x["unexecuted"] for x in metrics.values()):
        raise ValueError("Record inventory omits planned or pending results")
    for method in manifest["planned_methods"]:
        bucket = read_json(output / "public-evidence" / (method + ".json"))
        for cid, row in bucket.items():
            detail = BeautifulSoup(
                (output / "zh" / (record_route("c", cid) + ".html")).read_text(),
                "html.parser",
            )
            detail = detail.find("details", id=method)
            if detail is None:
                raise ValueError("Record case page omits a participant detail")
            button = detail.select_one("[data-call-log-open]")
            if (
                button is None
                or button.get("data-key") != cid
                or button.get("data-src") != "public-evidence/" + method + ".json"
            ):
                raise ValueError("Record detail evidence points to a different result")
            if row["status"] == "call_error":
                error = detail.select_one('[data-result-logs] pre[data-kind="error"]')
                if error is None or (
                    row["native_error"] and error.get_text() != row["native_error"]
                ):
                    raise ValueError("Record native failure error is missing or changed")
    for file in sorted((output / "zh").glob("*.html")):
        soup = check_presentation(file.read_text())
        for node in soup.select("a[href],script[src],link[href]"):
            value = node.get("href", node.get("src", ""))
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = output / unquote(parsed.path)
            if not target.is_file():
                raise ValueError("Broken record evidence/navigation target: " + value)
    if release and manifest.get("local_example"):
        raise ValueError("Synthetic record evidence cannot be a formal release")
    compared = manifest.get("comparison_methods", [])
    skipped = manifest.get("skipped_methods", {})
    if (
        not compared
        or len(set(compared)) != len(compared)
        or set(compared) & set(skipped)
        or set(compared) | set(skipped) != set(manifest["planned_methods"])
        or any(not isinstance(reason, str) or not reason.strip() for reason in skipped.values())
    ):
        raise ValueError("Record comparison scope omits a participant or its reason")
    if release and not set(compared).issubset(manifest["complete_methods"]):
        raise ValueError("Formal record release requires every comparison participant complete")
    if release:
        check_visual_receipt(output, manifest, visual_receipt)
        observations = read_json(visual_receipt)["observations"]
        observed = {
            (item["route_class"], item["viewport"], item.get("theme")) for item in observations
        }
        required = {
            (route, viewport, theme)
            for route in (
                *PAGES,
                "success-detail",
                "failure-detail",
                "source-detail",
                "case-comparison",
            )
            for viewport in ("desktop", "narrow")
            for theme in ("light", "dark")
        }
        if not required.issubset(observed):
            raise ValueError("Incomplete record report detail, theme or viewport coverage")
    return manifest
