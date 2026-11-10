"""Bilingual research and input-compatibility reports for structured extraction."""

from __future__ import annotations

import json
import math
import re
import statistics
import struct
import zipfile
from collections import Counter
from html import escape as esc
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, Comment

from harness.distribution import repository_root
from harness.env_file import local_setting
from harness.record_io import canonical_json_sha256
from harness.report_languages import resolve_report_language
from harness.reporting.links import LinkIndex
from harness.reporting.release_check import check_report_identity, checked_page, open_report
from harness.reporting.templates import template_identity
from harness.reporting.versions import bind_build_version
from harness.web_extract.analysis import (
    check_product_audit,
    failure_stage,
    field_results,
    native_error,
)
from harness.web_extract.messages import message, messages_sha256, validate_catalog
from harness.web_extract.pipeline import (
    FIELDS,
    PRODUCT_FIELDS,
    PROFILE,
    available_methods,
    digest,
    execution_identity,
    load_run,
    normalize,
    read_json,
    require_external_output,
    resolve_run_path,
    save_json,
    score_rows,
    score_run,
    unsafe_parents,
    validate_dataset,
    verify_execution_snapshot,
    verify_retry_history,
)
from harness.web_extract.report_ui import JS
from harness.web_fetch.publication_privacy import project_evidence_view, publication_risk

VERSION = "web-extract-research/1"
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


def table(headers, rows, *, inventory=False, attrs="", sort_values=None, pin_method=False):
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
        + '" tabindex="0"><table '
        + attrs
        + (' data-method-column="true"' if pin_method else "")
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
    language,
    route,
    title,
    body,
    *,
    detail=False,
    languages=("en", "zh"),
    state="compatibility",
    navigation=None,
    notice=None,
    intro="",
):
    if intro:
        fragment = BeautifulSoup(body, "html.parser")
        introduction = BeautifulSoup(
            "<section data-page-intro>" + intro + "</section>", "html.parser"
        ).section
        if route == "index" and state in ("full-cohort-progress", "field-extraction-cohort"):
            anchor = home_comparison_end(fragment)
            if anchor is None:
                raise ValueError("Report home intro requires its core comparison")
            anchor.insert_after(introduction)
        else:
            fragment.insert(0, introduction)
        body = str(fragment)
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
    if notice is not None:
        labels = list(labels)
        labels[7] = notice
    other = "zh" if language == "en" else "en"
    primary_routes = ("index", "results", "dataset", "protocol", "interpretation")
    entries = (
        navigation
        if navigation is not None
        else [
            {
                "route": p,
                "label": EXTRA_LABELS[p][language] if p in EXTRA_LABELS else labels[PAGES.index(p)],
                "primary": p in primary_routes,
            }
            for p in (*primary_routes, *(p for p in PAGES if p not in primary_routes))
        ]
    )
    if (
        (state != "field-extraction-suite" and (
            len(entries) != len(PAGES)
            or {entry.get("route") for entry in entries} != set(PAGES)
        ))
        or not entries
        or len({entry.get("route") for entry in entries}) != len(entries)
        or any(
            not isinstance(entry.get("label"), str) or not entry["label"].strip()
            for entry in entries
        )
    ):
        raise ValueError("Navigation must name every report page exactly once")

    def nav_link(entry):
        p = entry["route"]
        return (
            f'<a href="{p}.html"'
            + (' aria-current="page"' if route == p else "")
            + ">"
            + esc(entry["label"])
            + "</a>"
        )

    nav = "".join(nav_link(entry) for entry in entries if entry.get("primary", True))
    secondary = [entry for entry in entries if not entry.get("primary", True)]
    if secondary:
        nav += (
            '<details class="nav-more"'
            + (" open" if route in {e["route"] for e in secondary} else "")
            + "><summary>"
            + message("more_resources", language)
            + "</summary><div>"
            + "".join(nav_link(entry) for entry in secondary)
            + "</div></details>"
        )
    other_route = route if not detail else "results"
    if other not in languages:
        other_route = "index"
    html = '<!doctype html><html lang="{part0}"><head><base href="../"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{part1} · Web Extract</title><link rel="stylesheet" href="../style.css"></head><body><header><div class="eyebrow">WEB EXTRACT / STRUCTURED EXTRACTION</div><h1>{part2}</h1><p class="notice" data-evidence-state="{part3}">{part4}</p><div class="report-toolbar"><nav>{part5}</nav><div class="report-tools"><a href="../{part6}/{part7}.html">{part8}</a><button id="theme">{part9}</button></div></div></header><main>{part10}</main><footer>Web Extract · {part11} · <a href="../report-manifest.json">{part12}</a></footer><script src="../app.js"></script></body></html>'.format(
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
        part11=("评测方法与数据版本" if language == "zh" else "Methodology and dataset versions"),
        part12=message("input_and_file_bindings", language),
    )
    # Artifact Site injects a site-root base even on nested pages. Use the same
    # root-relative references locally, where our own base supplies that root.
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.select("a[href]"):
        parsed = urlsplit(anchor["href"])
        if parsed.scheme in {"http", "https"} or parsed.netloc:
            anchor["target"] = "_blank"
            anchor["rel"] = ["noopener", "noreferrer"]
    for element in soup.select("a[href],link[href],script[src],img[src],[data-src]"):
        for attr in ("href", "src", "data-src"):
            value = element.get(attr)
            if value is not None and not urlsplit(value).scheme and not urlsplit(value).netloc:
                base = (
                    "/" if state == "field-extraction-suite" and urlsplit(value).path
                    else f"/{language}/{route}.html"
                )
                element[attr] = urljoin(base, value).lstrip("/")
    return scope_record_html(str(soup), site_prefix)


def scope_record_html(raw, prefix, *, restore=False):
    """Keep owned links working under the hosted site's root base element."""
    if not prefix:
        return raw
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", prefix):
        raise ValueError("Report prefix must be a single safe directory")
    soup = BeautifulSoup(raw, "html.parser")
    for node in soup.select("a[href],link[href],script[src],img[src],[data-src]"):
        for attr in ("href", "src", "data-src"):
            value = node.get(attr)
            if value is None or urlsplit(value).scheme or urlsplit(value).netloc:
                continue
            if restore:
                if value.startswith(prefix + "/"):
                    node[attr] = value[len(prefix) + 1 :]
            elif value and not value.startswith("#"):
                node[attr] = prefix + "/" + value
    if soup.base:
        soup.base["href"] = "../" if restore else "../../"
    if not restore and soup.body and not soup.select_one("[data-suite-home]"):
        link = soup.new_tag("a", href="index.html")
        link["data-suite-home"] = ""
        link.string = "全部评测" if soup.html.get("lang") == "zh" else "All benchmarks"
        (soup.select_one(".report-tools") or soup.body).append(link)
    return str(soup)


def authored_intro(model, route, language):
    value = model.get("pages", {}).get(route, {}).get("intro", {})
    if not isinstance(value, dict) or not isinstance(value.get(language, ""), str):
        raise ValueError("Report page intro must contain language-keyed HTML")
    return value.get(language, "")


def home_comparison_end(document):
    core = document.select_one("[data-core-metrics]")
    if core is None or (
        core.name != "table" and core.select_one("table[data-completion-metrics]") is None
    ):
        return None
    anchor = core.parent if core.name == "table" else core
    note = anchor.find_next_sibling()
    return note if note is not None and note.has_attr("data-completion-links") else anchor




def page_intro_hash(rendered):
    node = BeautifulSoup(rendered, "html.parser").select_one("main > [data-page-intro]")
    if node is None:
        raise ValueError("Report page intro was not rendered")
    return digest(str(node).encode())


def model_comparison_tables(output, binding, language):
    """Render model diagnostics from hash-bound scored cases, not authored numbers."""
    from harness.web_extract.analysis import summarize_model_rows

    if not binding:
        return {}
    dataset = binding["dataset"]
    rendered = {}
    archives = {}
    for name, specification in binding["tables"].items():
        if not re.fullmatch(r"[a-z]+", name):
            raise ValueError("Unsafe model comparison table name")
        rows, metrics, case_ids, seen = [], {i: [] for i in range(1, 8)}, None, set()
        for cohort in specification["cohorts"]:
            archive = cohort["archive"]
            relative = Path(archive["path"])
            if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "assets":
                raise ValueError("Unsafe model comparison evidence archive")
            path = output / relative
            if path.is_symlink():
                raise ValueError("Unsafe model comparison evidence archive")
            if archive["path"] not in archives:
                archives[archive["path"]] = digest(path.read_bytes())
            if archives[archive["path"]] != archive["sha256"]:
                raise ValueError("Model comparison evidence archive changed")
            with zipfile.ZipFile(path) as bundle:
                raw = bundle.read(cohort["scores"]["path"])
            if digest(raw) != cohort["scores"]["sha256"]:
                raise ValueError("Model comparison scored evidence changed")
            scores = json.loads(raw)
            if scores["stage"] != name:
                raise ValueError("Model comparison stage differs from its evidence")
            identities = [(row["model_id"], row["case_id"]) for row in scores["details"]]
            expected = {(model_id, case_id) for model_id in scores["groups"]
                        for case_id in scores["case_ids"]}
            if len(identities) != len(expected) or set(identities) != expected:
                raise ValueError("Model comparison case population is incomplete or duplicated")
            if case_ids is not None and scores["case_ids"] != case_ids:
                raise ValueError("Model comparison cohorts must use the same cases")
            case_ids = scores["case_ids"]
            groups = summarize_model_rows(scores["details"], scores["groups"])
            if groups != scores["groups"]:
                raise ValueError("Model comparison aggregates differ from retained case scores")
            for model_id, group in groups.items():
                if model_id in seen:
                    raise ValueError("Duplicate model comparison configuration")
                seen.add(model_id)
                values = group["datasets"][dataset]
                count = values["cases"]
                timers = [values["timings"][key] for key in (
                    "first_any_output_seconds", "first_answer_seconds",
                    "complete_json_seconds", "elapsed_seconds")]
                label = specification.get("labels", {}).get(model_id, model_id)
                if isinstance(label, dict):
                    label = label[language]
                cohort_label = cohort.get("label", {})
                rows.append([
                    esc(label), record_metric(values["f1"], True),
                    f'{values["schema_valid"]}/{count}',
                    f'{values["page_exact"]}/{values["page_exact_denominator"]}',
                    *[record_metric(timer["p50"]) + '<br><small>'
                      + f'{timer["count"]}/{count}</small>' for timer in timers],
                    esc(cohort_label.get(language, "")),
                ])
                raw_values = [values["f1"], values["schema_valid"] / count,
                              values["page_exact"] / values["page_exact_denominator"]
                              if values["page_exact_denominator"] else None,
                              *[timer["p50"] for timer in timers]]
                for index, value in enumerate(raw_values, 1):
                    metrics[index].append(value)
        rendered[name] = record_comparison(
            message("model_comparison_headers", language), rows,
            {i: ("model-" + name + "-" + str(i), "max" if i <= 3 else "neutral", values)
             for i, values in metrics.items()},
            pin_method=True,
        )
    return rendered


def bind_model_tables(output, binding, language, body):
    soup = BeautifulSoup(body, "html.parser")
    tables = model_comparison_tables(output, binding, language)
    existing = soup.select("[data-model-validation], [data-model-screening]")
    if len(existing) != len(tables):
        raise ValueError("Model comparison requires one evidence-bound table per stage")
    for name, markup in tables.items():
        nodes = soup.select("[data-model-" + name + "]")
        if len(nodes) != 1:
            raise ValueError("Model comparison table placeholder is missing or duplicated")
        nodes[0].clear()
        nodes[0].append(BeautifulSoup(markup, "html.parser"))
    return str(soup) if tables else body


def intro_is_correctly_placed(document, node, route, mode):
    if Path(route).stem == "index" and mode in ("full-cohort-progress", "field-extraction-cohort"):
        anchor = home_comparison_end(document)
        return anchor is not None and node.find_previous_sibling() is anchor
    return document.main.find(True, recursive=False) is node


def report_editions(manifest):
    """The maintained renderer emits Chinese alone or English plus Chinese."""
    primary = manifest.get("primary_language")
    expected = {"zh": ["zh"], "en": ["en", "zh"]}.get(primary)
    if expected is None or manifest.get("languages") != expected:
        raise ValueError("Report generated editions differ from their primary language")
    if manifest.get("mode") == "field-extraction-cohort" and primary != "zh":
        raise ValueError("Record reports currently support only the Chinese edition")
    return expected


def verify_page_intros(source, model, manifest):
    """Reproduce the complete intro inventory from the selected frozen content."""
    expected, observed = {}, {}
    placeholder = '<div class="table-wrap"><table data-core-metrics></table></div>'
    for language in report_editions(manifest) if "languages" in manifest else []:
        for route in PAGES:
            relative = language + "/" + route + ".html"
            intro = authored_intro(model, route, language)
            if intro:
                rendered = page(
                    language,
                    route,
                    "",
                    placeholder,
                    languages=manifest["languages"],
                    state=model.get("mode", "compatibility"),
                    intro=intro,
                )
                expected[relative] = page_intro_hash(rendered)
            document = BeautifulSoup((source / relative).read_text(encoding="utf-8"), "html.parser")
            nodes = document.select("[data-page-intro]")
            if nodes:
                if len(nodes) != 1 or nodes[0].parent is not document.main:
                    raise ValueError("Frozen page intro inventory differs")
                if not intro_is_correctly_placed(
                    document, nodes[0], route, model.get("mode", "compatibility")
                ):
                    raise ValueError("Frozen page intro position differs")
                observed[relative] = digest(str(nodes[0]).encode())
    if observed != expected or manifest.get("page_intros", {}) != expected:
        raise ValueError("Frozen page intro inventory or content differs")


def cohort_source(source, selection, home, truth):
    """Validate a complete certified run or an immutable journal checkpoint."""
    from harness.web_extract.dataset import benchmark_config

    adapter = benchmark_config(home)["adapter"]

    def project(row):
        schema = adapter.task_schema(home, truth[row["case_id"]]) if adapter else None
        return normalize(row["method"], row.get("response", {}), schema=schema)

    contract = read_json(source / "contract.json")
    plan_path = Path(selection["plan"])
    plan = read_json(plan_path)
    from harness.web_extract.certification import verify_execution_assessment

    verify_execution_assessment(plan, contract["methods"][0], execution_sources=contract)
    precall = read_json(source / "certification-precall.json")
    if (
        digest(plan_path.read_bytes()) != selection["plan_sha256"]
        or precall["profile_sha256"] != selection["plan_sha256"]
        or precall.get("case_ids_sha256") != plan["case_ids_sha256"]
        or contract["dataset_manifest_sha256"]
        != digest((home / "dataset/manifest.json").read_bytes())
        or set(contract["case_ids"]) != set(truth)
        or contract["kind"] != "full_archived_extraction"
        or plan["case_count"] != len(truth)
        or plan["case_ids_sha256"] != digest(("\n".join(sorted(truth)) + "\n").encode())
        or contract["methods"] != [precall["method"]]
        or precall["method"] not in plan["participants"]
        or precall["dataset_manifest_sha256"] != contract["dataset_manifest_sha256"]
        or precall["pipeline_sha256"] != contract["pipeline_sha256"]
        or contract["dataset_manifest_sha256"]
        != digest((home / "dataset/manifest.json").read_bytes())
    ):
        raise ValueError("Full cohort plan, population or execution binding differs")
    verify_execution_snapshot(source, contract)
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
        if (
            cert["profile_sha256"] != selection["plan_sha256"]
            or cert["case_count"] != len(truth)
            or cert.get("method") != precall["method"]
            or cert.get("dataset_manifest_sha256") != contract["dataset_manifest_sha256"]
        ):
            raise ValueError("Certificate population, profile, method or dataset differs")
        saved = read_json(source / "scores.json")
        # Recompute through the original scoring boundary, without provider calls.
        import tempfile

        with tempfile.TemporaryDirectory(prefix="web-extract-score-check-") as temporary:
            recalculated = score_run(home, source, output=Path(temporary) / "scores.json")
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
            saved = score_run(
                home, source, reproject=True, output=assessment_path, verify_existing=True
            )
            rows = [
                {
                    **r,
                    "collected_normalized_fields": r.get("normalized_fields"),
                    "normalized_fields": project(r),
                    "projection_revision": saved["projection_revision"],
                }
                for r in rows
            ]
        repair_binding = []
        if selection.get("repair"):
            from harness.web_extract.repair import load_selection

            revisions = selection["repair"]
            _, rows = load_selection(home, selection)
            if selection.get("assessment"):
                rows = [{**r, "normalized_fields": project(r)} for r in rows]
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
    latest, physical, physical_entries, selected_entries = {}, [], [], {}
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
        physical_entries.append(entry)
        selected_entries[key] = entry
        latest[key] = row
    verify_retry_history(
        source,
        {"calls": list(selected_entries.values()), "physical_calls": physical_entries},
        contract,
    )
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


