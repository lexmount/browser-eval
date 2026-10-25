from __future__ import annotations

import json
import hashlib
import inspect
from copy import deepcopy
import importlib.metadata
import re
from collections import Counter
from functools import lru_cache, wraps
from contextvars import ContextVar
from pathlib import Path
from typing import Any


def _resolve_catalog_templates(data):
    """Expand named defaults once; catalog entries override dictionaries and replace lists."""
    def merge(base, override):
        result = deepcopy(base)
        for key, value in override.items():
            if key == 'extends':
                continue
            result[key] = (merge(result[key], value) if isinstance(result.get(key), dict)
                           and isinstance(value, dict) else deepcopy(value))
        return result

    templates = data.get('templates', {})
    def resolve(row, chain=()):
        parent = row.get('extends')
        if parent is None:
            return deepcopy(row)
        if parent not in templates:
            raise ValueError('Unknown method template: ' + str(parent))
        if parent in chain:
            raise ValueError('Cyclic method template: ' + parent)
        return merge(resolve(templates[parent], (*chain, parent)), row)

    return {**data, **{section: {name: resolve(row) for name, row in data[section].items()}
                      for section in ('methods', 'adapters') if section in data}}


@lru_cache(maxsize=1)
def _raw_report_method_catalog():
    path = Path(__file__).resolve().parent / "data/report-methods.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported report method registry schema")
    return _resolve_catalog_templates(data)


def _catalog_value(value, path=(), *, language="zh"):
    if isinstance(value, dict):
        if set(value) == {"zh", "en"}:
            if len(path) == 3 and path[0] == "methods" and path[-1] == "label":
                return value
            return value[language]
        return {key: _catalog_value(item, (*path,key), language=language) for key,item in value.items()}
    if isinstance(value, list):
        return [_catalog_value(item, (*path,str(index)), language=language) for index,item in enumerate(value)]
    return value


@lru_cache(maxsize=2)
def report_method_catalog(language="zh"):
    """Select colocated language fields by identity, never by matching prose."""
    if language not in {"zh", "en"}:
        raise ValueError("Unsupported method catalog language: " + language)
    return _catalog_value(_raw_report_method_catalog(), language=language)


_REPORT_RECORDS = ContextVar("webfetch_report_records", default={})
_REPORT_EXECUTION_CONFIGS = ContextVar("webfetch_report_execution_configs", default={})


def frozen_execution_configs(manifests):
    """Read installation bindings only from bytes named by execution manifests.

    Missing or conflicting configurations remain unavailable. Never publish raw
    configuration values, which may include credentials and machine paths.
    """
    bindings = {}
    for manifest in manifests:
        config = {}
        binary_key = ("resource_binary" if manifest.get("parameters", {}).get("resource_sample_interval_ms") is not None else "binary")
        source = manifest.get("provider_config_source")
        digest = manifest.get("provider_config_sha256")
        if source and digest:
            path = Path(source)
            if path.is_file() and not path.is_symlink():
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() == digest:
                    payload = json.loads(raw)
                    config = payload.get("providers", {})
                    # A manifest does not identify which cohort override was
                    # selected. Do not infer an executable when overrides differ.
                    config = deepcopy(config)
                    for cohort in payload.get("cohorts", {}).values():
                        for name, override in cohort.get("provider_overrides", {}).items():
                            if any(key in override and override[key] != config.get(name, {}).get(key)
                                   for key in (binary_key, "engine")):
                                config.pop(name, None)
                    if binary_key != "binary":
                        config = {name: {**value, "binary": value.get(binary_key)}
                                  for name, value in config.items()}
        for provider in manifest.get("providers", []):
            name = provider["name"]
            value = config.get(name)
            if name in bindings and bindings[name] != value:
                bindings[name] = None
            else:
                bindings[name] = value
    return bindings


