"""Compose independent record benchmarks through the maintained report owner."""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

from bs4 import BeautifulSoup

from harness.web_extract import report as owner
from harness.web_extract.pipeline import digest, read_json, require_external_output, save_json

MODE = "field-extraction-suite"


def section(output, entry, language="zh"):
    child = output / entry["id"]
    manifest = read_json(child / "report-manifest.json")
    metrics = read_json(child / "data/metrics.json")
    methods = manifest["planned_methods"]
    def t(key, **values):
        return owner.message(key, language, **values)

    def score(method, family, field):
        if method not in manifest["comparison_methods"]:
            return None
        scores = metrics[method]["scores"]
        return scores[family][field] if scores is not None else None

    quality, operation = [], []
    for method in methods:
        current = metrics[method]
        latency = current["latency"]
        quality.append(
            [
                escape(manifest["method_labels"][method])
                + (
                    "<br><small>" + escape(manifest["skipped_methods"][method]) + "</small>"
                    if method in manifest["skipped_methods"]
                    else ""
                ),
                f"{current['executed']} / {current['planned']}",
                owner.record_metric(score(method, "value_micro", "f1")),
                owner.record_metric(score(method, "page_hit_macro", "f1")),
                owner.record_metric(score(method, "exact_field_sets", "rate"), True),
                f'<a href="{entry["id"]}/{language}/interpretation.html#{method}">'
                + t("suite_results") + "</a>",
            ]
        )
        operation.append(
            [
                escape(manifest["method_labels"][method]),
                f"{latency['timed']} / {current['planned']}",
                *[owner.record_metric(latency[key]) for key in ("mean", "p50", "p95")],
                escape(owner.price_quote(manifest["prices"][method], language)),
                f'<a href="{entry["id"]}/{language}/accounts.html">{t("suite_pricing")}</a>',
            ]
        )
    quality_table = owner.record_comparison(
        t("suite_quality_headers"),
        quality,
        {
            column: (key, "max", [score(m, family, field) for m in methods])
            for column, key, family, field in [
                (2, "micro-f1", "value_micro", "f1"),
                (3, "macro-f1", "page_hit_macro", "f1"),
                (4, "exact-rate", "exact_field_sets", "rate"),
            ]
        },
    )
    time_table = owner.record_comparison(
        t("suite_operation_headers"),
        operation,
        {
            column: (key, "min", [metrics[m]["latency"][key] for m in methods])
            for column, key in [(2, "mean"), (3, "p50"), (4, "p95")]
        },
    )
    return (
        f'<section data-cohort="{entry["id"]}" id="{entry["id"]}"><h2>{escape(entry["label"])} · '
        + t("suite_cases", count=manifest["case_count"]) + "</h2>"
        + entry.get("summary_html", "")
        + quality_table
        + "<h3>" + t("latency_and_pricing") + "</h3>"
        + time_table
        + f'<p><a href="{entry["id"]}/{language}/dataset.html">{t("suite_dataset")}</a> · '
        f'<a href="{entry["id"]}/{language}/protocol.html">{t("suite_examples")}</a> · '
        f'<a href="{entry["id"]}/{language}/results.html">{t("suite_case_results")}</a>'
        "</p></section>"
    )


def build(output, content, *, profile, language=None):
    require_external_output(output)
    language = owner.resolve_report_language(output, language, supported=("zh",))
    model, authority = read_json(content), read_json(profile)
    if authority.get("research_report", {}).get("content", {}).get("sha256") != digest(
        content.read_bytes()
    ):
        raise ValueError("Suite content differs from its instance profile")
    entries = model["cohorts"]
    names = [entry["id"] for entry in entries]
    if (
        not names
        or len(set(names)) != len(names)
        or any(
            name in {"zh", "en", "public-evidence", "assets"}
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
            for name in names
        )
    ):
        raise ValueError("Suite cohorts need distinct safe directory names")
    for entry in entries:
        child_content = read_json(Path(entry["content"]))
        if child_content.get("site_prefix") != entry["id"]:
            raise ValueError("Each cohort must declare its suite directory")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty suite report directory")
    output.mkdir(parents=True, exist_ok=True)
    (output / "index.html").write_text(
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta http-equiv="refresh" content="0;url=zh/index.html"></head>'
        '<body><a href="zh/index.html">Web Extract</a></body></html>',
        encoding="utf-8",
    )
    bindings = []
    for entry in entries:
        owner.build(
            output / entry["id"],
            Path(entry["content"]),
            home=Path(entry["home"]),
            profile=Path(entry["profile"]),
            language=language,
        )
        bindings.append(
            {key: entry[key] for key in ("id", "label")}
            | {
                "summary_html": entry.get("summary_html", ""),
                "manifest_sha256": digest(
                    (output / entry["id"] / "report-manifest.json").read_bytes()
                ),
            }
        )
    for directory in ("zh", "en"):
        (output / directory).mkdir()
    (output / "style.css").write_text(owner.CSS, encoding="utf-8")
    (output / "app.js").write_text(owner.JS, encoding="utf-8")
    owner.copy_report_assets(output, content, model)
    body = model.get("intro_html", "") + "".join(
        section(output, entry, language) for entry in bindings
    )
    body += owner.report_metric_guide(language, records=True) + model.get("interpretation_html", "")
    (output / "zh/index.html").write_text(
        owner.page(
            language, "index", model["title"], body, languages=(language,), state=MODE,
            notice=owner.message("suite_notice", language),
            navigation=[{"route": f'{e["id"]}/{language}/index', "label": e["label"]}
                        for e in bindings],
        ),
        encoding="utf-8",
    )
    (output / "en/index.html").write_text(
        owner.message("english_not_generated_notice", "en"), encoding="utf-8"
    )
    input_profile = owner.copy_input_profile(output, profile)
    save_json(output / "suite.json", {"cohorts": bindings})
    save_json(
        output / "report-manifest.json",
        {
            "mode": MODE,
            "template": owner.template_identity("web-extract"),
            "template_version": owner.VERSION,
            "browser_eval": owner.bind_build_version(output),
            "renderer_sha256": digest(Path(owner.__file__).read_bytes()),
            "composition_sha256": digest(Path(__file__).read_bytes()),
            **owner.report_dependencies(),
            "input_profile": input_profile,
            "profile_sha256": digest(profile.read_bytes()),
            "content_sha256": digest(content.read_bytes()),
            "local_example": authority.get("local_example", False),
            "dataset_kind": "local-example"
            if authority.get("local_example", False)
            else "published-upstream",
            "languages": ["zh"],
            "primary_language": "zh",
            "cohorts": bindings,
            "files": {
                p.relative_to(output).as_posix(): digest(p.read_bytes())
                for p in sorted(output.rglob("*"))
                if p.is_file()
            },
        },
        exclusive=True,
    )
    return check(output)


