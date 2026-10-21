from __future__ import annotations

import json
import importlib.metadata
import re
from collections import Counter
from functools import lru_cache, wraps
from contextvars import ContextVar
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _raw_report_method_catalog():
    path = Path(__file__).resolve().parent / "data/report-methods.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported report method registry schema")
    return data


def _catalog_value(value, path=()):
    if isinstance(value, dict):
        if set(value) == {"zh", "en"}:
            if len(path) == 3 and path[0] == "methods" and path[-1] == "label":
                return value
            return value["zh"]
        return {key: _catalog_value(item, (*path,key)) for key,item in value.items()}
    if isinstance(value, list):
        return [_catalog_value(item, (*path,str(index))) for index,item in enumerate(value)]
    return value


@lru_cache(maxsize=1)
def report_method_catalog():
    """One source for reusable method presentation, including installed wheels."""
    return _catalog_value(_raw_report_method_catalog())


@lru_cache(maxsize=1)
def report_method_translations():
    """Read colocated author translations; never infer them from report output."""
    translations = {}
    def visit(value):
        if isinstance(value, dict):
            if set(value) == {"zh", "en"}:
                translations[value["zh"]] = value["en"]
            else:
                for item in value.values():
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(_raw_report_method_catalog())
    return translations


_REPORT_RECORDS = ContextVar("webfetch_report_records", default={})


def report_method(name, record=None):
    """Resolve run IDs through recorded identity and the execution registry."""
    methods = report_method_catalog()["methods"]
    record = record or _REPORT_RECORDS.get().get(name, {})
    metadata = record.get("provider_metadata") or {}
    execution = browser_method_registry().get(name, {})
    for key in (metadata.get("engine"), execution.get("engine"), name):
        if key in methods:
            return methods[key]
    provider_type = record.get("provider_type", execution.get("type"))
    for item in methods.values():
        if provider_type in item.get("adapter_types", []):
            return item
    return {}


@lru_cache(maxsize=1)
def browser_method_registry():
    """Use the execution registry for release and source-build participants."""
    path = Path(__file__).resolve().parents[2] / "configs/providers.headless.json"
    if not path.is_file():
        from harness.distribution import installed_data_path
        try:
            path = installed_data_path("share/browser-harness-eval/providers.headless.json")
        except importlib.metadata.PackageNotFoundError:
            return {}
        except ValueError as error:
            if str(error).startswith('Distribution must declare one data file:'):
                return {}
            raise
    return json.loads(path.read_text())["providers"]


def native_browser_engine(name):
    return browser_method_registry().get(name, {}).get("engine")


def browser_method_label(name):
    return _MEASURED_LABELS.get().get(
        name, browser_method_registry().get(name, {}).get("label") or report_method(name).get("label", {}).get("zh", name)
    )


_MEASURED_LABELS = ContextVar("webfetch_measured_labels", default={})


def measured_method_label(name, fallback):
    return _MEASURED_LABELS.get().get(name, fallback)


def method_version(record, provenance=None):
    """Prefer the recorded executable version; CDP compatibility is not an identity."""
    metadata = record.get("provider_metadata") or {}
    registered = report_method(record.get("provider", ""), record)
    policy = registered.get("version_policy")
    if metadata.get("reported_version"):
        return str(metadata["reported_version"])
    bound = (provenance or {}).get(metadata.get("binary_sha256"), {})
    if bound.get("version"):
        return str(bound["version"])
    for key in ("binary_version", "library_version", "version"):
        if metadata.get(key):
            return str(metadata[key])
    if policy == "hosted":
        return registered.get("version_unavailable")
    browser_version = metadata.get("browser_version")
    if policy == "native" and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", str(browser_version)):
        return None
    return str(browser_version) if browser_version else None