def _browser_installation_access(name, selected, registered, access, language):
    if registered.get("kind") != "browser" or access.get("type") != "local":
        return access
    measured = selected or _REPORT_RECORDS.get().get(name)
    config = ((_REPORT_EXECUTION_CONFIGS.get().get(name) or _REPORT_METHODS.get().get(name)) if measured
              else browser_method_registry().get(name))
    engine = (measured or {}).get("provider_metadata", {}).get("engine")
    if config and engine and config.get("engine") != engine:
        config = None
    binary = config.get("binary") if config else None
    variables = []
    messages = report_method_catalog(language)["messages"]
    if binary is None:
        explanation = messages["installation_unrecorded"]
    else:
        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?}", str(binary))
        if match:
            variables = [match.group(1)]
            explanation = messages["installation_variable"].format(variable=match.group(1))
        else:
            explanation = messages["installation_direct"]
    return {**access, "env_vars": variables, "configuration": explanation}


def report_method(name, record=None, *, language="zh"):
    """Resolve run IDs through recorded identity and the execution registry."""
    methods = (report_method_catalog() if language == "zh" else report_method_catalog(language))["methods"]
    record = record or _REPORT_RECORDS.get().get(name, {})
    metadata = record.get("provider_metadata") or {}
    if metadata.get("engine"):
        return methods.get(metadata["engine"], {})
    execution = browser_method_registry().get(name, {})
    for key in (execution.get("engine"), name):
        if key in methods:
            return methods[key]
    provider_type = record.get("provider_type", execution.get("type"))
    matches = [item for item in methods.values()
               if provider_type in item.get("adapter_types", [])]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous report method for adapter {provider_type!r}; record an engine")
    return matches[0] if matches else {}


def normalized_method_version(registered, version):
    """Normalize presentation only; preserve the full recorded build suffix."""
    value = str(version).strip()
    labels = registered.get("label", {})
    prefixes = [registered.get("version_prefix"), *(labels.values() if isinstance(labels, dict) else [labels])]
    for prefix in sorted({str(item) for item in prefixes if item}, key=len, reverse=True):
        if value.casefold().startswith(prefix.casefold() + " "):
            value = value[len(prefix):].strip()
            break
    return value


def versioned_method_label(registered, base, version):
    value = normalized_method_version(registered, version)
    return registered.get("label_template", "{label} {version}").format(
        label=base, version=value)


def method_access(name, records, *, language="zh"):
    bound = _REPORT_METHODS.get().get(name, {}).get("report_access")
    if bound is not None:
        return {**bound,
                "account": bound.get("account_en", bound["account"]) if language == "en" else bound["account"],
                "configuration": bound.get("configuration_en", bound["configuration"]) if language == "en" else bound["configuration"]}
    selected = next((row for row in records if row.get("provider") == name), None)
    registered = report_method(name, selected, language=language)
    access = registered.get("access")
    if access:
        access = _browser_installation_access(name, selected, registered, access, language)
        template = registered.get("release_url_template")
        versions = {normalized_method_version(registered, value) if value else None
                    for row in records if row.get("provider") == name
                    for value in [method_version(row)]}
        if template and len(versions) == 1 and None not in versions:
            version = next(iter(versions))
            # A build suffix is not proof that a corresponding release exists.
            if re.fullmatch(r"\d+\.\d+\.\d+", version):
                return {**access, "links": [(versioned_method_label(
                    registered, registered["label"][language], version),
                    template.format(version=version))]}
        return access
    return report_method_catalog(language)["unregistered_access"]


@lru_cache(maxsize=1)
def _execution_browser_method_registry():
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


_REPORT_METHODS = ContextVar("webfetch_report_methods", default={})


def browser_method_registry():
    """Combine reusable adapters with the selected instance's frozen registry."""
    return {**_execution_browser_method_registry(), **_REPORT_METHODS.get()}


def native_browser_engine(name, record=None):
    record = record or _REPORT_RECORDS.get().get(name, {})
    recorded = (record.get("provider_metadata") or {}).get("engine")
    if recorded:
        return recorded
    configured = browser_method_registry().get(name, {}).get("engine")
    if configured:
        return configured
    if report_method_catalog()["methods"].get(name, {}).get("kind") == "browser":
        return name
    return None