def cost_unavailability(value, lang):
    unavailable = value.get("cost_unavailable")
    if unavailable is None:
        return None
    if not isinstance(unavailable, dict):
        raise ValueError("Invalid cost unavailability disclosure")
    cases = unavailable.get("population_cases")
    missing = unavailable.get("missing_cases")
    evidence = unavailable.get("evidence", {})
    reason = unavailable.get("reason", {})
    software_fee = value.get("software_fee_usd_per_1000")
    if software_fee is not None and (
        type(software_fee) not in (int, float)
        or not math.isfinite(software_fee)
        or software_fee < 0
    ):
        raise ValueError("Invalid software fee")
    if (
        type(cases) is not int
        or type(missing) is not int
        or not 0 < missing <= cases
        or not isinstance(reason, dict)
        or not isinstance(reason.get(lang), str)
        or not reason[lang].strip()
        or not isinstance(evidence, dict)
        or not isinstance(evidence.get("path"), str)
        or not isinstance(evidence.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", evidence.get("sha256", ""))
        or value.get("usd_per_1000_range") is not None
        or value.get("pricing_formula")
    ):
        raise ValueError("Invalid cost unavailability disclosure")
    return unavailable


def price_remarks(value, lang, *, summary=False):
    text = value.get("billing_summary", {}).get(lang) if summary else None
    text = text or value["terms"][lang]
    unavailable = cost_unavailability(value, lang)
    if unavailable and value.get("software_fee_usd_per_1000") is None:
        text += " " + message(
            "cost_unavailable_population",
            lang,
            missing=unavailable["missing_cases"],
            cases=unavailable["population_cases"],
            reason=unavailable["reason"][lang],
        )
    return text


def price_quote(value, lang):
    if cost_unavailability(value, lang):
        software_fee = value.get("software_fee_usd_per_1000")
        if software_fee is not None:
            return message("software_fee_plus_model", lang, fee=f"{software_fee:g}")
        return message("cost_total_unavailable", lang)
    bounds = value.get("usd_per_1000_range")
    if bounds is None:
        if value.get("pricing_formula"):
            raise ValueError("Usage-priced methods require a numeric workload cost conversion")
        return value["usd_per_1000"]
    if (
        not isinstance(bounds, list)
        or len(bounds) != 2
        or any(type(n) not in (int, float) or not math.isfinite(n) or n < 0 for n in bounds)
        or bounds[0] > bounds[1]
    ):
        raise ValueError("Invalid USD per 1,000 calls range")
    if value.get("cost_conversion"):
        from harness.reporting.pricing import usd_per_thousand

        converted = usd_per_thousand(**value["cost_conversion"])
        if any(
            not math.isclose(a, b, rel_tol=1e-10) for a, b in zip(bounds, converted, strict=True)
        ):
            raise ValueError("Displayed cost differs from its usage conversion")
    low, high = (f"{n:.3f}".rstrip("0").rstrip(".") for n in bounds)
    prefix = ("约 " if lang == "zh" else "Est. ") if value.get("estimate") else ""
    return prefix + "$" + low + ("–" + high if bounds[0] != bounds[1] else "")


def redact_account_endpoint(row):
    """Preserve request parameters while hiding account identifiers in its URL."""
    result = dict(row)
    request = row.get("request")
    if isinstance(request, dict):
        request = dict(request)
        endpoint = request.get("endpoint", "")
        if isinstance(endpoint, str):
            request["endpoint"] = re.sub(r"/accounts/[^/]+/", "/accounts/[REDACTED]/", endpoint)
        result["request"] = request
    if isinstance(row.get("physical_attempts"), list):
        result["physical_attempts"] = [
            redact_account_endpoint(v) if isinstance(v, dict) else v
            for v in row["physical_attempts"]
        ]
    if isinstance(row.get("superseded_result"), dict):
        result["superseded_result"] = redact_account_endpoint(row["superseded_result"])
    return result


def verify_execution_projection(original, published):
    """Bind delivery, timing, billing and every retained physical attempt."""
    derived = {"normalized_fields", "collected_normalized_fields", "projection_revision"}
    required = {
        "response",
        "http_status",
        "compatibility",
        "request",
        "state",
        "error",
        "elapsed_seconds",
        "provider_usage",
        "physical_attempt",
        "retry_execution_seconds",
        "retry_missing_timers",
        "review",
    }

    def same_fields(expected, actual):
        if ("review" in expected) != ("review" in actual):
            return False
        fields = (set(expected) - derived - {"physical_attempts"}) | required
        return canonical_json_sha256(
            {k: actual.get(k) for k in fields}, allow_nan=False
        ) == canonical_json_sha256({k: expected.get(k) for k in fields}, allow_nan=False)

    if not same_fields(original, published):
        raise ValueError("Completion audit native execution projection changed")
    expected = original.get("physical_attempts")
    actual = published.get("physical_attempts")
    if expected is None and actual is None:
        return
    if expected is None:
        expected = [{k: v for k, v in original.items() if k != "physical_attempts"}]
    if (
        not isinstance(expected, list)
        or not expected
        or any(not isinstance(v, dict) for v in expected)
    ):
        raise ValueError("Completion audit native attempt history is invalid")
    if actual is None:
        if len(expected) != 1 or not same_fields(expected[0], published):
            raise ValueError("Completion audit native attempt history is missing")
        return
    if not isinstance(actual, list) or any(not isinstance(v, dict) for v in actual):
        raise ValueError("Completion audit native attempt history is invalid")

    def canonical(history):
        return [{k: v for k, v in row.items() if k not in derived} for row in history]

    if canonical_json_sha256(canonical(actual), allow_nan=False) != canonical_json_sha256(
        canonical(expected), allow_nan=False
    ):
        raise ValueError("Completion audit native attempt history changed")


def cohort_source_binding(source, selection, contract, receipt, *, records=False):
    """Use one metadata shape for initial publication and independent verification."""
    if records:
        return {
            "run_id": source.name,
            "contract": contract,
            "receipt": receipt,
            "plan_sha256": selection["plan_sha256"],
        }
    return {
        "run_id": source.name,
        "manifest_sha256": digest((source / "manifest.json").read_bytes())
        if (source / "manifest.json").exists()
        else None,
        **receipt,
        "source_plan_sha256": selection.get("plan_sha256"),
        "methods": selection.get("methods"),
        "contract": contract,
    }


def verify_cohort_authorities(source, home, model, manifest, *, profile):
    """Revalidate private certification through the same owner used by the build."""
    mode = model.get("mode")
    if mode not in ("full-cohort-progress", "field-extraction-cohort"):
        return None
    truth = read_json(home / "dataset/dataset/ground-truth.json")
    records = mode == "field-extraction-cohort"
    if records:
        published = read_json(source / "public-evidence/sources.json")
        method_order = list(model["participants"])
    else:
        plan = model["cohort_plan"]
        raw = Path(plan["path"]).read_bytes()
        if digest(raw) != plan["sha256"]:
            raise ValueError("Completion audit cohort plan changed")
        method_order = list(json.loads(raw)["participants"])
        published = manifest["source_runs"]
    expected_methods = set(method_order)
    if not expected_methods or canonical_json_sha256(
        {
            "methods": manifest.get("planned_methods"),
            "cases": manifest.get("case_count"),
        }
    ) != canonical_json_sha256({"methods": method_order, "cases": len(truth)}):
        raise ValueError("Completion audit declared population differs from frozen inputs")
    if records:
        compared, skipped = record_comparison_scope(model["participants"])
        if (
            manifest.get("comparison_methods") != compared
            or manifest.get("skipped_methods") != skipped
        ):
            raise ValueError("Completion audit comparison scope differs from frozen inputs")
    originals, seen = [], set()
    if records:
        try:
            assembled = record_cohort_inputs(home, model, profile=profile)
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ValueError("Completion audit cohort authority differs") from error
        expected = assembled["sources"]
        seen = set(expected)
        originals = [
            [row for row in assembled["native_rows"] if row["method"] == method]
            for method in expected
        ]
    else:
        expected = []
        for selection in model["evidence_runs"]:
            directory = resolve_run_path(home, selection["run"])
            try:
                contract, rows, receipt = cohort_source(directory, selection, home, truth)
            except (OSError, KeyError, TypeError, ValueError) as error:
                raise ValueError("Completion audit cohort authority differs") from error
            method = contract["methods"][0]
            if method in seen:
                raise ValueError("Completion audit certified participant scope differs")
            expected.append(cohort_source_binding(directory, selection, contract, receipt))
            seen.add(method)
            originals.append(rows)
    if seen != expected_methods:
        raise ValueError("Completion audit certified participant scope differs")
    if records and manifest.get("complete_methods") != [
        method for method in method_order if all(
            segment["receipt"]["certified"]
            for segment in expected[method].get("segments", [expected[method]])
        )
    ]:
        raise ValueError("Completion audit certified population differs from frozen inputs")
    view, _ = project_evidence_view(
        "public-evidence/sources.json" if records else "public-evidence/metadata.json",
        json.dumps(expected, ensure_ascii=False).encode(),
        role="native_response",
        content_format="json",
        host_names=publication_host_names(),
    )
    if canonical_json_sha256(json.loads(view)) != canonical_json_sha256(published):
        raise ValueError("Completion audit certification binding changed")
    return originals


def record_reference_policy(home):
    """Use the dataset adapter's declared matching and reference-coverage policy."""
    from harness.web_extract.dataset import benchmark_config

    adapter = benchmark_config(home)["adapter"]
    return adapter.reference_policy(home) if hasattr(adapter, "reference_policy") else None


def verify_record_reference(home, model, truth, rows, audit):
    """Share the reference-revision and audit contract between build and check."""
    from harness.web_extract.analysis import check_record_audit

    revision = None
    if model.get("reference_revision"):
        binding = model["reference_revision"]
        revision = Path(binding["path"])
        if revision.is_symlink() or digest(revision.read_bytes()) != binding["sha256"]:
            raise ValueError("Record reference revision differs from its report binding")
    if audit is not None:
        check_record_audit(home, truth, rows, audit, reference_revision=revision)


def verify_public_executions(source, home, model, cohort_rows, audit, *, profile):
    """Bind every execution and scored value independently of review coverage."""
    from harness.web_extract.repair import load_selection

    reviewed = audit.get("results", {}) if audit else {}
    compatibility = read_json(source / "report-manifest.json").get("mode") == "compatibility"
    records = model.get("mode") == "field-extraction-cohort"
    truth = (
        read_json(home / "dataset/dataset/ground-truth.json")
        if cohort_rows is not None or compatibility
        else {}
    )
    scoring_truth = audit["audited_reference"] if records and audit else truth
    if records:
        references = [("reference.json", scoring_truth)]
        if audit:
            references.append(("original-reference.json", truth))
        for relative, reference in references:
            raw = (
                json.dumps(reference, ensure_ascii=False).encode()
                if relative == "reference.json"
                else (home / "dataset/dataset/ground-truth.json").read_bytes()
            )
            view, _ = project_evidence_view(
                "public-evidence/" + relative,
                raw,
                role="scored_answer",
                content_format="json",
                host_names=publication_host_names(),
            )
            if canonical_json_sha256(
                read_json(source / "public-evidence" / relative)
            ) != canonical_json_sha256(json.loads(view)):
                raise ValueError("Completion audit public reference differs from frozen inputs")
    public_rows, seen, native_paths = {}, set(), set()
    for index, selection in enumerate(model.get("evidence_runs", [])):
        if compatibility:
            directory = resolve_run_path(home, selection["run"])
            contract, originals = load_run(directory)
            if contract["dataset_manifest_sha256"] != digest(
                (home / "dataset/manifest.json").read_bytes()
            ):
                raise ValueError("Compatibility executions belong to different inputs")
            originals = [
                row
                for row in originals
                if selection.get("methods") is None or row["method"] in selection["methods"]
            ]
        elif cohort_rows is None:
            _, originals = load_selection(home, selection)
        else:
            originals = cohort_rows[index]
        for row in originals:
            method, cid = row["method"], row["case_id"]
            key = method + ":" + cid
            native_hash = digest(
                json.dumps(row.get("response"), sort_keys=True, ensure_ascii=False).encode()
            )
            if key in seen or (
                reviewed
                and (
                    reviewed.get(key, {}).get("native_response_sha256") != native_hash
                    or reviewed.get(key, {}).get("execution_sha256") != execution_identity(row)
                )
            ):
                raise ValueError("Completion audit native original changed")
            seen.add(key)
            if compatibility:
                relative = f"public-evidence/{method}-{cid}.json"
                native_paths.add(relative)
                public_rows.setdefault(method, {})[cid] = read_json(source / relative)
            elif method not in public_rows:
                public_rows[method] = read_json(source / "public-evidence" / (method + ".json"))
            expected_row = row
            if cohort_rows is not None or compatibility:
                if records:
                    current = row
                    if audit:
                        from harness.web_extract.dataset import benchmark_config

                        current = {
                            **row,
                            "normalized_fields": normalize(
                                method,
                                row.get("response", {}),
                                schema=benchmark_config(home)["adapter"].task_schema(
                                    home, truth[cid]
                                ),
                            ),
                        }
                    if reviewed:
                        current = {**current, "review": reviewed[key]}
                    expected_row = record_result_fields(
                        scoring_truth[cid],
                        current,
                        policy=audit["reference_policy"] if audit else record_reference_policy(home),
                    )
                else:
                    expected_row = {
                        **row,
                        "field_results": field_results(home, truth[cid], row, profile=profile),
                    }
                if reviewed:
                    expected_row = {**expected_row, "review": reviewed[key]}
            view, _ = project_evidence_view(
                "public-evidence/native-row.json",
                json.dumps(redact_account_endpoint(expected_row), ensure_ascii=False).encode(),
                role="native_response",
                content_format="json",
                host_names=publication_host_names(),
            )
            expected = json.loads(view)
            published = public_rows[method].get(cid, {})
            verify_execution_projection(expected, published)
            if (cohort_rows is not None or compatibility) and canonical_json_sha256(
                expected.get("normalized_fields"), allow_nan=False
            ) != canonical_json_sha256(published.get("normalized_fields"), allow_nan=False):
                raise ValueError("Completion audit scored projection changed")
    published_keys = {
        method + ":" + cid for method, bucket in public_rows.items() for cid in bucket
    }
    if seen != published_keys or (reviewed and seen != set(reviewed)):
        raise ValueError("Completion audit native response coverage changed")
    if compatibility and native_paths != {
        path.relative_to(source).as_posix()
        for path in (source / "public-evidence").glob("*.json")
        if path.name not in ("metadata.json", "audit.json")
    }:
        raise ValueError("Compatibility native response coverage changed")


def verify_completion_audit(source, profile, *, home=None):
    """Verify the externally selected profile and reproduce its original audit view."""
    source = Path(source)
    manifest = read_json(source / "report-manifest.json")
    if not profile:
        raise ValueError("Completion audit requires its frozen profile")
    profile = Path(profile)
    if profile.is_symlink() or digest(profile.read_bytes()) != manifest["profile_sha256"]:
        raise ValueError("Completion audit frozen profile changed")
    instance = read_json(profile)
    if home is None:
        raise ValueError("Completion audit requires an explicit benchmark home")
    home = Path(home)
    require_external_output(home)
    if instance.get("dataset"):
        try:
            validate_dataset(home, profile)
        except (OSError, KeyError, ValueError) as error:
            raise ValueError(
                "Completion audit benchmark home differs from its frozen dataset"
            ) from error
    elif instance.get("local_example") is not True:
        raise ValueError("Completion audit frozen profile lacks its dataset identity")
    records = manifest.get("mode") == "field-extraction-cohort"
    authority = manifest.get("audit" if records else "completion_audit")
    content_binding = instance.get("research_report", {}).get("content", {})
    if (
        instance.get("local_example") is True
        and not authority
        and not content_binding.get("path")
        and instance.get("independent_result_review_scope") != "all"
    ):
        verify_page_intros(source, {}, manifest)
        return
    if not content_binding.get("path"):
        raise ValueError("Completion audit requires frozen content")
    content = profile.parent / content_binding["path"]
    if content.is_symlink() or digest(content.read_bytes()) != content_binding["sha256"]:
        raise ValueError("Completion audit frozen content changed")
    model = read_json(content)
    records = model.get("mode", manifest.get("mode")) == "field-extraction-cohort"
    authority = manifest.get("audit" if records else "completion_audit")
    verify_page_intros(source, model, manifest)
    cohort_rows = verify_cohort_authorities(source, home, model, manifest, profile=profile)
    all_rows = [row for rows in (cohort_rows or []) for row in rows]
    audit_binding = model.get("audit")
    if not audit_binding:
        if authority or instance.get("independent_result_review_scope") == "all":
            raise ValueError("Completion audit requires its frozen original")
        if cohort_rows is not None and manifest.get("completion_reference_exclusions", {}):
            raise ValueError("Completion audit reference coverage differs from frozen inputs")
        if records:
            if (
                cohort_rows is not None
                and manifest.get("reference_policy") != record_reference_policy(home)
            ):
                raise ValueError("Completion audit reference policy differs from frozen inputs")
            verify_record_reference(
                home, model, read_json(home / "dataset/dataset/ground-truth.json"), all_rows, None
            )
        verify_public_executions(source, home, model, cohort_rows, None, profile=profile)
        return
    if not authority or authority.get("path") != "public-evidence/audit.json":
        raise ValueError("Completion audit public authority is missing")
    audit = content.parent / audit_binding["path"]
    raw = audit.read_bytes()
    if audit.is_symlink() or digest(raw) != audit_binding["sha256"]:
        raise ValueError("Completion audit original changed")
    projected, _ = project_evidence_view(
        authority["path"],
        raw,
        role="native_response",
        content_format="json",
        host_names=publication_host_names(),
    )
    if (
        authority["source_sha256"] != audit_binding["sha256"]
        or (source / authority["path"]).read_bytes() != projected
    ):
        raise ValueError("Completion audit differs from its frozen original")
    audit_data = json.loads(raw)
    if cohort_rows is not None and manifest.get("completion_reference_exclusions", {}) != (
        {} if records else audit_data.get("excluded_fields", {})
    ):
        raise ValueError("Completion audit reference coverage differs from frozen inputs")
    if records and cohort_rows is not None:
        if canonical_json_sha256(manifest.get("reference_policy")) != canonical_json_sha256(
            audit_data["reference_policy"]
        ):
            raise ValueError("Completion audit reference policy differs from frozen inputs")
        verify_record_reference(
            home, model, read_json(home / "dataset/dataset/ground-truth.json"), all_rows, audit_data
        )
    verify_public_executions(source, home, model, cohort_rows, audit_data, profile=profile)


def completion_bundle(methods, rows, *, records, complete_methods, excluded_fields=None):
    """Derive additional metrics from the same public final-response evidence."""
    from harness.web_extract.analysis import completion_metrics

    excluded_fields = excluded_fields or {}
    results = {}
    for method in methods:
        selected = [r for r in rows if r["method"] == method]
        judgments = {}
        for row in selected:
            fields = row["field_results"]
            if not records:
                fields = row.get("review", {}).get("audited_fields", fields)
                fields = {f: fields[f] for f in PRODUCT_FIELDS}
            judgments[row["case_id"]] = {
                f: {**v, "status": "unavailable"}
                if f in excluded_fields.get(row["case_id"], [])
                else v
                for f, v in fields.items()
            }
        results[method] = (
            completion_metrics(selected, judgments, records=records)
            if method in complete_methods
            else None
        )
    return {
        "schema": "web-extract-completion-metrics/1",
        "provider_calls_added": 0,
        "reference_view": "source-qualified" if any(r.get("review") for r in rows) else "original",
        "records": records,
        "complete_methods": list(complete_methods),
        "excluded_fields": excluded_fields,
        "source_sha256": digest(
            json.dumps(
                sorted(rows, key=lambda r: (r["method"], r["case_id"])),
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ),
        "methods": results,
    }


def completion_columns(bundle, methods, language):
    """Render only the supplementary columns through the shared comparison owner."""
    values = bundle["methods"]
    rows = []
    keys = ("page_exact", "empty_fields")
    for method in methods:
        metrics = values[method]
        cells = []
        for key in keys:
            metric = metrics[key] if metrics else None
            cells.append(
                message("completion_incomplete", language)
                if metrics is None
                else message("completion_unavailable", language)
                if metric["rate"] is None
                else message(
                    "completion_rate", language, **metric, percent=f"{metric['rate'] * 100:.2f}"
                )
            )
        if metrics and metrics["excluded_pages"]:
            cells[0] += (
                "<br><small>"
                + message("completion_excluded", language, count=metrics["excluded_pages"])
                + "</small>"
            )
        rows.append(cells)
    headers = [esc(label) for label in message("completion_headers", language)[1:]]
    soup = BeautifulSoup(
        record_comparison(
            headers,
            rows,
            {
                column: (
                    "completion-" + key,
                    "max",
                    [values[m][key]["rate"] if values[m] else None for m in methods],
                )
                for column, key in enumerate(keys)
            },
        ),
        "html.parser",
    )
    for index, key in enumerate(keys):
        soup.select("thead th")[index]["data-completion-metric"] = key
        for row in soup.select("tbody tr"):
            row.select("td")[index]["data-completion-metric"] = key
    return soup.table


def with_completion_columns(core, bundle, methods, language, *, before_details=False):
    """Extend the existing participant comparison without creating another table."""
    soup = BeautifulSoup(core, "html.parser")
    target = soup.select_one("table")
    columns = completion_columns(bundle, methods, language)
    if target is None or len(target.select("tbody tr")) != len(methods):
        raise ValueError("Completion columns require the complete core comparison")
    header = target.select_one("thead tr")
    before = header.select("th")[-1] if before_details else None
    for cell in list(columns.select("thead th")):
        before.insert_before(cell.extract()) if before is not None else header.append(
            cell.extract()
        )
    for method, row, extra in zip(
        methods, target.select("tbody tr"), columns.select("tbody tr"), strict=True
    ):
        row["data-method"] = method
        before = row.select("td")[-1] if before_details else None
        for cell in list(extra.select("td")):
            before.insert_before(cell.extract()) if before is not None else row.append(
                cell.extract()
            )
    target["data-completion-metrics"] = "true"
    target.parent.insert_after(
        BeautifulSoup(completion_links(language, bundle["reference_view"]), "html.parser")
    )
    return str(soup)


def completion_links(language, reference_view):
    if reference_view not in ("original", "source-qualified"):
        raise ValueError("Completion note requires an explicit reference view")
    return (
        '<p class="table-note" data-completion-links>'
        + message(
            "completion_qualified_note"
            if reference_view == "source-qualified"
            else "completion_original_note",
            language,
        )
        + ' <a href="index.html#metric-completion-page_exact">'
        + message("completion_guide_link", language)
        + '</a> · <a href="../data/completion-metrics.json">'
        + message("completion_receipt", language)
        + "</a></p>"
    )


def check_completion_metrics(output, manifest, rows):
    """Recompute additional scores and reject missing or inconsistent displays."""
    bundle = read_json(output / "data/completion-metrics.json")
    methods = manifest["planned_methods"]
    records = manifest["mode"] == "field-extraction-cohort"
    complete = (
        [
            m
            for m in methods
            if m in manifest["complete_methods"] and m in manifest["comparison_methods"]
        ]
        if records
        else [m for b in manifest["source_runs"] if b.get("certified") for m in b["scores"]]
    )
    if bundle["records"] != records or bundle["complete_methods"] != complete:
        raise ValueError("Completion metrics use a different certified population")
    if bundle["excluded_fields"] != manifest.get("completion_reference_exclusions", {}):
        raise ValueError("Completion metrics use different reference coverage")
    authority = manifest.get("completion_audit")
    if not records and authority:
        raw = (output / authority["path"]).read_bytes()
        receipt = next(
            (v for v in manifest["public_evidence_views"] if v["path"] == authority["path"]), {}
        )
        audit = json.loads(raw)
        if (
            digest(raw) != authority["sha256"]
            or receipt.get("original_sha256") != authority["source_sha256"]
            or bundle["excluded_fields"] != audit.get("excluded_fields", {})
            or audit.get("result_count") != manifest["observations"]
        ):
            raise ValueError("Completion reference coverage differs from authoritative audit")
        for row in rows:
            reviewed = audit.get("results", {}).get(row["method"] + ":" + row["case_id"], {})
            if canonical_json_sha256(
                row.get("review", {}).get("audited_fields"), allow_nan=False
            ) != canonical_json_sha256(reviewed.get("audited_fields"), allow_nan=False):
                raise ValueError("Completion judgments differ from authoritative audit")
    elif not records and (manifest.get("review") or bundle["excluded_fields"]):
        raise ValueError("Completion reference coverage lacks its authoritative audit")
    expected = completion_bundle(
        methods,
        rows,
        records=bundle["records"],
        complete_methods=bundle["complete_methods"],
        excluded_fields=bundle["excluded_fields"],
    )
    if canonical_json_sha256(bundle, allow_nan=False) != canonical_json_sha256(
        expected, allow_nan=False
    ):
        raise ValueError("Completion metrics differ from retained final responses")
    for lang in manifest["languages"]:
        expected = completion_columns(bundle, methods, lang)
        for route in ("index", "interpretation"):
            document = BeautifulSoup(
                (output / lang / (route + ".html")).read_text(encoding="utf-8"), "html.parser"
            )
            tables = document.select("table[data-completion-metrics]")
            if len(tables) != 1 or document.select("section[data-completion-metrics]"):
                raise ValueError("Completion metrics must share the core comparison")
            note = document.select("[data-completion-links]")
            expected_note = BeautifulSoup(
                completion_links(lang, bundle["reference_view"]), "html.parser"
            ).p
            if len(note) != 1 or note[0].get_text() != expected_note.get_text():
                raise ValueError("Completion metric source or reference explanation differs")
            table_view = tables[0]
            if not table_view.has_attr("data-method-column"):
                raise ValueError("Completion comparison must keep method names pinned")
            owner = "data-core-metrics" if route == "index" else "data-interpretation-comparison"
            if (
                not table_view.has_attr(owner)
                and table_view.find_parent(attrs={owner: True}) is None
            ):
                raise ValueError("Completion metrics are outside the core comparison")
            if table_view.select("th[data-completion-metric]") != expected.select("th"):
                raise ValueError("Completion metric headers or provenance differ")
            actual_rows = table_view.select("tbody tr")
            if len(actual_rows) != len(methods):
                raise ValueError("Completion comparison population differs")
            for method, actual, wanted in zip(
                methods, actual_rows, expected.select("tbody tr"), strict=True
            ):
                if actual.get("data-method") != method or actual.select(
                    "td[data-completion-metric]"
                ) != wanted.select("td"):
                    raise ValueError("Completion metric comparison is missing or inconsistent")


def cohort_metrics(methods, rows, bindings, count, lang, labels=None, *, truth=None):
    """One full-population summary, with incomplete quality left unavailable."""
    labels = labels or {}
    comparable = latency_comparable(
        selected_execution_contracts([binding for binding in bindings if "contract" in binding]),
        methods,
    )
    scores = {m: b["scores"][m] for b in bindings if b.get("certified") for m in b["scores"]}
    fields = ("price", "sku", "availability", "OutOfStock")
    blocked = {b["contract"]["methods"][0] for b in bindings if b.get("access_blocked")}
    records = []
    for method in methods:
        subset = [r for r in rows if r["method"] == method]
        complete = method in scores
        cells = [
            esc(labels.get(method, method)),
            f"<span data-execution-count>{len(subset)} / {count}</span>"
            + (
                '<small class="scope-state">'
                + message("access_limited" if method in blocked else "incomplete", lang)
                + "</small>"
                if not complete
                else ""
            ),
            str(sum(r.get("compatibility") == "structured_output" for r in subset)),
        ]
        for field in fields:
            value = scores.get(method, {}).get(field, {}).get("f1")
            cells.append("—" if value is None else f"{value:.3f}")
        records.append(cells)
    result = BeautifulSoup(
        table(
            message("cohort_core_headers", lang),
            records,
            attrs='data-core-metrics="full-cohort" data-sortable',
            pin_method=True,
            sort_values={
                1: [sum(r["method"] == method for r in rows) for method in methods],
                2: [int(row[2]) for row in records],
            },
        ),
        "html.parser",
    )
    if truth is not None:
        for field, heading in zip(fields, result.select("thead th")[3:], strict=True):
            population = reference_populations(truth)[field]
            heading.append(result.new_tag("br"))
            small = result.new_tag("small", attrs={"data-reference-population": field})
            small.string = message(
                population["label"], lang, **{k: v for k, v in population.items() if k != "label"}
            )
            heading.append(small)
    for method, row in zip(methods, result.select("tbody tr"), strict=True):
        row["data-method"] = method
        for field, cell in zip(fields, row.select("td")[3:], strict=True):
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
            attrs='data-latency-table="true" data-sortable',
            pin_method=True,
            sort_values={i: [int(row[i]) for row in latency] for i in (2, 3)},
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
                {"data-metric": name, "data-metric-direction": "min" if comparable else "neutral",
                 "data-sort": str(value)}
            )
            if comparable:
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
    if not comparable:
        note += " " + message("latency_loads_differ", lang)
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
        terms = esc(price_remarks(value, lang))
        unavailable = cost_unavailability(value, lang)
        if unavailable:
            separate_model = value.get("software_fee_usd_per_1000") is not None
            if separate_model:
                terms += "<details><summary>" + message("model_billing_records", lang) + "</summary>"
                terms += esc(message(
                    "cost_unavailable_population", lang,
                    missing=unavailable["missing_cases"], cases=unavailable["population_cases"],
                    reason=unavailable["reason"][lang],
                ))
            terms += '<br><a data-cost-evidence="true" href="'
            terms += (
                esc(quote("../" + unavailable["evidence"]["path"], safe="/"), quote=True) + '">'
            )
            terms += message("cost_disclosure_receipt", lang) + "</a>"
            if separate_model:
                terms += "</details>"
        records.append(
            [
                esc(labels.get(method, method)),
                esc(price_quote(value, lang)),
                terms,
                esc(value["verified_at"]),
                " · ".join(
                    f'<a href="{esc(u, quote=True)}" target="_blank" rel="noopener noreferrer">{i}</a>'
                    for i, u in enumerate(sources, 1)
                ),
            ]
        )
    return table(
        message("method_usd_1_000_requests_billing_terms_and_commitment_verified_o", lang),
        records,
        attrs='data-price-table="true"',
        pin_method=True,
    )


def self_hosted_usage(response):
    """Read exposed SDK counters without counting both nodes and their total."""
    native = response.get("native", {})
    if not isinstance(native, dict):
        return {}
    calls = native.get("model_calls")
    if isinstance(calls, list):
        usages = [call.get("usage", {}) for call in calls if isinstance(call, dict)]
    elif isinstance(native.get("usage"), dict):
        usages = [native["usage"]]
    else:
        execution = native.get("execution", [])
        execution = (
            [item for item in execution if isinstance(item, dict)]
            if isinstance(execution, list)
            else []
        )
        totals = [item for item in execution if item.get("node_name") == "TOTAL RESULT"]
        usages = (
            totals
            if len(totals) == 1
            else [item for item in execution if item.get("node_name") != "TOTAL RESULT"]
        )
    receipts = {}
    for usage in usages:
        if not isinstance(usage, dict):
            continue
        cached = usage.get("prompt_cache_hit_tokens")
        details = usage.get("prompt_tokens_details", {})
        if cached is None and isinstance(details, dict):
            cached = details.get("cached_tokens")
        for unit, value in (
            ("input_tokens", usage.get("prompt_tokens")),
            ("output_tokens", usage.get("completion_tokens")),
            ("cached_input_tokens", cached),
        ):
            if value is not None:
                receipts.setdefault(unit, []).append(value)
    data = native.get("data", {})
    metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
    if isinstance(metadata, dict) and metadata.get("creditsUsed") is not None:
        receipts["internal_credits"] = [metadata["creditsUsed"]]
    return receipts


def usage_table(methods, rows, lang, labels=None):
    labels = labels or {}
    self_hosted = {
        method
        for method, config in available_methods().items()
        if config.get("deployment") == "self-hosted-open-source"
    }
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
            native_receipts = self_hosted_usage(response) if method in self_hosted else {}
            if receipts or native_receipts:
                observed.append(receipts)
            for unit, value in receipts.items():
                totals.setdefault(unit, []).append(value)
            for unit, values in native_receipts.items():
                totals.setdefault(unit, []).extend(values)
        usages = []
        for unit, values in totals.items():
            try:
                total = sum(float(value) for value in values)
                if not math.isfinite(total):
                    raise ValueError("Nonfinite usage")
            except (TypeError, ValueError):
                usages.append(unit + ": " + esc(json.dumps(values, ensure_ascii=False)))
            else:
                label = (
                    message("usage_unit_" + unit, lang)
                    if unit
                    in {"input_tokens", "output_tokens", "cached_input_tokens", "internal_credits"}
                    else "ms (x-browser-ms-used)"
                    if unit == "x-browser-ms-used"
                    else unit
                )
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
        pin_method=True,
    )