def check(output, *, release=False, visual_receipt=None):
    output = owner.open_report(output)
    manifest = read_json(output / "report-manifest.json")
    owner.check_report_identity(output, manifest, "web-extract")
    owner.check_input_profile(output, manifest)
    if (
        manifest.get("mode") != MODE
        or manifest.get("renderer_sha256") != digest(Path(owner.__file__).read_bytes())
        or manifest.get("composition_sha256") != digest(Path(__file__).read_bytes())
    ):
        raise ValueError("Suite renderer changed; rebuild required")
    actual = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    if actual != set(manifest["files"]) | {"report-manifest.json"}:
        raise ValueError("Suite inventory changed")
    for name, sha in manifest["files"].items():
        path = output / name
        if (
            path.is_symlink()
            or digest(path.read_bytes()) != sha
            or owner.public_risk(path.read_bytes())
        ):
            raise ValueError("Suite file changed or has a privacy risk: " + name)
    entries = read_json(output / "suite.json")["cohorts"]
    if entries != manifest["cohorts"]:
        raise ValueError("Suite cohort bindings changed")
    names = [entry["id"] for entry in entries]
    if (
        not names
        or len(names) != len(set(names))
        or any(
            name in {"zh", "en", "public-evidence", "assets"}
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
            for name in names
        )
    ):
        raise ValueError("Suite cohort directories changed")
    visual = read_json(visual_receipt) if release and visual_receipt else {}
    for entry in entries:
        child = output / entry["id"]
        if digest((child / "report-manifest.json").read_bytes()) != entry["manifest_sha256"]:
            raise ValueError("Suite cohort manifest changed")
        receipt = visual.get("cohorts", {}).get(entry["id"], {})
        if release and (
            not receipt.get("path")
            or digest(Path(receipt["path"]).read_bytes()) != receipt.get("sha256")
        ):
            raise ValueError("Suite requires each cohort's bound visual receipt")
        owner.check_record_cohort(
            child, release=release, visual_receipt=Path(receipt["path"]) if release else None
        )
    from harness.reporting.presentation import check_metric_guide, check_presentation

    soup = check_presentation((output / "zh/index.html").read_text(encoding="utf-8"))
    expected = BeautifulSoup(owner.page(
        "zh", "index", "", "".join(section(output, entry) for entry in entries),
        languages=("zh",), state=MODE,
        navigation=[{"route": f'{e["id"]}/zh/index', "label": e["label"]} for e in entries],
    ), "html.parser")
    if [str(node) for node in soup.select("[data-cohort]")] != [
        str(node) for node in expected.select("[data-cohort]")
    ]:
        raise ValueError("Suite comparison differs from its independent cohort results")
    check_metric_guide(
        str(soup),
        owner.metric_definitions("zh", records=True),
        title=owner.message("guide_title", "zh"),
        headings=owner.message("guide_headings", "zh"),
    )
    if [(a.get("href"), a.get_text()) for a in soup.select("nav a")] != [
        (entry["id"] + "/zh/index.html", entry["label"]) for entry in entries
    ]:
        raise ValueError("Suite navigation differs from its benchmark registry")
    notice = BeautifulSoup((output / "en/index.html").read_text(encoding="utf-8"), "html.parser")
    if {p.name for p in (output / "en").glob("*.html")} != {"index.html"} or not notice.select_one(
        '[data-language-notice] a[href="zh/index.html"]'
    ):
        raise ValueError("Suite English notice is missing or stale")
    links = owner.LinkIndex(errors="strict")
    for path in [output / "zh/index.html", output / "en/index.html"]:
        for value in links.page(path).values:
            parsed = owner.urlsplit(value)
            if parsed.scheme or parsed.netloc:
                continue
            target = output / owner.unquote(parsed.path) if parsed.path else path
            if not target.resolve().is_relative_to(output) or not target.is_file():
                raise ValueError("Broken suite navigation: " + value)
            if parsed.fragment and owner.unquote(parsed.fragment) not in links.page(target).ids:
                raise ValueError("Broken suite anchor: " + value)
    if release:
        if manifest.get("local_example"):
            raise ValueError("Synthetic suites cannot be published as measured results")
        owner.check_visual_receipt(output, manifest, visual_receipt, routes=("index",))
    return manifest