def browser_method_label(name, *, language="zh"):
    measured = _MEASURED_LABELS.get().get(name)
    if measured:
        return measured[language]
    record = _REPORT_RECORDS.get().get(name, {})
    engine = (record.get("provider_metadata") or {}).get("engine")
    if engine:
        return report_method(name, record).get("label", {}).get(language, engine)
    return (browser_method_registry().get(name, {}).get("label")
            or report_method(name).get("label", {}).get(language, name))


_MEASURED_LABELS = ContextVar("webfetch_measured_labels", default={})


def measured_method_label(name, fallback):
    return _MEASURED_LABELS.get().get(name, {}).get("zh", fallback)


def method_version(record, provenance=None, *, language="zh"):
    """Prefer the recorded executable version; CDP compatibility is not an identity."""
    metadata = record.get("provider_metadata") or {}
    registered = report_method(record.get("provider", ""), record, language=language)
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
    if browser_method_registry().get(record.get("provider"), {}).get("version_source") == "reported":
        return None
    browser_version = metadata.get("browser_version")
    if policy == "native" and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", str(browser_version)):
        return None
    return str(browser_version) if browser_version else None



def method_versions(records, provenance=None):
 """Keep distinct measured binaries visible when their reported versions coincide."""
 versions={}
 for record in records:
  version=method_version(record,provenance)
  if version:
   versions.setdefault(str(version),set())
   digest=(record.get('provider_metadata') or {}).get('binary_sha256')
   if digest:versions[str(version)].add(str(digest))
 multiple=len({digest for hashes in versions.values() for digest in hashes})>1
 return [version+(' ['+', '.join(sorted(digest[:8] for digest in hashes))+']'
                 if multiple and hashes else '') for version,hashes in sorted(versions.items())]



def with_measured_method_labels(render):
    """Scope all participant labels to recorded versions and binary identities."""
    signature = inspect.signature(render)
    def render_with_labels(out, dataset, records, *args, **kwargs):
        path = Path(out) / "implementation-provenance.json"
        provenance = json.loads(path.read_text()) if path.is_file() else {}
        identities = {item["binary_sha256"]: item for item in provenance.get("implementations", [])
                      if item.get("binary_sha256")}
        records_token = _REPORT_RECORDS.set({row["provider"]: row for row in records})
        arguments = signature.bind(out, dataset, records, *args, **kwargs).arguments
        configs_token = _REPORT_EXECUTION_CONFIGS.set(
            frozen_execution_configs(arguments["manifests"]) if "manifests" in arguments
            else _REPORT_EXECUTION_CONFIGS.get())
        labels = {}
        try:
            for name in {row["provider"] for row in records}:
                registered = report_method(name)
                subset = [row for row in records if row["provider"] == name]
                versions = [normalized_method_version(registered, value) if value else None
                            for row in subset for value in [method_version(row, identities)]]
                if not versions or not all(versions):
                    continue
                metadata = subset[0].get("provider_metadata") or {}
                execution = browser_method_registry().get(name, {})
                preserve_name = (name not in report_method_catalog()["methods"]
                                 and not metadata.get("engine") and not execution.get("engine"))
                channels = {row.get("provider_metadata", {}).get(
                    "release_channel", execution.get("release_channel")) for row in subset}
                labels[name] = {}
                for language in ("zh", "en"):
                    counts = Counter(normalized_method_version(registered, method_version(
                        row, identities, language=language)) for row in subset)
                    releases = [match.group(1) if (match := re.match(
                        r"^(\d+\.\d+\.\d+)", version)) else None for version in counts]
                    base = name if preserve_name else registered.get("label", {}).get(
                        language, metadata.get("engine") or name)
                    def label(version):
                        return versioned_method_label(registered, base, version)
                    messages = report_method_catalog(language)["messages"]
                    messages = report_method_catalog(language)["messages"]
                    messages = report_method_catalog(language)["messages"]
                    if len(counts) == 1:
                        value = label(next(iter(counts)))
                    elif None not in releases and len(set(releases)) == 1:
                        qualifier = messages["candidate_suffix"] if channels == {"candidate"} else ""
                        value = label(releases[0]) + qualifier
                    else:
                        parts = [messages["mixed_count_one" if count == 1 else "mixed_count_many"].format(
                            count=count, version=version) for version, count in sorted(counts.items())]
                        value = messages["mixed_builds"].format(
                            label=base, counts=messages["separator"].join(parts))
                    labels[name][language] = value
            token = _MEASURED_LABELS.set(labels)
            try:
                return render(out, dataset, records, *args, **kwargs)
            finally:
                _MEASURED_LABELS.reset(token)
        finally:
            _REPORT_EXECUTION_CONFIGS.reset(configs_token)
            _REPORT_RECORDS.reset(records_token)
    @wraps(render)
    def wrapped(out, dataset, records, *args, **kwargs):
        comparison = dataset.get("comparison")
        saved = Path(out) / "dataset.json"
        if comparison is None and saved.is_file():
            comparison = json.loads(saved.read_text()).get("comparison", {})
        token = _REPORT_METHODS.set((comparison or {}).get("method_registry", _REPORT_METHODS.get()))
        try:
            return render_with_labels(out, dataset, records, *args, **kwargs)
        finally:
            _REPORT_METHODS.reset(token)
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