def selected_execution_contracts(bindings):
    sources = {}
    for binding in bindings:
        for segment in binding.get("segments", [binding]):
            contract = segment["contract"]
            methods = segment.get("methods")
            for method in contract["methods"] if methods is None else methods:
                sources.setdefault(method, []).append(contract)
    return sources


def effective_concurrency(contract, method):
    value = contract.get("concurrency")
    policy = contract.get("execution_policy", {})
    if type(value) is not int:
        return None
    if policy:
        groups = policy["resources"]
        selected = policy.get("assignments", {}).get(method)
        if selected is None:
            selected = list(groups) if len(contract.get("methods", [])) == 1 else [method]
        value = min([value, policy["max_in_flight"]]
                    + [groups[group]["capacity"] for group in selected])
    return value


def latency_comparable(sources, methods):
    limits = {effective_concurrency(contract, method)
              for method in methods for contract in sources.get(method, [{}])}
    return len(limits) == 1 and None not in limits


def execution_settings(sources, methods, labels, language):
    """Expose the collected load without treating concurrency as a ranking."""
    rows = []
    for method, contract in (
        (m, c) for m in methods for c in sources.get(m, [{}])
    ):
        purpose = contract.get("execution_policy", {}).get("purpose", "undeclared")
        if purpose not in {"quality", "latency", "throughput", "undeclared"}:
            raise ValueError("Unknown execution purpose in measured evidence")

        def number(key, source=contract, participant=method):
            value = source.get(key)
            if key == "concurrency":
                value = effective_concurrency(source, participant)
            return f"{value:g}" if type(value) in (int, float) and math.isfinite(value) else "—"

        rows.append(
            [
                '<span data-execution-method="'
                + esc(method)
                + '">'
                + esc(labels.get(method, method))
                + "</span>",
                number("concurrency"),
                message("execution_" + purpose, language),
                number("minimum_call_interval_seconds"),
                number("post_call_cooldown_seconds"),
            ]
        )
    rows = list(dict.fromkeys(tuple(row) for row in rows))
    return (
        "<section data-execution-settings><h2>"
        + message("execution_settings_title", language)
        + "</h2><p>"
        + message("execution_settings_note", language)
        + "</p>"
        + table(message("execution_settings_headings", language), rows, pin_method=True)
        + "</section>"
    )


def worker_resources(methods, rows, labels, language):
    """Compare only native worker counters, retaining each metric's missing population."""
    samples = {method: [[], []] for method in methods}
    counts = Counter(row["method"] for row in rows)
    for row in rows:
        response = row.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
        if row["method"] not in samples or not isinstance(usage, dict):
            continue
        cpu = usage.get("process_cpu_seconds")
        memory = usage.get("peak_rss_native_units")
        scale = {"bytes": 1 / 1048576, "KiB": 1 / 1024}.get(usage.get("rss_unit"))
        for index, value in enumerate((cpu, memory)):
            if (type(value) in (int, float) and math.isfinite(value) and value >= 0
                    and (index == 0 or scale is not None)):
                samples[row["method"]][index].append(value if index == 0 else value * scale)
    if not any(values for pair in samples.values() for values in pair):
        return ""
    means = [[statistics.mean(samples[m][i]) if samples[m][i] else None for m in methods]
             for i in range(2)]
    records = [[esc(labels.get(m, m)),
                f"{len(samples[m][0])} / {counts[m] - len(samples[m][0])}",
                record_metric(means[0][i]),
                f"{len(samples[m][1])} / {counts[m] - len(samples[m][1])}",
                record_metric(means[1][i])] for i, m in enumerate(methods)]
    leaders = []
    for metric, values in zip(("worker_cpu_lowest", "worker_memory_lowest"), means, strict=True):
        observed = [value for value in values if value is not None]
        names = " / ".join(labels.get(m, m) for m, value in zip(methods, values, strict=True)
                           if observed and value == min(observed))
        leaders.append(message(metric, language, methods=esc(names or "—")))
    return ("<section data-worker-resources><h2>" + message("worker_resources_title", language)
            + "</h2><p data-resource-leaders>" + "; ".join(leaders) + "</p><p>"
            + message("worker_resources_scope", language) + "</p>"
            + record_comparison(message("worker_resources_headings", language), records,
                                {2: ("worker-cpu", "min", means[0]),
                                 4: ("worker-rss", "min", means[1])},
                                pin_method=True) + "</section>")