def with_measured_method_labels(render):
    """Scope all participant labels to recorded versions and binary identities."""
    @wraps(render)
    def wrapped(out, dataset, records, *args, **kwargs):
        path = Path(out) / "implementation-provenance.json"
        provenance = json.loads(path.read_text()) if path.is_file() else {}
        identities = {item["binary_sha256"]: item for item in provenance.get("implementations", [])
                      if item.get("binary_sha256")}
        records_token = _REPORT_RECORDS.set({row["provider"]: row for row in records})
        labels = {}
        try:
            for name in {row["provider"] for row in records}:
                registered = report_method(name)
                subset = [row for row in records if row["provider"] == name]
                versions = [method_version(row, identities) for row in subset]
                if not versions or not all(versions):
                    continue
                base = registered.get("label", {}).get("zh", name)
                # An adapter type describes transport, not a participant identity.
                # Preserve caller names unless a product engine is explicitly bound.
                metadata = subset[0].get("provider_metadata") or {}
                execution = browser_method_registry().get(name, {})
                if (name not in report_method_catalog()["methods"]
                        and not metadata.get("engine") and not execution.get("engine")):
                    base = name
                # Product-prefixed executable output is already a complete label.
                counts = Counter(versions)
                def label(version):
                    if version.casefold().startswith(base.casefold() + " "):
                        return base + version[len(base):]
                    return registered.get("label_template", "{label} {version}").format(
                        label=base, version=version)
                if len(counts) == 1:
                    labels[name] = label(next(iter(counts)))
                else:
                    releases = [
                        match.group(1) if (match := re.match(r"^(\d+\.\d+\.\d+)", version)) else None
                        for version in counts
                    ]
                    if None not in releases and len(set(releases)) == 1:
                        configured = browser_method_registry().get(name, {}).get("label", "")
                        qualifier = "（候选版）" if "candidate" in configured.lower() else ""
                        labels[name] = label(releases[0]) + qualifier
                    else:
                        labels[name] = base + " 多构建结果（" + "；".join(
                            f"{count} 例 {version}" for version, count in sorted(counts.items())
                        ) + "）"
            token = _MEASURED_LABELS.set(labels)
            try:
                return render(out, dataset, records, *args, **kwargs)
            finally:
                _MEASURED_LABELS.reset(token)
        finally:
            _REPORT_RECORDS.reset(records_token)
    return wrapped


METHOD_REGISTRY = report_method_catalog()["adapters"]


def is_browser_comparison(records: list[dict[str, Any]]) -> bool:
    """Recognize browser cohorts independently of their task-specific adapters."""
    roles = [
        "primary" if report_method(row.get("provider", ""), row).get("kind") == "browser"
        else method_info(row.get("provider_type", "")).get("browser_cohort_role")
        for row in records
    ]
    return "primary" in roles and all(role in {"primary", "companion"} for role in roles)


def method_info(provider_type: str) -> dict[str, Any]:
    return METHOD_REGISTRY.get(provider_type, {"endpoint":"未记录","contract":"未知","output":"未知","freshness":"未知","leaderboard":"未分类"})


ROUTE_LABELS = report_method_catalog()["route_labels"]


def _metadata_matches(metadata: dict[str, Any], expected: dict[str, Any]) -> bool:
    for path, value in expected.items():
        current: Any = metadata
        for key in path.split("."):
            if not isinstance(current, dict) or key not in current:
                return False
            current = current[key]
        if current != value:
            return False
    return True


def method_page_model(name: str, label: str, records: list[dict[str, Any]]) -> dict[str, str]:
    """Resolve method-specific evidence into provider-neutral page fields."""
    if not records:
        raise ValueError("method page requires at least one record")
    info = method_info(records[0].get("provider_type", ""))
    report = info.get("report", {})
    versions = sorted({
        str(record.get("provider_metadata", {}).get("reported_version", "")).strip()
        for record in records
        if str(record.get("provider_metadata", {}).get("reported_version", "")).strip()
    })
    title = label
    version_rule = report.get("reported_version")
    if version_rule and len(versions) == 1:
        version = re.sub(
            version_rule["pattern"], version_rule.get("replacement", ""), versions[0],
            flags=re.I,
        )
        if version not in title:
            title += f" · {version}"
    model = {
        "title": title,
        "contract": info["contract"],
        "endpoint": info["endpoint"],
        "freshness": info["freshness"],
        "configuration": report.get(
            "configuration",
            "调用参数与交付路线以本次配置和逐例记录为准。",
        ),
        "pricing_anchor": report.get("pricing_anchor", "method-guide-pricing"),
    }
    metadata = [record.get("provider_metadata", {}) for record in records]
    if report.get("observed_routes"):
        extractors = sorted({
            ROUTE_LABELS.get(item.get("extractor"), item.get("extractor"))
            for item in metadata if item.get("extractor")
        })
        contracts = sorted({str(item.get("output_contract")) for item in metadata
                            if item.get("output_contract")})
        route_count = sum(bool(item.get("extractor")) for item in metadata)
        output_count = sum(bool(item.get("output_contract")) for item in metadata)
        route = "；".join(extractors) or "未记录"
        output = "、".join(contracts) or "未记录"
        model["configuration"] = (
            f"交付路线（{route_count}/{len(records)} 条记录）：{route}；"
            f"输出格式（{output_count}/{len(records)} 条记录）：{output}。"
        )
    for profile in report.get("profiles", []):
        if metadata and all(_metadata_matches(item, profile["metadata_equals"])
                            for item in metadata):
            for field in ("endpoint", "freshness", "configuration"):
                if field in profile:
                    model[field] = profile[field].format(record_count=len(records))
            break
    return model