def method_info(provider_type: str, *, language="zh") -> dict[str, Any]:
    registry = METHOD_REGISTRY if language == "zh" else report_method_catalog(language)["adapters"]
    return registry.get(provider_type, report_method_catalog(language)["unregistered_adapter"])



ROUTE_LABELS = report_method_catalog()["route_labels"]


def metadata_matches(metadata: dict[str, Any], expected: dict[str, Any]) -> bool:
    for path, value in expected.items():
        current: Any = metadata
        for key in path.split("."):
            if not isinstance(current, dict) or key not in current:
                return False
            current = current[key]
        if current != value:
            return False
    return True


def method_page_model(name: str, label: str, records: list[dict[str, Any]], *, language="zh") -> dict[str, str]:
    """Resolve method-specific evidence into provider-neutral page fields."""
    if not records:
        raise ValueError("method page requires at least one record")
    info = method_info(records[0].get("provider_type", ""), language=language)
    report = info.get("report", {})
    registered = report_method(name, records[0], language=language)
    versions = {method_version(row, language=language) for row in records}
    title = label
    if len(versions) == 1 and None not in versions:
        version = normalized_method_version(registered, next(iter(versions)))
        if version not in title:
            title = versioned_method_label(registered, label, version)
    model = {
        "title": title,
        "contract": info["contract"],
        "endpoint": info["endpoint"],
        "freshness": info["freshness"],
        "configuration": report.get(
            "configuration",
            report_method_catalog(language)["messages"]["configuration_default"],
        ),
        "pricing_anchor": report.get("pricing_anchor", "method-guide-pricing"),
    }
    metadata = [record.get("provider_metadata", {}) for record in records]
    if report.get("observed_routes"):
        route_labels = ROUTE_LABELS if language == "zh" else report_method_catalog(language)["route_labels"]
        extractors = sorted({
            route_labels.get(item.get("extractor"), item.get("extractor"))
            for item in metadata if item.get("extractor")
        })
        contracts = sorted({str(item.get("output_contract")) for item in metadata
                            if item.get("output_contract")})
        route_count = sum(bool(item.get("extractor")) for item in metadata)
        output_count = sum(bool(item.get("output_contract")) for item in metadata)
        messages = report_method_catalog(language)["messages"]
        route = messages["separator"].join(extractors) or messages["not_recorded"]
        output = messages["list_separator"].join(contracts) or messages["not_recorded"]
        model["configuration"] = messages["configuration_observed"].format(
            route_count=route_count, record_count=len(records), route=route,
            output_count=output_count, output=output)
    for profile in report.get("profiles", []):
        if metadata and all(metadata_matches(item, profile["metadata_equals"])
                            for item in metadata):
            for field in ("endpoint", "freshness", "configuration"):
                if field in profile:
                    model[field] = profile[field].format(record_count=len(records))
            break
    return model