def check_worker_resources(output, methods, rows, labels, language):
    expected = BeautifulSoup(worker_resources(methods, rows, labels, language), "html.parser")
    for route in ("index", "interpretation"):
        page = BeautifulSoup((output / language / (route + ".html")).read_text(encoding="utf-8"),
                             "html.parser")
        if page.select("[data-worker-resources]") != expected.select("[data-worker-resources]"):
            raise ValueError("Worker resource comparison differs from retained measurements")


def operating_metrics(methods, rows, prices, lang, labels, *, compared=None, execution=None):
    """Home-page latency and price, with the measured population kept explicit."""
    price_table(methods, prices, lang, labels)
    compared = set(methods if compared is None else compared)
    comparable = execution is None or latency_comparable(execution, methods)
    if not comparable:
        compared.clear()
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
                esc(price_remarks(prices[method], lang, summary=True))
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
            pin_method=True,
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
    if not comparable:
        note += " " + message("latency_loads_differ", lang)
    return (
        "<section data-home-operations><h2>"
        + (message("latency_and_pricing", lang))
        + "</h2><p>"
        + note
        + "</p>"
        + str(soup)
        + "</section>"
    )


def displayed_values(value, language, *, reference=False):
    if value is None or value == []:
        return message("no_reference_value" if reference else "no_value_returned", language)
    values = value if isinstance(value, list) else [value]
    return (
        '<ul class="field-values">'
        + "".join(
            "<li>"
            + esc(item if isinstance(item, str) else json.dumps(item, ensure_ascii=False))
            + "</li>"
            for item in values
        )
        + "</ul>"
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
                displayed_values(value["expected"], lang, reference=True),
                displayed_values(value["actual"], lang),
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


def case_comparison_controls(methods, labels, language):
    if len(methods) <= 2:
        return ""

    def options(selected=()):
        return "".join(
            '<option value="'
            + esc(method)
            + '"'
            + (" selected" if method in selected else "")
            + ">"
            + esc(labels.get(method, method))
            + "</option>"
            for method in methods
        )

    return (
        "<details data-case-comparison><summary>"
        + message("case_comparison", language)
        + '</summary><div class="controls"><label>'
        + message("comparison_baseline", language)
        + "<select data-comparison-baseline>"
        + options(methods[:1])
        + "</select></label><label>"
        + message("comparison_methods", language)
        + '<select multiple data-comparison-methods size="4">'
        + options(methods[1:])
        + "</select></label><label>"
        + message("comparison_relation", language)
        + "<select data-comparison-relation>"
        + "".join(
            '<option value="' + value + '">' + message("comparison_" + key, language) + "</option>"
            for value, key in [
                ("", "off"),
                ("different", "different"),
                ("baseline_only", "baseline_only"),
                ("comparison_only", "comparison_only"),
                ("all_pass", "all_pass"),
                ("all_fail", "all_fail"),
            ]
        )
        + "</select></label></div><p>"
        + message("comparison_scope", language)
        + "</p></details>"
    )


def check_case_comparison_controls(output, methods, labels, language):
    if len(methods) <= 2:
        return
    soup = BeautifulSoup(
        (output / language / "results.html").read_text(encoding="utf-8"), "html.parser"
    )
    expected = BeautifulSoup(case_comparison_controls(methods, labels, language), "html.parser")
    if str(soup.select_one("[data-case-comparison]")) != str(
        expected.select_one("[data-case-comparison]")
    ):
        raise ValueError("Missing or changed same-page comparison controls")
    if any(not row.get("data-case") for row in soup.select("tr[data-status]")):
        raise ValueError("Same-page comparison requires a case identity on every result")


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
            + '></label><output data-result-count aria-live="polite"></output>'
            + case_comparison_controls(methods, names, lang)
            + "</form>"
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


def audit_explanation(audit, rows, methods, labels, lang, *, reference_view, core_html=None, authored_html=""):
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
                **dict(value0=esc(" / ".join(sorted({r["model"] for r in
                    audit.get("reviewers", [audit["reviewer"]])})))) if zh else dict(),
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
            pin_method=True,
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
                if index not in (0, 2, 3, 4, 5, 6) and not cell.has_attr("data-completion-metric"):
                    cell.decompose()
        compact.attrs = {
            "data-interpretation-comparison": "true",
            "data-completion-metrics": "true",
            "data-highlight-ties": "all",
            "data-method-column": "true",
            "data-sortable": "",
        }
        interpretation += (
            "<h2>"
            + (message("compact_core_comparison", lang))
            + "</h2>"
            + '<div class="table-wrap" tabindex="0">'
            + str(compact)
            + "</div>"
            + completion_links(lang, reference_view)
        )
    interpretation += authored_html
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
        representative = (
            failures[:1]
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
                    (diagnosis.get("cause") if failures else None)
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
            if row.get("compatibility") == "structured_output":
                differences = [
                    [
                        FIELD_LABELS[field][0 if zh else 1],
                        displayed_values(value.get("expected"), lang, reference=True),
                        displayed_values(value.get("actual"), lang),
                    ]
                    for field, value in row["field_results"].items()
                    if value["status"] != "correct"
                ]
                interpretation += (
                    "</p>"
                    + table(
                        [
                            message("field", lang),
                            message("reference", lang),
                            message("output", lang),
                        ],
                        differences,
                        attrs="data-interpretation-example",
                    )
                    + "<p>"
                )
            interpretation += (
                f'<a href="case-{row["case_id"]}.html#{method}">'
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
            "WEB_EXTRACT_PUBLICATION_HOSTS", repository_root() / ".env"
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


def copy_input_profile(output, profile):
    """Retain the privacy-projected input authority for release exceptions."""
    source = profile.read_bytes()
    original = json.loads(source)
    if type(original.get("local_example", False)) is not bool:
        raise ValueError("Input profile local_example must be a boolean")
    raw, receipt = project_evidence_view(
        "public-evidence/provenance/input-profile.json",
        source,
        role="native_response",
        content_format="json",
        host_names=publication_host_names(),
    )
    projected = json.loads(raw)
    if projected.get("local_example", False) != original.get(
        "local_example", False
    ) or projected.get("dataset") != original.get("dataset"):
        raise ValueError("Input profile privacy projection changed benchmark authority")
    path = output / receipt["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return receipt


def check_input_profile(output, manifest):
    binding = manifest.get("input_profile", {})
    if binding.get("path") != "public-evidence/provenance/input-profile.json":
        raise ValueError("Missing bound input profile")
    path = output / binding["path"]
    if (
        not path.is_file()
        or path.is_symlink()
        or digest(path.read_bytes()) != binding.get("published_sha256")
        or binding.get("original_sha256") != manifest.get("profile_sha256")
    ):
        raise ValueError("Report input profile binding changed")
    profile = read_json(path)
    local = profile.get("local_example", False)
    kind = "local-example" if local else "published-upstream"
    if type(local) is not bool or manifest.get("dataset_kind") != kind:
        raise ValueError("Report dataset kind differs from its bound input profile")
    if (
        manifest.get("mode") in {"field-extraction-cohort", "field-extraction-suite"}
        and manifest.get("local_example") is not local
    ):
        raise ValueError("Report example flag differs from its bound input profile")
    return local


def metric_definitions(language, *, records):
    labels = message("guide_record_labels" if records else "guide_product_labels", language)
    meanings = message("guide_record_meanings" if records else "guide_product_meanings", language)
    result = [
        dict(
            id="quality-" + str(i),
            label=label,
            meaning=meaning,
            unit=message("guide_rate_unit" if records and i == 2 else "guide_score_unit", language),
            population=message("guide_quality_population", language),
            direction=message("guide_higher", language),
        )
        for i, (label, meaning) in enumerate(zip(labels, meanings, strict=True))
    ]
    for i, key in enumerate(("page_exact", "empty_fields")):
        result.append(
            dict(
                id="completion-" + key,
                label=message("completion_headers", language)[i + 1],
                meaning=message("guide_completion_meanings", language)[i],
                unit=message("guide_rate_unit", language),
                population=message("guide_completion_populations", language)[i],
                direction=message("guide_higher", language),
            )
        )
    for i, (label, meaning) in enumerate(
        zip(
            message("guide_latency_labels", language),
            message("guide_latency_meanings", language),
            strict=True,
        )
    ):
        result.append(
            dict(
                id="operations-" + str(i),
                label=label,
                meaning=meaning,
                unit=message("guide_cost_unit" if i == 3 else "guide_seconds_unit", language),
                population=message(
                    "guide_price_population" if i == 3 else "guide_time_population", language
                ),
                direction=message("guide_context" if i == 3 else "guide_lower", language),
            )
        )
    return result


def report_metric_guide(language, *, records):
    from harness.reporting.presentation import metric_guide

    return metric_guide(
        metric_definitions(language, records=records),
        title=message("guide_title", language),
        headings=message("guide_headings", language),
    )


def retry_summary(statistics_by_method, labels, language="zh"):
    return (
        "<section data-retry-summary><h2>"
        + message("retry_title", language)
        + "</h2><p>"
        + message("retry_explanation", language)
        + "</p>"
        + table(
            message("retry_headings", language),
            [
                [esc(labels[m]), f"{v['retry_seconds']:.3f}", str(v["retry_missing_timers"])]
                for m, v in statistics_by_method.items()
            ],
            pin_method=True,
        )
        + "</section>"
    )


def dataset_controls(language):
    label = message("dataset_search", language)
    return (
        '<form data-dataset-controls class="controls"><label>'
        + label
        + '<input type="search" aria-label="'
        + label
        + '"></label><output></output></form>'
    )


def method_examples(output, binding, language="zh"):
    """Show the page, requested fields and returned values before developer records."""
    if not binding:
        return ""
    path = Path(binding["path"])
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe method example path")
    raw = (output / path).read_bytes()
    if digest(raw) != binding["sha256"]:
        raise ValueError("Method example source changed")
    groups = json.loads(raw)["benchmarks"]
    if not groups or len({g["id"] for g in groups}) != len(groups):
        raise ValueError("Method examples require distinct benchmarks")
    parts = [
        "<section data-method-examples><h2>" + message("examples_reader_title", language) + "</h2>"
    ]
    for group in groups:
        entries = group["methods"]
        if not entries or len({e["id"] for e in entries}) != len(entries):
            raise ValueError("Method examples require distinct participants")
        rows, cards = [], []
        for entry in entries:
            identifier = "example-" + digest((group["id"] + "/" + entry["id"]).encode())[:16]
            example = entry.get("example")
            if example is None:
                if not entry.get("unavailable_reason"):
                    raise ValueError("An unexecuted method requires its example limitation")
                link = esc(entry["unavailable_reason"])
            else:
                if not example.get("case_id") or not isinstance(example.get("request"), dict):
                    raise ValueError("Method example lacks a retained request or case identity")
                if "response" not in example or not example.get("source", {}).get("sha256"):
                    raise ValueError("Method example lacks its native response provenance")
                native = example["response"]
                try:
                    for key in entry.get("output_path", []):
                        native = native[key]
                except (KeyError, IndexError, TypeError) as error:
                    raise ValueError(
                        "Example output path is not a native response field"
                    ) from error
                request = example["request"]
                source = (
                    example.get("input_preview_url")
                    or request.get("fixture_url")
                    or request.get("source_url")
                )
                if "fields" in example:
                    projected = normalize(
                        entry["id"],
                        example["response"],
                        **({"schema": request["schema"]} if example.get("record_schema") else {}),
                    )
                    if projected != example["fields"]:
                        raise ValueError("Example fields differ from the native response")
                    values = example["fields"] or {}
                else:
                    values = native if isinstance(native, dict) else {"result": native}

                def readable(value):
                    if value is None:
                        return message("example_missing", language)
                    if isinstance(value, list):
                        return (
                            message("list_separator", language).join(
                                readable(item) for item in value
                            )
                            if value
                            else message("example_empty", language)
                        )
                    if isinstance(value, dict):
                        return json.dumps(value, ensure_ascii=False)
                    return str(value)

                requested_fields = entry.get("requested_fields", list(values))
                output_table = table(
                    [message("field", language), message("output", language)],
                    [
                        [esc(entry.get("field_labels", {}).get(k, k)), esc(readable(values.get(k)))]
                        for k in requested_fields
                    ],
                )
                record = {
                    "case_id": example["case_id"],
                    "request": request,
                    "http_status": example.get("http_status"),
                    "response": example["response"],
                    "source": example["source"],
                }
                elapsed = example.get("elapsed_seconds")
                timing = (
                    message("examples_timing", language, seconds=f"{elapsed:.3f}")
                    if isinstance(elapsed, (int, float))
                    else ""
                )
                cards.append(
                    '<details class="method-example" id="'
                    + identifier
                    + '" data-method-example="'
                    + esc(group["id"] + "/" + entry["id"])
                    + '"><summary>'
                    + esc(entry["label"])
                    + '</summary><div class="example-pair">'
                    + "<div><h4>"
                    + message("examples_input", language)
                    + '</h4><p><a href="'
                    + esc(source or "#")
                    + '">'
                    + message("open_page", language)
                    + "</a></p><p>"
                    + esc(entry["field_contract"])
                    + "</p><p>"
                    + message("examples_delivery", language)
                    + esc(entry["input_mode"])
                    + "</p></div>"
                    + "<div><h4>"
                    + message("examples_output", language)
                    + "</h4>"
                    + output_table
                    + "<p>"
                    + timing
                    + "</p></div></div><details><summary>"
                    + message("examples_record", language)
                    + "</summary><pre>"
                    + esc(json.dumps(record, ensure_ascii=False, indent=2))
                    + "</pre></details></details>"
                )
                link = (
                    '<a href="#' + identifier + '">' + message("examples_open", language) + "</a>"
                )
            rows.append(
                [esc(entry[k]) for k in ("label", "implementation", "input_mode", "field_contract")]
                + [link]
            )
        parts.append(
            '<section id="examples-'
            + esc(group["id"])
            + '"><h3>'
            + esc(group["label"])
            + "</h3><p>"
            + esc(group["description"])
            + "</p>"
            + table(
                message("examples_columns", language)[:4]
                + message("examples_columns", language)[-1:],
                rows,
            )
            + '<div class="method-examples">'
            + "".join(cards)
            + "</div></section>"
        )
    return "".join(parts) + "</section>"


def check_method_examples(output, manifest):
    if not manifest.get("method_examples"):
        return
    for language in manifest["languages"]:
        expected = BeautifulSoup(
            method_examples(output, manifest["method_examples"], language), "html.parser"
        )
        actual = BeautifulSoup(
            scope_record_html(
                (output / language / "protocol.html").read_text(encoding="utf-8"),
                manifest.get("site_prefix", ""),
                restore=True,
            ),
            "html.parser",
        ).select_one("[data-method-examples]")
        if actual is not None:
            for anchor in actual.select("a"):
                anchor.attrs.pop("target", None)
                anchor.attrs.pop("rel", None)
                if anchor.get("href", "").startswith(language + "/protocol.html#"):
                    anchor["href"] = "#" + anchor["href"].split("#", 1)[1]
        if str(actual) != str(expected.select_one("[data-method-examples]")):
            raise ValueError("Method examples differ from their native input/output evidence")


def build(output: Path, content: Path, *, home: Path, run=None, language=None, profile=PROFILE):
    from harness.web_extract.dataset import require_product_reference

    require_external_output(output)
    if content.is_file() and read_json(content).get("mode") == "field-extraction-suite":
        from harness.web_extract.report_suite import build as build_suite

        return build_suite(output, content, profile=profile, language=language)
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
        page.get(part, {}).get("zh") and not page.get(part, {}).get("en")
        for page in model["pages"].values()
        for part in ("body", "intro")
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
        from harness.web_extract.dataset import distribution_identity

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
    instance = read_json(profile)
    historical_methods = instance.get(
        "historical_f1",
        history["scores"] if instance.get("local_example") else read_json(PROFILE)["historical_f1"],
    )
    history = {
        **history,
        "scores": {m: scores for m, scores in history["scores"].items() if m in historical_methods},
    }
    selections = (
        model.get("evidence_runs", []) if run is None else [{"run": str(run), "methods": None}]
    )
    rows = []
    bindings = []
    for selection in selections:
        source = resolve_run_path(home, selection["run"])
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
            rows.append(redact_account_endpoint(r))
        bindings.append(cohort_source_binding(source, selection, contract, extra))
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
    if audit is not None:
        check_product_audit(home, truth, rows, audit, profile=profile)
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
    input_profile = copy_input_profile(output, profile)
    buckets = (
        {
            m: {r["case_id"]: r for r in rows if r["method"] == m}
            for m in cohort_plan["participants"]
        }
        if cohort
        else {f"{r['method']}-{r['case_id']}": r for r in rows}
    )
    public_receipts, public_rows = [input_profile], []
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
    completion = None
    completion_audit = None
    if cohort:
        if audit:
            source = content.parent / model["audit"]["path"]
            raw, receipt = project_evidence_view(
                "public-evidence/audit.json",
                source.read_bytes(),
                role="native_response",
                content_format="json",
                host_names=publication_host_names(),
            )
            (output / "public-evidence/audit.json").write_bytes(raw)
            public_receipts.append(receipt)
            completion_audit = {
                "path": "public-evidence/audit.json",
                "sha256": digest(raw),
                "source_sha256": model["audit"]["sha256"],
            }
        completion = completion_bundle(
            list(cohort_plan["participants"]),
            rows,
            records=False,
            complete_methods=[m for b in bindings if b.get("certified") for m in b["scores"]],
            excluded_fields=audit.get("excluded_fields", {}) if audit else None,
        )
        save_json(output / "data/completion-metrics.json", completion)
    page_intros = {}
    for lang in languages:
        lab = LABELS[lang]
        directory = output / lang
        directory.mkdir()
        bodies = {p: model["pages"].get(p, {}).get("body", {}).get(lang, "") for p in PAGES}
        bodies["protocol"] += method_examples(output, model.get("method_examples"), lang)
        configurations = selected_execution_contracts(bindings)
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
        bodies["protocol"] += execution_settings(configurations, methods, names, lang)
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
                pin_method=True,
            )
            + bodies["index"]
        )
        if not cohort:
            bodies["index"] += operating_metrics(
                methods, rows, model.get("prices", {}), lang, names, execution=configurations
            )
            bodies["accounts"] += price_table(methods, model.get("prices", {}), lang, names)
        if cohort:
            core, timings = cohort_metrics(
                methods, rows, bindings, len(truth), lang, names, truth=truth
            )
            core = with_completion_columns(core, completion, methods, lang)
            bodies["index"] = (
                core
                + report_metric_guide(lang, records=False)
                + operating_metrics(methods, rows, model.get("prices", {}), lang, names,
                                    execution=configurations)
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
                audit, rows, methods, names, lang, reference_view=completion["reference_view"] if completion else None, core_html=core if cohort else None,
                authored_html=bodies["interpretation"],
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
        resources = worker_resources(methods, rows, names, lang)
        for route in ("index", "interpretation"):
            bodies[route] += resources
        if cohort:
            if not audit:
                interpretation_core = BeautifulSoup(core, "html.parser")
                interpretation_core.table["data-interpretation-comparison"] = "true"
                bodies["interpretation"] = str(interpretation_core) + bodies["interpretation"]
            bodies["protocol"] += "<p>" + message("completion_protocol", lang) + "</p>"
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
        hist = table(headers, hr, attrs='data-history-table="true"', pin_method=True)
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
        case_bodies = {}
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
                cells = [f'<a href="case-{cid}.html#{method}">{esc(names.get(method, method))}</a>']
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
                    pin_method=True,
                    sort_values={5: [row.get("elapsed_seconds") for row in case_rows]},
                )
                + "</article>"
            )
            case_bodies[cid] = case_body
        bodies["dataset"] += table(
            [lab[14], message("source", lang), message("snapshot", lang), lab[19]],
            dr,
            attrs="data-dataset-inventory data-sortable",
            inventory=True,
        )
        bodies["dataset"] = dataset_controls(lang) + bodies["dataset"]
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
            url = f"case-{cid}.html#{r['method']}"
            error = (
                ""
                if ok
                else (r["native_error"] or message("no_recognizable_product_fields_returned", lang))
            )
            evidence = "../public-evidence/" + (r["method"] if cohort else key) + ".json"
            evidence_key = f' data-key="{cid}"' if cohort else ""
            detail = f'<article data-report-detail="{"passed" if ok else "failed"}"><div data-result-summary><h2>{status}</h2><p>{esc(names.get(r["method"], r["method"]))}</p><p><code>{cid}</code></p></div>'
            detail += (
                f'<p class="case-links"><a href="case-{cid}.html">'
                + (message("compare_all_methods", lang))
                + f'</a><a href="source-{cid}.html">'
                + (message("source_and_annotations_cf191383", lang))
                + "</a></p>"
            )
            detail += (
                (
                    "<details><summary>"
                    + message("original_field_judgments", lang)
                    + "</summary>"
                    + field_table(r, lang)
                    + "</details>"
                )
                if r.get("review")
                else field_table(r, lang)
            )
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
                    "<details data-reviewer-findings><summary>"
                    + message("reviewer_findings", lang)
                    + "</summary>"
                )
                detail += "<p>" + message("reviewer_reconciliation", lang) + "</p>"
                detail += table(message("reviewer_findings_columns", lang), review_rows)
                detail += "<p>" + esc(findings["reviewer_projection_issue"]) + "</p></details>"
            if r.get("diagnosis") and r.get("compatibility") != "structured_output":
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
                detail += '<p data-failure-diagnosis>{part0}</p><details data-result-logs open><summary>{part1}</summary><pre data-kind="error">{part2}</pre></details>'.format(
                    part0=message("no_scoreable_fields_returned_see_the_program_error_below", lang),
                    part1=lab[21],
                    part2=esc(error),
                )
            dialog_id = "evidence-" + r["method"]
            detail += f'<p><button data-call-log-open="{dialog_id}" data-src="{evidence}"{evidence_key}>{message("developer_record", lang)}</button></p><dialog id="{dialog_id}" aria-labelledby="{dialog_id}-title"><h2 id="{dialog_id}-title">{message("developer_record", lang)}</h2><button data-call-log-close>{lab[10]}</button><pre></pre></dialog></article>'
            case_bodies[cid] += (
                f'<details data-result-panel id="{r["method"]}"><summary>'
                + esc(names.get(r["method"], r["method"]))
                + " · "
                + status
                + "</summary>"
                + detail
                + "</details>"
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
                f'<tr data-case="{cid}" data-status="{"pass" if ok else "fail"}" data-method="{r["method"]}" data-stage="{r["failure_stage"]}" data-outcome="{"correct" if correct and ok else "wrong"}" data-search="{esc(search, quote=True)}" {data_fields}><td>{esc(names.get(r["method"], r["method"]))}</td><td><a href="{url}"><code>{cid[:12]}</code></a></td><td>{status}</td>{field_cells}<td>{r.get("http_status") or "—"}</td><td data-sort="{r.get("elapsed_seconds", "")}">{elapsed}</td><td><button data-call-log-open="diagnosis" data-src="{evidence}"{evidence_key}>{lab[9]}</button> · <a href="{url}">'
                + (message("full_result", lang))
                + "</a></td></tr>"
            )
        for cid, case_body in case_bodies.items():
            (directory / ("case-" + cid + ".html")).write_text(
                page(
                    lang,
                    "results",
                    message("same_case_comparison", lang) + cid[:12],
                    case_body,
                    detail=True,
                    languages=languages,
                    state=state,
                ),
                encoding="utf-8",
            )
        if cohort:
            observed = {(r["method"], r["case_id"]) for r in rows}
            for method in methods:
                for cid in sorted(truth):
                    if (method, cid) not in observed:
                        search = esc(cid + " " + truth[cid].get("url", ""), quote=True)
                        inventory.append(
                            f'<tr data-case="{cid}" data-status="pending" data-method="{method}" data-outcome="pending" data-search="{search}"'
                            + "".join(
                                " data-" + field + '="pending"' for field in (*FIELDS, "OutOfStock")
                            )
                            + f"><td>{esc(names.get(method, method))}</td><td><code>{cid[:12]}</code></td><td>{message('not_executed', lang)}</td>"
                            + "<td>—</td>" * 6
                            + "</tr>"
                        )
        if not cohort:
            bodies["index"] = report_metric_guide(lang, records=False) + bodies["index"]
        bodies["results"] += (
            result_controls(methods, rows, names, lang)
            + '<div class="table-wrap inventory" tabindex="0"><table data-sortable data-method-column="true"><thead><tr>'
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
            intro = authored_intro(model, p, lang)
            if p == "interpretation":
                bodies[p] = bind_model_tables(
                    output, model.get("model_comparison"), lang, bodies[p])
            rendered = page(
                lang, p, titles[p], bodies[p], languages=languages, state=state, intro=intro
            )
            (directory / (p + ".html")).write_text(
                rendered,
                encoding="utf-8",
            )
            if intro:
                page_intros[lang + "/" + p + ".html"] = page_intro_hash(rendered)
    if "en" not in languages:
        (output / "en").mkdir()
        (output / "en/index.html").write_text(
            message("english_not_generated_notice", "en"), encoding="utf-8"
        )
    (output / "index.html").write_text(
        f'<!doctype html><html lang="{language}"><head><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={language}/index.html"><title>Web Extract</title></head><body><a href="{language}/index.html">Web Extract</a></body></html>',
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
            "template": template_identity("web-extract"),
            "page_intros": page_intros,
            "model_comparison": model.get("model_comparison"),
            "template_version": VERSION,
            "methods": methods,
            "browser_eval": build_version,
            **report_dependencies(),
            "dataset_kind": "published-upstream"
            if not read_json(profile).get("local_example", False)
            else "local-example",
            "mode": state,
            "planned_methods": list(cohort_plan["participants"]) if cohort else None,
            "method_labels": names,
            "prices": model.get("prices", {}),
            "method_examples": model.get("method_examples"),
            "renderer_sha256": digest(Path(__file__).read_bytes()),
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
            "completion_reference_exclusions": audit.get("excluded_fields", {}) if audit else {},
            "completion_audit": completion_audit,
            "content_sha256": digest(content.read_bytes()),
            "profile_sha256": digest(profile.read_bytes()),
            "input_profile": input_profile,
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
    _check_rendered_report(output)


def report_dependencies():
    return {
        "asset_sha256": {p.name: digest(p.read_bytes()) for p in ASSETS.iterdir() if p.is_file()},
        "messages_sha256": messages_sha256(),
        "interaction_sha256": digest(Path(__file__).with_name("report_ui.py").read_bytes()),
        "analysis_sha256": digest(Path(__file__).with_name("analysis.py").read_bytes()),
    }


def validate_release_prices(manifest):
    if manifest.get("dataset_kind") == "published-upstream":
        for value in manifest["prices"].values():
            if (
                "usd_per_1000_range" not in value
                and not value.get("pricing_formula")
                and not value.get("cost_unavailable")
            ):
                raise ValueError(
                    "Formal releases require normalized price ranges or a billing formula"
                )


def check_cost_disclosures(output, manifest):
    """Bind missing monetary telemetry to its population and frozen source plan."""
    selected = {
        method: value
        for method, value in manifest.get("prices", {}).items()
        if value.get("cost_unavailable") is not None
    }
    if not selected:
        return
    if manifest.get("mode") == "field-extraction-cohort":
        sources = read_json(output / "public-evidence/sources.json")
        plans = {method: item["plan_sha256"] for method, item in sources.items()}
    else:
        plans = {
            method: item.get("source_plan_sha256")
            for item in manifest.get("source_runs", [])
            for method in (item.get("methods") or item.get("contract", {}).get("methods", []))
        }
    for method, value in selected.items():
        disclosure = cost_unavailability(value, manifest["primary_language"])
        for lang in manifest["languages"]:
            cost_unavailability(value, lang)
        evidence = disclosure["evidence"]
        relative = Path(evidence["path"])
        path = output / relative
        if (
            disclosure["population_cases"] != manifest["case_count"]
            or relative.is_absolute()
            or ".." in relative.parts
            or not relative.parts
            or relative.parts[0] != "assets"
            or not path.is_file()
            or path.is_symlink()
            or unsafe_parents(path)
            or digest(path.read_bytes()) != evidence["sha256"]
            or manifest["files"].get(relative.as_posix()) != evidence["sha256"]
            or not re.fullmatch(r"[0-9a-f]{64}", plans.get(method) or "")
        ):
            raise ValueError("Cost disclosure population or evidence differs")
        expected = {
            "schema": "web-extract-cost-disclosure/1",
            "method": method,
            "case_count": disclosure["population_cases"],
            "total_charge_receipts": disclosure["population_cases"] - disclosure["missing_cases"],
            "source_plan_sha256": plans[method],
        }
        observed = read_json(path)
        if observed != expected or any(
            type(observed[key]) is not type(item) for key, item in expected.items()
        ):
            raise ValueError("Cost disclosure does not match its frozen source")


def check_price_coverage(output, methods, prices, language, labels):
    accounts = BeautifulSoup(
        (output / language / "accounts.html").read_text(encoding="utf-8"), "html.parser"
    )
    expected = BeautifulSoup(
        price_table(methods, prices, language, labels), "html.parser"
    ).select_one("[data-price-table]")
    for anchor in expected.select("[data-cost-evidence]"):
        anchor["href"] = anchor["href"].removeprefix("../")
    actual = accounts.select("[data-price-table]")
    if len(actual) != 1 or actual[0] != expected:
        raise ValueError("Cost/pricing coverage differs from bound source")


def check_reader_contract(output, manifest, *, release=False):
    report_editions(manifest)
    validate_catalog()
    if any(manifest.get(key) != value for key, value in report_dependencies().items()):
        raise ValueError("Report client dependencies changed; rebuild required")
    check_input_profile(output, manifest)
    check_cost_disclosures(output, manifest)
    for route, checksum in manifest.get("page_intros", {}).items():
        if route not in manifest["files"] or Path(route).is_absolute() or ".." in Path(route).parts:
            raise ValueError("Unsafe report page intro binding")
        document = BeautifulSoup((output / route).read_text(encoding="utf-8"), "html.parser")
        nodes = document.select("main > [data-page-intro]")
        if len(nodes) != 1 or digest(str(nodes[0]).encode()) != checksum:
            raise ValueError("Report page intro is missing or changed")
        if not intro_is_correctly_placed(document, nodes[0], route, manifest.get("mode")):
            raise ValueError("Report page intro moved from its declared position")
    if release:
        validate_release_prices(manifest)
    methods = manifest.get("planned_methods") or manifest.get("methods", [])
    labels = manifest.get("method_labels", {})
    for language in manifest["languages"]:
        home_page = BeautifulSoup(
            (output / language / "index.html").read_text(encoding="utf-8"), "html.parser")
        first = home_page.main.find(True, recursive=False)
        core = home_page.select_one("[data-core-metrics]")
        if manifest.get("mode") in {"full-cohort-progress", "field-extraction-cohort"} and (
            core is None or (core is not first and first not in core.parents)
        ):
            raise ValueError("Core comparison must precede authored home-page content")
        interpretation = BeautifulSoup(
            (output / language / "interpretation.html").read_text(encoding="utf-8"),
            "html.parser",
        )
        model_tables = model_comparison_tables(output, manifest.get("model_comparison"), language)
        if len(interpretation.select("[data-model-validation], [data-model-screening]")) != len(
            model_tables
        ):
            raise ValueError("Model comparison requires evidence-bound tables")
        for name, markup in model_tables.items():
            nodes = interpretation.select("[data-model-" + name + "]")
            expected_table = BeautifulSoup(markup, "html.parser").select_one("table")
            if len(nodes) != 1 or nodes[0].select_one("table") != expected_table:
                raise ValueError("Model comparison table differs from scored evidence")
        check_case_comparison_controls(output, methods, labels, language)
        if manifest.get("mode") == "field-extraction-cohort":
            sources = read_json(output / "public-evidence/sources.json")
            configurations = selected_execution_contracts(sources.values())
        else:
            configurations = selected_execution_contracts(manifest["source_runs"])
        actual = BeautifulSoup(
            (output / language / "protocol.html").read_text(encoding="utf-8"), "html.parser"
        )
        expected = BeautifulSoup(
            execution_settings(configurations, methods, labels, language), "html.parser"
        )
        sections = actual.select("[data-execution-settings]")
        if len(sections) != 1 or [
            row.get_text(" ", strip=True) for row in sections[0].select("tr")
        ] != [row.get_text(" ", strip=True) for row in expected.select("tr")]:
            raise ValueError("Report execution settings differ from measured contracts")
    check_method_examples(output, manifest)
    from harness.reporting.presentation import check_metric_guide

    for lang in manifest["languages"]:
        home_page = (output / lang / "index.html").read_text(encoding="utf-8")
        if manifest.get("mode") in ("full-cohort-progress", "field-extraction-cohort"):
            document = BeautifulSoup(home_page, "html.parser")
            main = document.select_one("main")
            current = document.select_one("main table[data-completion-metrics]")
            if current is None:
                raise ValueError("Core comparison must precede authored home-page content")
            while current is not main:
                if any(
                    not isinstance(sibling, Comment)
                    and (getattr(sibling, "name", None) is not None or str(sibling).strip())
                    for sibling in current.previous_siblings
                ):
                    raise ValueError("Core comparison must precede authored home-page content")
                current = current.parent
        check_metric_guide(
            home_page,
            metric_definitions(lang, records=manifest.get("mode") == "field-extraction-cohort"),
            title=message("guide_title", lang),
            headings=message("guide_headings", lang),
        )


def check(output: Path, *, release=False, visual_receipt=None, profile=None, home=None):
    """Validate a report against explicitly selected external authorities."""
    if profile is None or home is None:
        raise ValueError("Report check requires an explicit profile and benchmark home")
    output = open_report(output)
    verify_completion_audit(output, profile, home=home)
    return _check_rendered_report(output, release=release, visual_receipt=visual_receipt)


def _check_rendered_report(output: Path, *, release=False, visual_receipt=None):
    """Internal build self-check of rendered evidence and reader-facing semantics."""
    output = open_report(output)
    manifest = read_json(output / "report-manifest.json")
    if manifest.get("mode") == "field-extraction-suite":
        from harness.web_extract.report_suite import check as check_suite

        return check_suite(output, release=release, visual_receipt=visual_receipt)
    if manifest.get("mode") == "field-extraction-cohort":
        return _check_record_cohort(output, release=release, visual_receipt=visual_receipt)
    link_index = LinkIndex(errors="strict")
    check_report_identity(output, manifest, "web-extract")
    if public_risk((output / "report-manifest.json").read_bytes()):
        raise ValueError("Report metadata has a publication privacy risk")
    check_reader_contract(output, manifest, release=release)
    if manifest["template_version"] != VERSION:
        raise ValueError("Wrong template")
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
    if manifest["renderer_sha256"] != digest(Path(__file__).read_bytes()):
        raise ValueError("Renderer identity changed; rebuild required")
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
        if name != "index.html" and not notice:
            lang = name.split("/")[0]
            expected_primary = [
                f"{lang}/{route}.html"
                for route in ("index", "results", "dataset", "protocol", "interpretation")
            ]
            if (
                [a.get("href") for a in soup.select("nav > a")] != expected_primary
                or len(soup.select("nav details.nav-more a")) != 4
                or len(soup.select(".report-tools #theme")) != 1
                or len(soup.select(".report-tools a")) != 1
            ):
                raise ValueError("Report navigation must separate core pages, resources and tools")
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
            if u.fragment and unquote(u.fragment) not in link_index.page(target).ids:
                raise ValueError("Broken evidence anchor")
    if manifest.get("mode") != "full-cohort-progress":
        rows = [
            read_json(output / view["path"])
            for view in views
            if view["path"] != manifest["input_profile"]["path"]
        ]
        methods = sorted({row["method"] for row in rows})
        names = manifest.get("method_labels", {})
        for lang in manifest["languages"]:
            home_view = BeautifulSoup(
                (output / lang / "index.html").read_text(encoding="utf-8"), "html.parser"
            )
            check_worker_resources(output, methods, rows, names, lang)
            expected = BeautifulSoup(
                operating_metrics(methods, rows, manifest.get("prices", {}), lang, names,
                                  execution=selected_execution_contracts(manifest["source_runs"])),
                "html.parser",
            ).select_one("[data-operating-metrics]")
            for anchor in expected.select('a[href="accounts.html"]'):
                anchor["href"] = lang + "/accounts.html"
            if home_view.select_one("[data-operating-metrics]") != expected:
                raise ValueError("Home latency/pricing differs from retained calls")
            check_price_coverage(output, methods, manifest.get("prices", {}), lang, names)
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
        check_completion_metrics(output, manifest, [r for m in methods for r in data[m].values()])
        for lang in manifest["languages"]:
            soup = BeautifulSoup(
                (output / lang / "index.html").read_text(encoding="utf-8"), "html.parser"
            )
            summary = soup.select('[data-core-metrics="full-cohort"] tbody tr')
            if [r.get("data-method") for r in summary] != methods:
                raise ValueError("Missing or reordered planned participant")
            for method, row in zip(methods, summary, strict=True):
                cells = row.select("td:not([data-completion-metric])")
                executed = cells[1].select_one("[data-execution-count]")
                returned = sum(
                    r.get("compatibility") == "structured_output" for r in data[method].values()
                )
                if (
                    executed is None
                    or executed.get_text() != f"{len(data[method])} / {manifest['case_count']}"
                    or cells[2].get_text(strip=True) != str(returned)
                ):
                    raise ValueError("Full cohort progress count differs")
                for field, cell in zip(
                    ("price", "sku", "availability", "OutOfStock"), cells[3:], strict=True
                ):
                    value = scores.get(method, {}).get(field, {}).get("f1")
                    if cell.get_text(strip=True) != ("—" if value is None else f"{value:.3f}"):
                        raise ValueError("Displayed quality differs from certified scores")
            protocol = BeautifulSoup(
                (output / lang / "protocol.html").read_text(encoding="utf-8"), "html.parser"
            )
            check_price_coverage(output, methods, manifest.get("prices", {}), lang, names)
            rows = [r for method in methods for r in data[method].values()]
            check_worker_resources(output, methods, rows, names, lang)
            expected_home = BeautifulSoup(
                operating_metrics(methods, rows, manifest.get("prices", {}), lang, names,
                                  execution=selected_execution_contracts(manifest["source_runs"])),
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
                    cells = row.select("td:not([data-completion-metric])")
                    if [c.get_text(strip=True) for c in cells[2:]] != [
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
                panels = comparison.select("details[data-result-panel]")
                if len(panels) != len(expected_methods) or {
                    panel.get("id") for panel in panels
                } != set(expected_methods):
                    raise ValueError("Incomplete same-case participant details")
                for method in expected_methods:
                    row = data[method][cid]
                    detail = comparison.select_one(f'details[data-result-panel][id="{method}"]')
                    if detail is None:
                        raise ValueError("Missing same-case participant detail")
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


def check_visual_receipt(output, manifest, visual_receipt, *, routes=PAGES):
    """Verify the shared reader-pass receipt against this exact report tree."""
    if visual_receipt is None:
        raise ValueError("Release requires actual visual coverage")
    visual = read_json(visual_receipt)
    if visual["report_manifest_sha256"] != digest((output / "report-manifest.json").read_bytes()):
        raise ValueError("Visual receipt belongs to a different report")
    required = {(lang, route, "desktop") for lang in manifest["languages"] for route in routes}
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


def completion_authorities(profile, home):
    """Inputs whose bytes must stay frozen during packaging and upload."""
    from harness.web_extract.certification import execution_assessment_authorities

    profile = Path(profile)
    instance = read_json(profile)
    paths = [profile]
    plans = set()

    def include_plan(path):
        paths.append(path)
        if path in plans:
            return
        plans.add(path)
        plan = read_json(path)
        for method in plan.get("execution_assessments", {}):
            authorities = execution_assessment_authorities(plan, method)
            if authorities:
                assessment, receipt = authorities
                paths.extend((assessment, receipt.parent))

    if instance.get("dataset"):
        paths.append(Path(home) / "dataset")
    binding = instance.get("research_report", {}).get("content", {})
    if binding.get("path"):
        content = profile.parent / binding["path"]
        paths.append(content)
        model = read_json(content)
        if model.get("audit"):
            paths.append(content.parent / model["audit"]["path"])
        for key in ("cohort_plan", "reference_revision"):
            binding = model.get(key) or {}
            if binding.get("path"):
                if key == "cohort_plan":
                    include_plan(Path(binding["path"]))
                else:
                    path = Path(binding["path"])
                    paths.append(path)
                    if model.get("mode") == "field-extraction-cohort":
                        from harness.web_extract.dataset import reference_revision_authorities

                        paths.extend(reference_revision_authorities(Path(home), read_json(path)))
        for group in model.get("evidence_runs", []):
            for selection in group.get("segments", [group]):
                paths.append(resolve_run_path(home, selection["run"]))
                if selection.get("plan"):
                    include_plan(Path(selection["plan"]))
                if selection.get("assessment"):
                    paths.append(Path(selection["assessment"]["path"]))
                paths.extend(Path(repair["path"]) for repair in selection.get("repair", []))
    return tuple(dict.fromkeys(paths))


def package(output, archive, *, visual_receipt, profile, home=None):
    """Package only the release-checked report inventory, with no private siblings."""
    from harness.reporting.publication import package_archive

    require_external_output(archive)
    if profile is None or home is None:
        raise ValueError("Packaging requires an explicit profile and benchmark home")
    return package_archive(
        output,
        archive,
        archive.with_suffix(".receipt.json"),
        inputs=(visual_receipt, *completion_authorities(profile, home)),
        validate=lambda: check(
            output, release=True, visual_receipt=visual_receipt, profile=profile, home=home
        ),
        attest=lambda path: {
            "files": len(read_json(output / "report-manifest.json")["files"]) + 1,
            "archive_bytes": path.stat().st_size,
            "expanded_bytes": sum(
                (output / name).stat().st_size
                for name in (
                    *read_json(output / "report-manifest.json")["files"],
                    "report-manifest.json",
                )
            ),
            "archive_sha256": digest(path.read_bytes()),
            "report_manifest_sha256": digest((output / "report-manifest.json").read_bytes()),
        },
    )


def record_cohort_inputs(home, model, *, profile):
    """Load a field-schema cohort through the existing certified/checkpoint owner."""
    from harness.web_extract.analysis import record_statistics
    from harness.web_extract.dataset import benchmark_config
    from harness.web_extract.pipeline import selected_profile

    adapter = benchmark_config(home)["adapter"]
    if adapter is None:
        raise ValueError("Field-schema reports require a registered record scorer")
    instance = read_json(profile)
    local_example = instance.get("local_example", False)
    required_review = instance.get("independent_result_review_scope")
    if required_review not in (None, "all"):
        raise ValueError("Record reports support complete review or original machine scoring")
    if required_review == "all" and not model.get("audit"):
        raise ValueError("Required complete record review is missing")
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
        "scoring_cases": truth,
        "reference_policy": record_reference_policy(home),
        "audit": None,
    }
    native_rows = []
    seen = set()
    for selection in selections:
        segments = selection.get("segments", [selection])
        if not segments:
            raise ValueError("Record source segments must cover the declared population")
        rows, scopes, receipts, method, configuration = [], set(), [], None, None
        for segment in segments:
            source_home = Path(segment["home"]) if "home" in segment else home
            if source_home != home:
                validate_dataset(source_home)
            source_truth = read_json(source_home / "dataset/dataset/ground-truth.json")
            from harness.web_extract.snapshots import resource_inventory

            resources = resource_inventory(home)
            expected_resources = {
                name: sha
                for name, sha in resources.items()
                if any(name.startswith("assets/" + cid + "/") for cid in source_truth)
            }
            if resource_inventory(source_home) != expected_resources:
                raise ValueError("Record source segment changed captured resources")
            source_policy = read_json(source_home / "dataset/manifest.json").get(
                "live_reference_policy"
            )
            target_policy = read_json(home / "dataset/manifest.json").get("live_reference_policy")
            if source_policy and (
                not target_policy
                or source_policy["maximum_observation_age_seconds"]
                > target_policy["maximum_observation_age_seconds"]
            ):
                raise ValueError("Record source segment used a weaker live reference policy")
            source_adapter = benchmark_config(source_home)["adapter"]
            if (
                source_adapter is not adapter
                or scopes & set(source_truth)
                or not set(source_truth) <= set(truth)
            ):
                raise ValueError("Record source segments must be disjoint parts of this dataset")
            for cid, reference in source_truth.items():
                if (
                    reference != truth[cid]
                    or adapter.task_schema(source_home, reference)
                    != adapter.task_schema(home, truth[cid])
                    or digest((source_home / "dataset/html" / (cid + ".html")).read_bytes())
                    != digest((home / "dataset/html" / (cid + ".html")).read_bytes())
                ):
                    raise ValueError("Record source segment changed a task, schema or webpage")
            from harness.web_extract.pipeline import input_mode, record_instructions

            if input_mode(source_home) != input_mode(home) or record_instructions(
                source_home
            ) != record_instructions(home):
                raise ValueError("Record source segment changed the input or instruction contract")
            source = resolve_run_path(source_home, segment["run"])
            contract, part_rows, receipt = cohort_source(source, segment, source_home, source_truth)
            part_method = contract["methods"][0]
            if method is not None and method != part_method:
                raise ValueError("Record source segments must use the same participant")
            method = part_method
            source_plan = read_json(Path(segment["plan"]))
            participant = source_plan["participants"][method]
            current_configuration = {
                "native_options": contract.get("native_options") or {},
                "participant": {k: v for k, v in participant.items()
                                if k != "maximum_physical_calls"},
                "pipeline_sha256": contract["pipeline_sha256"],
                "scorer_sha256": source_plan["scorer_sha256"],
            }
            if configuration is not None and current_configuration != configuration:
                raise ValueError(
                    "Record source segments use different runtime configurations; "
                    "register them as separate participants"
                )
            configuration = current_configuration
            if receipt["certified"]:
                segment_scores = record_statistics(
                    source_truth, part_rows, certified=True, scorer=adapter.score,
                    reference_policy=result["reference_policy"],
                )["scores"]
                if segment_scores != receipt["scores"][method]:
                    raise ValueError("Record segment scores differ from the certified scorer")
            scopes.update(source_truth)
            rows.extend(part_rows)
            receipts.append(
                {
                    "run_id": source.name,
                    "case_count": len(source_truth),
                    "participant": participant,
                    "scorer_sha256": source_plan["scorer_sha256"],
                    "contract": contract,
                    "receipt": receipt,
                    "plan_sha256": segment["plan_sha256"],
                }
            )
        if scopes != set(truth):
            raise ValueError("Record source segments omit part of the declared population")
        native_rows.extend(rows)
        if method not in methods or method in seen:
            raise ValueError("Unexpected or duplicated report participant")
        seen.add(method)
        stats = record_statistics(
            truth, rows, certified=receipt["certified"], scorer=adapter.score,
            reference_policy=result["reference_policy"],
        )
        result["methods"][method] = {"statistics": stats, "results": {}}
        result["sources"][method] = cohort_source_binding(
            source, selection, contract, receipt, records=True
        )
        for row in rows:
            result["methods"][method]["results"][row["case_id"]] = record_result_fields(
                truth[row["case_id"]], row, policy=result["reference_policy"]
            )
    if seen != set(methods):
        raise ValueError("Report omits a planned participant")
    if model.get("audit"):
        if any(not result["methods"][method]["statistics"]["certified"] for method in methods):
            raise ValueError("Complete record review requires every planned certified cohort")
        binding = model["audit"]
        raw = Path(binding["path"]).read_bytes()
        if digest(raw) != binding["sha256"]:
            raise ValueError("Record audit differs from its report binding")
        audit = json.loads(raw)
        verify_record_reference(home, model, truth, native_rows, audit)
        result.update(
            audit=audit,
            audit_raw=raw,
            scoring_cases=audit["audited_reference"],
            reference_policy=audit["reference_policy"],
        )
        result["original_statistics"] = {m: result["methods"][m]["statistics"] for m in methods}
        for method in methods:
            rows = [
                {
                    **row,
                    "normalized_fields": normalize(
                        method,
                        row.get("response", {}),
                        schema=adapter.task_schema(home, truth[row["case_id"]]),
                    ),
                }
                for row in native_rows
                if row["method"] == method
            ]
            result["methods"][method] = {
                "statistics": record_statistics(
                    audit["audited_reference"],
                    rows,
                    certified=True,
                    scorer=adapter.score,
                    reference_policy=audit["reference_policy"],
                ),
                "results": {
                    row["case_id"]: record_result_fields(
                        audit["audited_reference"][row["case_id"]],
                        {**row, "review": audit["results"][method + ":" + row["case_id"]]},
                        policy=audit["reference_policy"],
                    )
                    for row in rows
                },
            }
    elif model.get("reference_revision"):
        verify_record_reference(home, model, truth, native_rows, None)
    result["native_rows"] = native_rows
    return result


def verify_record_projection(reference, row):
    """The published native payload must support every scored record value."""
    schema = {"type": "object", "properties": {field: {} for field in reference["values"]}}
    fields = normalize(row["method"], row.get("response", {}), schema=schema)
    if (fields or {}) != (row.get("normalized_fields") or {}):
        raise ValueError("Record native response differs from its scored field projection")


def record_result_fields(reference, row, *, policy=None):
    from harness.web_extract.analysis import record_field_results

    policy = policy or {"unavailable_reference_groups": [], "text_matching": "literal"}
    unavailable = {tuple(group) for group in policy["unavailable_reference_groups"]}
    fields = record_field_results(
        reference,
        row,
        text_matching=policy["text_matching"],
        unavailable_fields=[
            f
            for f in reference["values"]
            if (reference["domain"], reference["site"], f) in unavailable
        ],
    )
    active = [value for value in fields.values() if value["status"] != "unavailable"]
    return {
        **row,
        "field_results": fields,
        "failure_stage": failure_stage(row),
        "native_error": native_error(row),
        "scored_fields": len(active),
        "exact_fields": sum(bool(v["exact"]) for v in active),
        "referenced_fields": sum(bool(v["expected"]) for v in active),
        "hit_fields": sum(bool(v["hit"]) for v in active),
        "status": "call_error"
        if row.get("compatibility") != "structured_output"
        else "unavailable"
        if not active
        else "exact"
        if all(v["exact"] for v in active)
        else "field_difference",
    }


def record_inventory_outcome(row):
    if row["status"] == "unavailable":
        return "unavailable", "unavailable"
    return ("pass", "correct") if row["status"] == "exact" else ("fail", "wrong")


def record_field_rows(row, language):
    def t(key):
        return message("records_" + key, language)

    return [
        [
            esc(row.get("field_labels", {}).get(field, field)),
            displayed_values(f["expected"], language, reference=True),
            displayed_values(f["actual"], language),
            message("outcomes_" + f["status"], language),
            "—" if f["hit"] is None else t("yes") if f["hit"] else t("no"),
            "—" if f["exact"] is None else t("yes") if f["exact"] else t("no"),
        ]
        for field, f in row["field_results"].items()
    ]


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


def record_comparison(headers, rows, metrics, *, sort_values=None, pin_method=False):
    """Use the common table owner and raw-value extrema checks for each metric."""
    soup = BeautifulSoup(
        table(headers, rows, attrs="data-sortable", sort_values=sort_values, pin_method=pin_method),
        "html.parser",
    )
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
                    for child in list(cell.contents):
                        span.append(child.extract())
                    cell.append(span)
    return str(soup)


def record_metric(number, percent=False):
    if number is None:
        return "—"
    return f"{number * 100:.2f}%" if percent else f"{number:.3f}"


def record_review_section(audit, original_reference, original_metrics, labels, language):
    """Present review coverage and original/revised scores once, outside result inventories."""

    def t(key):
        return message("records_" + key, language)

    models = sorted({r["reviewer"]["model"] for r in audit["review_receipts"]})
    changed = sum(
        values != audit["audited_reference"][cid]["values"][field]
        for cid, ref in original_reference.items()
        for field, values in ref["values"].items()
    )
    methods = list(labels)
    before = [original_metrics[m]["scores"]["page_hit_macro"]["f1"] for m in methods]
    after = [audit["audited_scores"][m]["page_hit_macro"]["f1"] for m in methods]
    rows = [
        [esc(labels[m]), record_metric(old), record_metric(new)]
        for m, old, new in zip(methods, before, after, strict=True)
    ]
    return (
        "<section data-record-review><h2>"
        + t("review_summary")
        + "</h2><p>"
        + message(
            "records_review_coverage",
            language,
            reviewed=audit["result_count"],
            total=len(audit["audited_reference"]) * len(audit["audited_scores"]),
            unreviewed=audit["accepted_unreviewed_passes"],
            unresolved=audit["unresolved"],
        )
        + "</p><p>"
        + t("review_models")
        + ": "
        + esc(", ".join(models))
        + "</p><p>"
        + message(
            "records_review_changes",
            language,
            fields=changed,
            groups=len(audit["reference_policy"]["unavailable_reference_groups"]),
        )
        + "</p>"
        + record_comparison(
            [t("method"), t("original_score"), t("reviewed_score")],
            rows,
            {1: ("original-macro-f1", "max", before), 2: ("reviewed-macro-f1", "max", after)},
            pin_method=True,
        )
        + "</section>"
    )


def record_segment_configurations(sources, labels, language):
    """Expose every certified segment's settings instead of implying one runtime."""
    rows = []
    for method, source in sources.items():
        segments = source.get("segments", [])
        for index, segment in enumerate(segments, 1):
            contract = segment["contract"]
            rows.append([
                esc(labels[method]) + " · " + str(index),
                str(segment["case_count"]),
                "<pre>" + esc(json.dumps({
                    "native_options": contract.get("native_options", {}),
                    "participant": segment["participant"],
                }, ensure_ascii=False, sort_keys=True)) + "</pre>",
                "<code>" + esc(contract["pipeline_sha256"]) + "</code>",
                "<code>" + esc(segment["scorer_sha256"]) + "</code>",
            ])
    if not rows:
        return ""
    return (
        '<details id="segment-configurations" data-segment-configurations><summary>'
        + message("segment_configurations", language) + "</summary><p>"
        + message("segment_explanation", language) + "</p>"
        + table(message("segment_headers", language), rows) + "</details>"
    )


def record_preview(home, model, *, local_example, audit=False):
    """Bind retained previews offline; audit hosted bytes only at publication."""
    from harness.web_extract.dataset import input_mode
    from harness.web_extract.snapshots import manifest_receipt, verify_bound_manifest

    if input_mode(home) == "live":
        if model.get("snapshot_base") or model.get("preview_snapshot"):
            raise ValueError("Live report inputs link to original URLs, not snapshots")
        urls = {
            cid: case["url"]
            for cid, case in read_json(home / "dataset/dataset/ground-truth.json").items()
        }
        return {"mode": "live", "case_count": len(urls), "urls": urls}

    base = model.get("snapshot_base")
    if not base:
        raise ValueError("Report requires a hash-bound frozen input preview origin")
    if local_example:
        return {"mode": "synthetic", "fixture_base": base}
    binding = model.get("preview_snapshot", {})
    path = Path(binding.get("path", ""))
    if (
        not path.is_file()
        or path.is_symlink()
        or digest(path.read_bytes()) != binding.get("sha256")
    ):
        raise ValueError("Report requires a hash-bound dataset snapshot receipt")
    saved = read_json(path)
    receipt = saved.get("snapshot_manifest", saved.get("verification", saved))
    if not isinstance(receipt, dict) or receipt.get("mode") != "dataset_manifest":
        raise ValueError("Record previews require a whole-dataset snapshot manifest")
    verification_context = {
        **receipt,
        **({"cloudflare": saved["cloudflare"]} if saved.get("cloudflare") else {}),
        **(
            {"hosting_provider": saved["hosting_provider"]} if saved.get("hosting_provider") else {}
        ),
    }
    manifest_receipt(home, verification_context, base)
    if audit:
        verify_bound_manifest(home, verification_context, base)
    return receipt


def build_record_cohort(output, content, *, home, language, profile):
    """Render registered record schemas through the shared report layout and controls."""
    from collections import Counter

    from harness.web_extract.dataset import benchmark_config

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
    site_prefix = model.get("site_prefix", "")
    if site_prefix and site_prefix != output.name:
        raise ValueError("Nested report prefix must match its output directory")

    def t(key):
        return message("records_" + key, language)

    output.mkdir(parents=True, exist_ok=True)
    (output / "zh").mkdir()
    (output / "en").mkdir()
    (output / "data").mkdir()
    (output / "public-evidence").mkdir()
    (output / "style.css").write_text(CSS, encoding="utf-8")
    (output / "app.js").write_text(JS, encoding="utf-8")
    copy_report_assets(output, content, model)
    input_profile = copy_input_profile(output, profile)
    prices = model["prices"]
    if set(prices) != set(methods):
        raise ValueError("Every participant requires a public pricing contract")
    public_receipts = [input_profile]
    for method in methods:
        bucket = data["methods"][method]["results"]
        for cid, row in bucket.items():
            row["method_label"] = labels[method]
            row["record_schema"] = True
            row["field_labels"] = model.get("field_labels", {})
            row["result_href"] = "zh/" + record_result_href(method, cid)
            row["case_href"] = "zh/" + record_route("c", cid) + ".html"
            bucket[cid] = redact_account_endpoint(row)
        raw, receipt = project_evidence_view(
            "public-evidence/" + method + ".json",
            json.dumps(bucket, ensure_ascii=False).encode(),
            role="native_response",
            content_format="json",
            host_names=publication_host_names(),
        )
        after = json.loads(raw)
        for cid, row in after.items():
            verify_record_projection(truth[cid], row)
        (output / "public-evidence" / (method + ".json")).write_bytes(raw)
        data["methods"][method]["results"] = after
        public_receipts.append(receipt)
    reference_raw, reference_receipt = project_evidence_view(
        "public-evidence/reference.json",
        json.dumps(data["scoring_cases"], ensure_ascii=False).encode(),
        role="scored_answer",
        content_format="json",
        host_names=publication_host_names(),
    )
    (output / "public-evidence/reference.json").write_bytes(reference_raw)
    public_receipts.append(reference_receipt)
    scoring_truth = json.loads(reference_raw)
    audit_binding = None
    if data["audit"] is not None:
        for name, payload, role in (
            (
                "original-reference.json",
                (home / "dataset/dataset/ground-truth.json").read_bytes(),
                "scored_answer",
            ),
            ("audit.json", data["audit_raw"], "native_response"),
        ):
            raw, receipt = project_evidence_view(
                "public-evidence/" + name,
                payload,
                role=role,
                content_format="json",
                host_names=publication_host_names(),
            )
            (output / "public-evidence" / name).write_bytes(raw)
            public_receipts.append(receipt)
            if name == "original-reference.json":
                truth = json.loads(raw)
            else:
                data["audit"] = json.loads(raw)
                audit_binding = {
                    "path": "public-evidence/audit.json",
                    "source_sha256": model["audit"]["sha256"],
                    "sha256": digest(raw),
                }
        save_json(output / "data/original-metrics.json", data["original_statistics"])
    else:
        truth = scoring_truth
    from harness.web_extract.analysis import record_statistics

    adapter = benchmark_config(home)["adapter"]
    for method in methods:
        if (
            record_statistics(
                scoring_truth,
                list(data["methods"][method]["results"].values()),
                certified=stats[method]["certified"],
                scorer=adapter.score,
                reference_policy=data["reference_policy"],
            )
            != stats[method]
        ):
            raise ValueError("Public evidence projection changed measured verdicts or metrics")
    save_json(output / "data/metrics.json", stats)
    completion = completion_bundle(
        methods,
        [r for m in methods for r in data["methods"][m]["results"].values()],
        records=True,
        complete_methods=[m for m in methods if m in compared and stats[m]["certified"]],
    )
    save_json(output / "data/completion-metrics.json", completion)
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

    page_intros = {}

    def write(route, heading, body):
        intro = authored_intro(model, route, language) if route in PAGES else ""
        if route == "interpretation":
            body = bind_model_tables(output, model.get("model_comparison"), language, body)
        rendered = page(
            language, route, heading, body, detail=route not in PAGES,
            languages=("zh",), state="field-extraction-cohort",
            navigation=model.get("navigation"),
            notice=t("reviewed_notice") if audit_binding else None, intro=intro,
        )
        (output / "zh" / (route + ".html")).write_text(
            rendered,
            encoding="utf-8",
        )
        if intro:
            page_intros[language + "/" + route + ".html"] = page_intro_hash(rendered)

    def value(value):
        return esc(json.dumps(value, ensure_ascii=False))

    def score(method, *keys):
        current = stats[method]["scores"] if method in compared else None
        for key in keys:
            current = current.get(key) if current is not None else None
        return current

    metric = record_metric

    def evidence_button(method, cid, dialog_id="diagnosis", *, technical=False):
        return (
            '<button data-call-log-open="'
            + esc(dialog_id)
            + '" data-src="../public-evidence/'
            + esc(method)
            + '.json" data-key="'
            + esc(cid)
            + ('" data-technical-record="true">' if technical else '">')
            + (message("developer_record", language) if technical else t("open"))
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
        "unavailable": message("outcomes_unavailable", language),
        "field_difference": t("field_difference"),
        "call_error": t("call_error"),
    }
    headers = [
        t(key) for key in ("method", "executed", "returned", "macro", "micro", "exact", "details")
    ]

    def method_cell(method):
        label = "<span data-method-label>" + esc(labels[method]) + "</span>"
        if method in skipped:
            label += (
                '<br><small data-exclusion-reason data-excluded-method="'
                + esc(method)
                + '">'
                + message("excluded_comparison", language)
                + ": "
                + esc(skipped[method])
                + "</small>"
            )
        return label

    core_rows = [
        [
            method_cell(m),
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
        sort_values={
            1: [stats[m]["executed"] for m in methods],
            2: [stats[m]["structured"] for m in methods],
        },
        pin_method=True,
    )
    core = with_completion_columns(core, completion, methods, language, before_details=True)
    operations = operating_metrics(
        methods,
        [row for m in methods for row in data["methods"][m]["results"].values()],
        prices,
        language,
        labels,
        compared=compared,
        execution=selected_execution_contracts(data["sources"].values()),
    )
    bodies = {route: model.get("pages", {}).get(route, {}).get("body", "") for route in PAGES}
    if audit_binding:
        bodies["review"] = (
            record_review_section(
                data["audit"],
                truth,
                data["original_statistics"],
                {m: labels[m] for m in compared},
                language,
            )
            + bodies["review"]
        )
    bodies["protocol"] += method_examples(output, model.get("method_examples")) + retry_summary(
        stats, labels
    )
    bodies["protocol"] += execution_settings(
        selected_execution_contracts(data["sources"].values()), methods, labels, language
    )
    bodies["protocol"] += "<p>" + message("completion_protocol", language) + "</p>"
    bodies["index"] = (
        "<section data-core-metrics>"
        + core
        + "</section>"
        + report_metric_guide(language, records=True)
        + operations
        + bodies["index"]
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
        attrs="data-dataset-inventory data-sortable",
        inventory=True,
    )
    bodies["dataset"] = dataset_controls(language) + bodies["dataset"]
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
    bodies["accounts"] += price_table(methods, prices, language, labels) + usage_table(
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
    preview_snapshot = record_preview(
        home, model, local_example=bound_profile.get("local_example", False)
    )
    snapshot_base = preview_snapshot.get("fixture_base")
    for cid, ref in sorted(truth.items()):
        data_route, comparison = record_route("d", cid), record_route("c", cid)
        schema = benchmark_config(home)["adapter"].task_schema(home, ref)
        source = (
            '<article data-source-case="'
            + esc(cid)
            + '"><p>'
            + esc(domain_labels.get(ref["domain"], ref["domain"]))
            + " · "
            + esc(ref["site"])
            + "</p>"
        )
        source += table(
            [t("field"), t("reference"), *([t("qualified_reference")] if audit_binding else [])],
            [
                [
                    esc(field),
                    value(gold),
                    *(
                        [
                            message("outcomes_unavailable", language)
                            if (ref["domain"], ref["site"], field)
                            in {
                                tuple(g)
                                for g in data["reference_policy"]["unavailable_reference_groups"]
                            }
                            else value(scoring_truth[cid]["values"][field])
                        ]
                        if audit_binding
                        else []
                    ),
                ]
                for field, gold in ref["values"].items()
            ],
        )
        source += (
            "<details><summary>"
            + t("schema")
            + "</summary><pre>"
            + value(schema)
            + "</pre></details>"
        )
        source += '<p><a href="' + esc(ref["url"]) + '">' + t("original_url") + "</a></p>"
        if preview_snapshot["mode"] == "live":
            source += (
                '<p><a data-live-source href="'
                + esc(ref["url"])
                + '">'
                + t("live_page")
                + "</a></p>"
            )
        else:
            source += (
                '<p><a href="'
                + esc(snapshot_base + "/" + cid)
                + '">'
                + t("download")
                + '</a></p><iframe sandbox class="frozen-preview" loading="lazy" title="'
                + t("preview")
                + '" src="'
                + esc(snapshot_base + "/" + cid)
                + '"></iframe>'
            )
        write(
            data_route,
            domain_labels.get(ref["domain"], ref["domain"]) + "网页 · " + ref["site"],
            source + "</article>",
        )
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
                    '<tr data-case="'
                    + esc(cid)
                    + '" data-status="pending" data-outcome="pending" data-method="'
                    + esc(method)
                    + '" data-domain="'
                    + esc(ref["domain"])
                    + '" data-site="'
                    + esc(ref["site"])
                    + '"'
                    + ' data-field-outcomes="'
                    + esc(json.dumps({field: "pending" for field in schema["properties"]}))
                    + '"'
                    + ' data-search="'
                    + esc(cid + " " + ref["site"])
                    + '"><td>'
                    + esc(labels[method])
                    + "</td><td>"
                    + esc(cid)
                    + "</td><td>"
                    + esc(domain_labels.get(ref["domain"], ref["domain"]))
                    + "</td><td>"
                    + t("pending")
                    + "</td><td>—</td><td>—</td><td>"
                    + result_link
                    + "</td></tr>"
                )
                continue
            short = status_labels[row["status"]]
            comparisons.append(
                [
                    esc(labels[method]),
                    short,
                    str(row["exact_fields"]) + "/" + str(row["scored_fields"]),
                    metric(row.get("elapsed_seconds")),
                    result_link,
                ]
            )
            body = (
                '<article data-report-detail="'
                + (
                    "unavailable"
                    if row["status"] == "unavailable"
                    else "failed"
                    if row["status"] != "exact"
                    else "passed"
                )
                + '"><section data-result-summary><p>'
                + esc(short)
                + " · "
                + metric(row.get("elapsed_seconds"))
                + " 秒"
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
                [
                    t("field"),
                    t("qualified_reference") if audit_binding else t("reference"),
                    t("actual"),
                    t("outcome"),
                    t("hit"),
                    t("equal"),
                ],
                record_field_rows(row, language),
                attrs="data-record-fields",
            )
            if row["status"] == "call_error":
                body += (
                    '<section data-failure-diagnosis data-result-logs id="result-logs-'
                    + esc(method)
                    + '"><h2>'
                    + message("failure_diagnosis_title", language)
                    + "</h2><p>"
                    + message("failure_stage", language)
                    + ": "
                    + esc(
                        STAGES.get(
                            row["failure_stage"], (row["failure_stage"], row["failure_stage"])
                        )[0 if language == "zh" else 1]
                    )
                    + "</p><details><summary>"
                    + message("provider_error", language)
                    + "</summary>"
                    + '<pre data-kind="error">'
                    + esc(
                        row["native_error"] or json.dumps(row.get("response"), ensure_ascii=False)
                    )
                    + "</pre></details></section>"
                )
            elif row["status"] == "field_difference":
                body += (
                    "<section data-failure-diagnosis data-result-logs><p data-log-unavailable>"
                    + message("result_no_error", language)
                    + "</p></section>"
                )
            body += (
                evidence_button(method, cid, "diagnosis-" + method, technical=True)
                + dialog.replace('id="diagnosis"', 'id="diagnosis-' + esc(method) + '"').replace(
                    "diagnosis-title", "diagnosis-" + method + "-title"
                )
                + "</article>"
            )
            panels.append(panel_start + body + "</details>")
            attrs = (
                ' data-case="'
                + esc(cid)
                + '" data-status="'
                + record_inventory_outcome(row)[0]
                + '" data-outcome="'
                + record_inventory_outcome(row)[1]
                + '" data-scored-fields="'
                + str(row["scored_fields"])
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
                + ' data-field-outcomes="'
                + esc(
                    json.dumps(
                        {field: result["status"] for field, result in row["field_results"].items()},
                        ensure_ascii=False,
                    )
                )
                + '"'
            )
            inventory_rows.append(
                "<tr"
                + attrs
                + "><td>"
                + esc(labels[method])
                + "</td><td>"
                + esc(cid)
                + "</td><td>"
                + esc(domain_labels.get(ref["domain"], ref["domain"]))
                + "</td><td>"
                + esc(short)
                + '</td><td data-sort="'
                + str(row["exact_fields"])
                + '">'
                + str(row["exact_fields"])
                + "/"
                + str(row["scored_fields"])
                + '</td><td data-sort="'
                + (str(row["elapsed_seconds"]) if row.get("elapsed_seconds") is not None else "")
                + '">'
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
            ("<p>" + t("review_scope_note") + "</p>" if audit_binding else "")
            + table(
                [t("method"), t("outcome"), t("exact"), t("timing"), t("details")],
                comparisons,
                pin_method=True,
            )
            + route_link(data_route, t("source"))
            + "".join(panels),
        )
    controls = controls.replace(
        "</form>", case_comparison_controls(methods, labels, language) + "</form>"
    )
    bodies["results"] += (
        controls
        + '<div class="table-wrap inventory" tabindex="0"><table data-sortable data-method-column="true"><thead><tr>'
        + "".join(
            "<th>" + t(k) + "</th>"
            for k in ("method", "case", "domain", "outcome", "exact", "timing", "details")
        )
        + "</tr></thead><tbody>"
        + "".join(inventory_rows)
        + "</tbody></table></div>"
        + dialog
    )
    bodies["interpretation"] = (
        "<section data-interpretation-comparison>"
        + core
        + "</section>"
        + bodies["interpretation"]
    )
    resources = worker_resources(
        methods, [row for m in methods for row in data["methods"][m]["results"].values()],
        labels, language,
    )
    for route in ("index", "interpretation"):
        bodies[route] += resources
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
    navigation_labels = {entry["route"]: entry["label"] for entry in model.get("navigation", [])}
    for route in PAGES:
        write(
            route,
            title
            + " · "
            + (
                navigation_labels[route]
                if route in navigation_labels
                else message("results_and_failures", language)
                if route == "results"
                else EXTRA_LABELS[route][language]
                if route in EXTRA_LABELS
                else LABELS[language][PAGES.index(route)]
            ),
            bodies[route],
        )
    (output / "en/index.html").write_text(
        scope_record_html(message("english_not_generated_notice", "en"), site_prefix),
        encoding="utf-8",
    )
    entry = (site_prefix + "/" if site_prefix else "") + "zh/index.html"
    entry_base = '<base href="../">' if site_prefix else ""
    (output / "index.html").write_text(
        "<!doctype html><html><head>"
        + entry_base
        + '<meta http-equiv="refresh" content="0;url='
        + entry
        + '"></head><body><a href="'
        + entry
        + '">'
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
            "page_intros": page_intros,
            "model_comparison": model.get("model_comparison"),
            **report_dependencies(),
            "local_example": bound_profile.get("local_example", False),
            "dataset_kind": "local-example"
            if bound_profile.get("local_example", False)
            else "published-upstream",
            "input_profile": input_profile,
            "template": template_identity("web-extract"),
            "template_version": VERSION,
            "browser_eval": version,
            "renderer_sha256": digest(Path(__file__).read_bytes()),
            "profile_sha256": digest(profile.read_bytes()),
            "content_sha256": digest(content.read_bytes()),
            "dataset_manifest_sha256": digest((home / "dataset/manifest.json").read_bytes()),
            "dataset_benchmark": read_json(home / "dataset/manifest.json").get("benchmark"),
            "dataset_input_mode": read_json(home / "dataset/manifest.json").get(
                "input_mode", "snapshot"
            ),
            "primary_language": "zh",
            "languages": ["zh"],
            "planned_methods": methods,
            "comparison_methods": compared,
            "skipped_methods": skipped,
            "method_labels": labels,
            "navigation": model.get("navigation"),
            "method_examples": model.get("method_examples"),
            "preview_snapshot": preview_snapshot,
            "prices": prices,
            "case_count": len(truth),
            "observations": sum(s["executed"] for s in stats.values()),
            "complete_methods": [m for m in methods if stats[m]["certified"]],
            "metrics_sha256": digest((output / "data/metrics.json").read_bytes()),
            "public_evidence_views": public_receipts,
            "audit": audit_binding,
            "reference_policy": data["reference_policy"],
            "required_review_scope": bound_profile.get("independent_result_review_scope"),
            "files": files,
        },
        exclusive=True,
    )
    _check_record_cohort(output)


def _check_record_cohort(output, *, release=False, visual_receipt=None):
    """Check the public record matrix and shared presentation semantics."""
    from harness.reporting.presentation import check_presentation
    from harness.web_extract.analysis import record_statistics
    from harness.web_extract.dataset import benchmark_config

    output = open_report(output)
    link_index = LinkIndex(errors="strict")
    manifest = read_json(output / "report-manifest.json")
    site_prefix = manifest.get("site_prefix", "")
    if site_prefix and site_prefix != output.name:
        raise ValueError("Nested report prefix differs from its directory")
    site_root = output.parent if site_prefix else output

    def read_html(path):
        return scope_record_html(path.read_text(encoding="utf-8"), site_prefix, restore=True)

    check_report_identity(output, manifest, "web-extract")
    if public_risk((output / "report-manifest.json").read_bytes()):
        raise ValueError("Report metadata has a publication privacy risk")
    check_reader_contract(output, manifest, release=release)
    if manifest["renderer_sha256"] != digest(Path(__file__).read_bytes()):
        raise ValueError("Record renderer changed; rebuild required")
    expected_segments = BeautifulSoup(record_segment_configurations(
        read_json(output / "public-evidence/sources.json"), manifest["method_labels"], "zh"
    ), "html.parser").select_one("[data-segment-configurations]")
    protocol = BeautifulSoup(read_html(output / "zh/protocol.html"), "html.parser")
    if protocol.select_one("[data-segment-configurations]") != expected_segments:
        raise ValueError("Segment configuration disclosure differs from its frozen sources")
    home = BeautifulSoup(read_html(output / "zh/index.html"), "html.parser")
    if bool(home.select_one("[data-segment-note]")) != bool(expected_segments):
        raise ValueError("Segment configuration note is missing or spurious")
    actual = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    if actual != set(manifest["files"]) | {"report-manifest.json"}:
        raise ValueError("Record report contains missing or undeclared files")
    for name, expected in manifest["files"].items():
        path = output / name
        if path.is_symlink() or digest(path.read_bytes()) != expected:
            raise ValueError("Record report bytes changed: " + name)
        if public_risk(path.read_bytes()):
            raise ValueError("Report artifact has a publication privacy risk: " + name)
    expected_retries = BeautifulSoup(
        retry_summary(read_json(output / "data/metrics.json"), manifest["method_labels"]),
        "html.parser",
    ).select_one("[data-retry-summary]")
    actual_retries = BeautifulSoup(
        read_html(output / "zh/protocol.html"), "html.parser"
    ).select_one("[data-retry-summary]")
    if actual_retries != expected_retries:
        raise ValueError("Retry timing summary is missing or changed")
    preview = manifest.get("preview_snapshot", {})
    sources = read_json(output / "public-evidence/sources.json")
    modes = {
        part["contract"].get("input_mode", "snapshot")
        for source in sources.values()
        for part in source.get("segments", [source])
    }
    if modes != {manifest.get("dataset_input_mode")} or (
        (preview.get("mode") == "live") != (manifest.get("dataset_input_mode") == "live")
    ):
        raise ValueError("Preview input mode differs from the frozen collection contract")
    if preview.get("mode") == "live":
        if (
            preview.get("case_count") != manifest["case_count"]
            or len(preview.get("urls", {})) != manifest["case_count"]
        ):
            raise ValueError("Live preview population differs from the declared cohort")
    elif (
        not preview.get("fixture_base")
        or preview.get("mode")
        != ("synthetic" if manifest.get("local_example") else "dataset_manifest")
        or (
            not manifest.get("local_example")
            and preview.get("case_count") != manifest["case_count"]
        )
    ):
        raise ValueError("Record preview is not bound to its dataset snapshot")
    if manifest.get("navigation") is not None:
        navigation = manifest["navigation"]
        ordered = [e for e in navigation if e.get("primary", True)] + [
            e for e in navigation if not e.get("primary", True)
        ]
        expected_navigation = [
            ("zh/" + entry["route"] + ".html", entry["label"]) for entry in ordered
        ]
        for path in (output / "zh").glob("*.html"):
            soup = BeautifulSoup(read_html(path), "html.parser")
            actual_navigation = [(a.get("href"), a.get_text()) for a in soup.select("nav a")]
            if actual_navigation != expected_navigation:
                raise ValueError("Record navigation differs from its declared reader routes")
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
    if preview.get("mode") == "live" and preview.get("urls") != {
        cid: reference["url"] for cid, reference in truth.items()
    }:
        raise ValueError("Live preview URLs differ from the reference sources")
    profile_view = read_json(output / manifest["input_profile"]["path"])
    required_review = profile_view.get("independent_result_review_scope")
    if (
        required_review not in (None, "all")
        or manifest.get("required_review_scope") != required_review
    ):
        raise ValueError("Record review requirement differs from its bound instance profile")
    audit = None
    if required_review == "all" and not manifest.get("audit"):
        raise ValueError("Required complete record review is missing")
    if manifest.get("audit"):
        binding = manifest["audit"]
        if binding.get("path") != "public-evidence/audit.json":
            raise ValueError("Record audit evidence path changed")
        raw = (output / binding["path"]).read_bytes()
        receipt = next((v for v in views if v["path"] == binding["path"]), {})
        if digest(raw) != binding.get("sha256") or receipt.get("original_sha256") != binding.get(
            "source_sha256"
        ):
            raise ValueError("Record audit public view differs from its source authority")
        audit = json.loads(raw)
        expected_keys = {m + ":" + cid for m in manifest["planned_methods"] for cid in truth}
        if (
            audit.get("schema") != "record-complete-audit/1"
            or audit.get("review_scope") != "all"
            or audit.get("review_state") != "complete"
            or audit.get("unresolved") != 0
            or audit.get("unresolved_fields") != []
            or audit.get("accepted_unreviewed_passes") != 0
            or audit.get("result_count") != len(expected_keys)
            or audit.get("original_results_reviewed") != len(expected_keys)
            or set(audit.get("results", {})) != expected_keys
            or set(manifest["complete_methods"]) != set(manifest["planned_methods"])
            or audit.get("reference_policy") != manifest.get("reference_policy")
            or {cid: ref["values"] for cid, ref in audit.get("audited_reference", {}).items()}
            != {cid: ref["values"] for cid, ref in truth.items()}
        ):
            raise ValueError(
                "Record audit coverage, source reference or adjudication is incomplete"
            )
        original = read_json(output / "public-evidence/original-reference.json")
        original_metrics = read_json(output / "data/original-metrics.json")
        reviewed_page = BeautifulSoup(read_html(output / "zh/review.html"), "html.parser")
        expected_review = BeautifulSoup(
            record_review_section(
                audit,
                original,
                original_metrics,
                {m: manifest["method_labels"][m] for m in manifest["comparison_methods"]},
                "zh",
            ),
            "html.parser",
        )
        if reviewed_page.select_one("[data-record-review]") != expected_review.select_one(
            "[data-record-review]"
        ):
            raise ValueError("Record review summary differs from its source-bound evidence")
    if len(truth) != manifest["case_count"] or set(metrics) != set(manifest["planned_methods"]):
        raise ValueError("Record report matrix differs from its declared population")
    adapter = benchmark_config(name=manifest["dataset_benchmark"])["adapter"]
    for method in manifest["planned_methods"]:
        bucket = read_json(output / "public-evidence" / (method + ".json"))
        if not isinstance(bucket, dict) or any(
            not isinstance(row, dict) or cid != row.get("case_id") for cid, row in bucket.items()
        ):
            raise ValueError("Record evidence keys differ from embedded case identities")
        rows = list(bucket.values())
        for row in rows:
            verify_record_projection(truth[row["case_id"]], row)
        recomputed = record_statistics(
            truth,
            rows,
            certified=method in manifest["complete_methods"],
            scorer=adapter.score,
            reference_policy=manifest.get("reference_policy"),
        )
        if recomputed != metrics[method]:
            raise ValueError("Record report metrics do not reproduce: " + method)
        if audit is not None:
            if audit["audited_scores"].get(method) != recomputed["scores"] or record_statistics(
                original, rows, certified=True, scorer=adapter.score
            ) != original_metrics.get(method):
                raise ValueError("Original or reviewed record scores do not reproduce")
            for row in rows:
                key = method + ":" + row["case_id"]
                fields = record_result_fields(
                    truth[row["case_id"]], row, policy=manifest["reference_policy"]
                )["field_results"]
                if (
                    fields != audit["results"][key]["audited_fields"]
                    or row.get("review") != audit["results"][key]
                    or row.get("field_results") != fields
                ):
                    raise ValueError(
                        "Record audit field judgments differ from public native answers"
                    )
        for cid in truth:
            for route in (
                record_route("d", cid),
                record_route("c", cid),
            ):
                if not (output / "zh" / (route + ".html")).is_file():
                    raise ValueError(
                        "Record report is missing a source, comparison or result detail"
                    )
    check_worker_resources(
        output, manifest["planned_methods"],
        [row for method in manifest["planned_methods"]
         for row in read_json(output / "public-evidence" / (method + ".json")).values()],
        manifest["method_labels"], "zh",
    )
    check_completion_metrics(
        output,
        manifest,
        [
            r
            for m in manifest["planned_methods"]
            for r in read_json(output / "public-evidence" / (m + ".json")).values()
        ],
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
            execution=selected_execution_contracts(read_json(
                output / "public-evidence/sources.json").values()),
        ),
        "html.parser",
    )
    for anchor in expected_operations.select('a[href="accounts.html"]'):
        anchor["href"] = "zh/accounts.html"
    home_view = BeautifulSoup((output / "zh/index.html").read_text(encoding="utf-8"), "html.parser")
    check_price_coverage(
        output, manifest["planned_methods"], manifest["prices"], "zh", manifest["method_labels"]
    )
    if home_view.select_one("[data-operating-metrics]") != expected_operations.select_one(
        "[data-operating-metrics]"
    ):
        raise ValueError("Record timing or pricing differs from measured evidence")
    for route, selector in (
        ("index", "[data-core-metrics]"),
        ("interpretation", "[data-interpretation-comparison]"),
    ):
        soup = BeautifulSoup(read_html(output / "zh" / (route + ".html")), "html.parser")
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
                record_metric(scores["page_hit_macro"]["f1"]) if scores else "—",
                record_metric(scores["value_micro"]["f1"]) if scores else "—",
                record_metric(scores["exact_field_sets"]["rate"], percent=True) if scores else "—",
            ]
            label = cells[0].select_one("[data-method-label]")
            if (
                label is None
                or [label.get_text(strip=True), *[cell.get_text(strip=True) for cell in cells[1:6]]]
                != expected
            ):
                raise ValueError("Record core comparison values differ from measured evidence")
            notes = cells[0].select("[data-exclusion-reason]")
            reason = manifest["skipped_methods"].get(method)
            if reason is not None:
                if (
                    len(notes) != 1
                    or notes[0].get("data-excluded-method") != method
                    or notes[0].get_text(strip=True)
                    != message("excluded_comparison", "zh") + ": " + reason
                ):
                    raise ValueError("Record comparison exclusion reason is missing or changed")
            elif notes:
                raise ValueError("Record comparison scope has an unexpected exclusion reason")
    inventory = BeautifulSoup(read_html(output / "zh/results.html"), "html.parser")
    rows = inventory.select("tr[data-status]")
    if len(rows) != len(truth) * len(metrics) or len(
        inventory.select('tr[data-status="pending"]')
    ) != sum(x["unexecuted"] for x in metrics.values()):
        raise ValueError("Record inventory omits planned or pending results")
    indexed = {(row.get("data-method"), row.select("td")[1].get_text()): row for row in rows}
    if set(indexed) != {(m, cid) for m in metrics for cid in truth}:
        raise ValueError("Record inventory method/case population changed")
    for method in manifest["planned_methods"]:
        bucket = read_json(output / "public-evidence" / (method + ".json"))
        for cid, ref in truth.items():
            rendered = indexed[(method, cid)]
            row = bucket.get(cid)
            if row is None:
                attrs = {"status": "pending", "outcome": "pending"}
                field_outcomes = {field: "pending" for field in ref["values"]}
            else:
                derived = record_result_fields(ref, row, policy=manifest.get("reference_policy"))
                if any(
                    row.get(key) != derived[key]
                    for key in (
                        "field_results",
                        "status",
                        "exact_fields",
                        "scored_fields",
                        "failure_stage",
                    )
                ):
                    raise ValueError("Record field judgments differ from native evidence")
                attrs = {
                    "status": record_inventory_outcome(derived)[0],
                    "outcome": record_inventory_outcome(derived)[1],
                    "stage": derived["failure_stage"],
                    "scored-fields": str(derived["scored_fields"]),
                }
                field_outcomes = {f: v["status"] for f, v in derived["field_results"].items()}
            if json.loads(rendered.get("data-field-outcomes", "{}")) != field_outcomes:
                raise ValueError(
                    "Record inventory field filter verdict differs from native evidence"
                )
            attrs.update(domain=ref["domain"], site=ref["site"])
            if any(rendered.get("data-" + key) != val for key, val in attrs.items()):
                raise ValueError("Record inventory filter verdict differs from native evidence")
        for cid, row in bucket.items():
            detail = BeautifulSoup(
                read_html(output / "zh" / (record_route("c", cid) + ".html")),
                "html.parser",
            )
            detail = detail.find("details", id=method)
            if detail is None:
                raise ValueError("Record case page omits a participant detail")
            expected_cells = [
                [BeautifulSoup(cell, "html.parser").get_text(strip=True) for cell in cells]
                for cells in record_field_rows(row, "zh")
            ]
            displayed = [
                [cell.get_text(strip=True) for cell in tr.select("td")]
                for tr in detail.select("[data-record-fields] tbody tr")
            ]
            if displayed != expected_cells:
                raise ValueError("Record displayed fields differ from native evidence")
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
        soup = check_presentation(read_html(file))
        if preview.get("mode") == "live" and soup.select_one("[data-source-case]"):
            case = soup.select_one("[data-source-case]")["data-source-case"]
            links = soup.select("a[data-live-source]")
            if case not in truth or len(links) != 1 or links[0].get("href") != truth[case]["url"]:
                raise ValueError("Live dataset page requires its original source link")
        for link in soup.select("a[data-live-source]"):
            case = soup.select_one("[data-source-case]")
            if case is None or link.get("href") != preview.get("urls", {}).get(
                case["data-source-case"]
            ):
                raise ValueError("Live source link differs from the declared original URL")
        if preview.get("mode") == "live" and soup.select("iframe.frozen-preview"):
            raise ValueError("Live report must not substitute a frozen-page preview")
        for frame in soup.select("iframe.frozen-preview"):
            case = soup.select_one("[data-source-case]")
            if case is None:
                raise ValueError("Source page lacks its dataset case identity")
            cid = case["data-source-case"]
            expected = preview["fixture_base"] + "/" + cid
            if (
                frame.get("src") != expected
                or soup.select_one('a[href="' + expected + '"]') is None
            ):
                raise ValueError("Record preview URL differs from the verified dataset snapshot")
        for node in soup.select("a[href]"):
            parsed = urlsplit(node["href"])
            if parsed.scheme in {"http", "https"} or parsed.netloc:
                if node.get("target") != "_blank" or not {"noopener", "noreferrer"}.issubset(
                    node.get("rel", [])
                ):
                    raise ValueError("External documentation must open outside the report preview")
        for value in link_index.page(file).values:
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc:
                continue
            if parsed.path.startswith("/") or ".." in Path(unquote(parsed.path)).parts:
                raise ValueError("Reference must be relative to the hosted site root: " + value)
            if (
                site_prefix
                and parsed.path
                and not parsed.path.startswith(site_prefix + "/")
                and value != "index.html"
            ):
                raise ValueError("Nested report link escaped its dataset: " + value)
            target = site_root / unquote(parsed.path) if parsed.path else file
            if not target.resolve().is_relative_to(site_root.resolve()) or not target.is_file():
                raise ValueError("Broken record evidence/navigation target: " + value)
            if parsed.fragment and unquote(parsed.fragment) not in link_index.page(target).ids:
                raise ValueError("Broken evidence anchor: " + value)
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
