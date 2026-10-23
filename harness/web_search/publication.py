"""Freeze and verify the next release before updating its existing Artifact Site."""
import argparse
from collections import Counter
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

from lexbench_webfetch.paths import contained_path
from harness.reporting.publication import atomic_write
from harness.web_fetch.report_publish import PublishConfig, publish as publish_package
from .core import digest
from harness.web_fetch.config import ROOT
from .profiles import current_selection, authority_root


def data_home():
    return Path(os.environ.get("BROWSER_EVAL_DATA_HOME", Path.home() / ".browser-eval")).expanduser().resolve()


def save(path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def prepare(report, visual, content, sources, work, *, profile):
    from .report import check
    from .delivery import export, verify as verify_delivery
    from .visual import verify as verify_visual
    check(report)
    verify_visual(report, visual)
    manifest = json.loads((report / "manifest.json").read_bytes())
    if not manifest["receipt"].get("certified") or manifest["receipt"].get("synthetic"):
        raise ValueError("Publication requires certified measured evidence")
    if json.loads(content.read_bytes()) != manifest["authored"]:
        raise ValueError("Publication content differs from the checked report")
    instance_path = Path(profile).resolve()
    root = authority_root(instance_path)
    selection = current_selection(instance_path)
    target = json.loads((root / selection["publication_target"]).read_bytes())
    # A new release is not required to repeat the last release's observations.
    # Preserve the exact remote identity/version while freezing its new inputs.
    work = work.resolve()
    if not work.is_relative_to(data_home()) or work.is_relative_to(ROOT):
        raise ValueError("Publication workspace must be inside the configured data home")
    work.mkdir(parents=True, exist_ok=False)
    inputs = work / "inputs"
    inputs.mkdir()
    bindings = {k: dict(v) for k, v in target["bindings"].items() if k in target.get("correction_history", {}).get("binding_names", [])}
    def bind(name, source, filename):
        destination = inputs / filename
        shutil.copyfile(source, destination)
        bindings[name] = {"path": str(destination.relative_to(data_home())), "sha256": digest(destination.read_bytes())}
    for name, source, filename in (
        ("report_manifest", report / "manifest.json", "manifest.json"),
        ("content", content, "reader-content.json"),
        ("visual_acceptance", visual, "visual-acceptance.json"),
        ("relevance", report / "relevance-judgments.json", "relevance.json"),
        ("independent_review", report / "independent-review.json", "independent-review.json"),
    ):
        bind(name, source, filename)
    receipts = manifest["receipt"].get("separate_collections") or [dict(manifest["receipt"], cohort="primary")]
    if set(sources) != {r["cohort"] for r in receipts}:
        raise ValueError("Publication requires the exact checked cohort sources")
    expected = {"primary": selection["profile"], **{x["id"]: x for x in selection.get("supplementary_profiles", [])}}
    for receipt in receipts:
        cohort = receipt["cohort"]
        source = sources[cohort]
        if source.is_symlink() or digest((source / "receipt.json").read_bytes()) != receipt["receipt_sha256"]:
            raise ValueError("Publication collection receipt differs")
        if digest((source / "profile.json").read_bytes()) != receipt["profile_sha256"]:
            raise ValueError("Publication collection profile differs")
        bindings[cohort + "_profile"] = {k: expected[cohort][k] for k in ("path", "sha256")}
        bind(cohort + "_receipt", source / "receipt.json", cohort + "-receipt.json")
        bind(cohort + "_frozen_profile", source / "profile.json", cohort + "-profile.json")
    export(report, work / "delivery")
    verify_delivery(report, work / "delivery")
    bind("delivery_index", work / "delivery/delivery-index.json", "delivery-index.json")
    archive = work / "report.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for file in sorted((work / "delivery").rglob("*")):
            if file.is_symlink():
                raise ValueError("Publication archive contains a symlink")
            if file.is_file():
                output.write(file, file.relative_to(work / "delivery"))
    review = json.loads((inputs / "independent-review.json").read_bytes())
    relevance = json.loads((inputs / "relevance.json").read_bytes())
    profile = manifest["profile"]
    factual = {c["id"] for c in profile["cases"] if "reference_answer" in c}
    excluded = factual & set(profile.get("factual_quality_exclusions", []))
    candidate = dict(target, bindings=bindings, methods=[m["id"] for m in profile["methods"]], cases=len(profile["cases"]), logical_results=len(manifest["rows"]),
                     physical_calls=sum(len(r.get("attempts", [r])) for r in manifest["rows"]),
                     populations={"custom": len(profile["cases"]) - len(factual), "factual": len(factual), "factual_quality": len(factual - excluded), "factual_quarantined": len(excluded)},
                     independent_review={k: review[k] for k in ("case_total", "case_reviewed", "output_total", "output_reviewed", "candidate_total", "candidate_reviewed", "accepted_unreviewed_passes")},
                     quality_annotation={"queries": len(relevance["plan"]["case_ids"]), "candidates": sum(len(v) for v in relevance["labels"].values()), "scope": relevance["plan"]["scope"]},
                     archive_sha256=digest(archive.read_bytes()))
    candidate["independent_review"]["finding_dispositions"] = dict(Counter(d["resolution"] for d in review["adjudications"].values()))
    candidate["independent_review"]["reference_dispositions"] = dict(Counter(d["resolution"] for k, d in review["adjudications"].items() if k.endswith(":dataset") and k.removesuffix(":dataset") in factual))
    for obsolete in ("superseded_physical_calls", "cohort_inputs", "quality_judge"):
        candidate.pop(obsolete, None)
    plan = {"instance": {"path": str(instance_path), "sha256": digest(instance_path.read_bytes())}, "title": manifest.get("authored", {}).get("title", {}).get(manifest.get("primary_language", "zh"), manifest["profile"].get("title", "Search API evaluation")), "schema": "search-publication-plan/1", "expected_target": {k: target[k] for k in ("base_url", "slug", "versionId", "visibility")}, "target": candidate, "archive_sha256": candidate["archive_sha256"]}
    save(work / "publication-plan.json", plan)
    verify_plan(report, work)
    return work


def verify_plan(report, work):
    from .profiles import publication_authority, verify_publication_inputs
    plan = json.loads((work / "publication-plan.json").read_bytes())
    manifest = json.loads((report / "manifest.json").read_bytes())
    if digest((work / "report.zip").read_bytes()) != plan["archive_sha256"] or plan["target"]["archive_sha256"] != plan["archive_sha256"]:
        raise ValueError("Publication archive changed after planning")
    if plan["target"]["bindings"]["report_manifest"]["sha256"] != digest((report / "manifest.json").read_bytes()):
        raise ValueError("Publication plan belongs to another report")
    expected = plan["expected_target"]
    candidate = plan["target"]
    receipt_file = work / "publication-receipt.json"
    profile = Path(plan["instance"]["path"])
    if digest(profile.read_bytes()) != plan["instance"]["sha256"]:
        raise ValueError("Search instance changed after publication planning")
    root = authority_root(profile)
    selection = current_selection(profile)
    live = json.loads((root / selection["publication_target"]).read_bytes())
    if receipt_file.is_file():
        activated = json.loads(receipt_file.read_bytes())
        if live.get("versionId") == activated.get("versionId") and live.get("archive_sha256") == plan["archive_sha256"]:
            expected = {k: live[k] for k in ("base_url", "slug", "versionId", "visibility")}
            candidate = dict(candidate, versionId=live["versionId"])
    publication_authority(manifest, profile, root=root, planned_target=candidate, expected_target=expected)
    visual_binding = plan["target"]["bindings"]["visual_acceptance"]
    visual = contained_path(data_home(), visual_binding["path"], reject_symlinks=True)
    if json.loads(visual.read_bytes())["manifest_sha256"] != digest((report / "manifest.json").read_bytes()):
        raise ValueError("Publication visual evidence belongs to another build")
    delivery_binding = plan["target"]["bindings"]["delivery_index"]
    if digest((work / "delivery/delivery-index.json").read_bytes()) != delivery_binding["sha256"]:
        raise ValueError("Publication delivery index changed")

    verify_publication_inputs(manifest, plan["target"], root=root)
    return plan


def publish(report, work, *, transport=None):
    plan = verify_plan(report, work)
    target = plan["target"]
    authority = work / "site-authority.json"
    receipt_file = work / "publication-receipt.json"
    desired = {"publication": {"base_url": target["base_url"], "site_slug": target["slug"], "version_id": target["versionId"]}}
    if not authority.exists():
        save(authority, desired)
    elif json.loads(authority.read_bytes()) != desired:
        raise ValueError("Publication authority differs from the frozen plan")
    if not receipt_file.exists():
        save(receipt_file, {"slug": target["slug"], "versionId": target["versionId"], "base_url": target["base_url"]})
    def package_check(archive, source):
        if digest(archive.read_bytes()) != plan["archive_sha256"]:
            raise ValueError("Frozen publication archive changed")
        verify_plan(report, work)
    completed = json.loads(receipt_file.read_bytes())
    if completed.get("archive_sha256") == plan["archive_sha256"] and completed.get("publication_pending") is False and completed.get("versionId") != target["versionId"]:
        result = completed
    else:
        result = publish_package(
        PublishConfig(authority, work / "report.zip", work / "delivery", receipt_file,
                      work / "operation-pending.json", mode="update",
                      title=plan.get("title", "Search API evaluation"),
                      operation_namespace="search", visibility=target["visibility"]),
            transport=transport, package_check=package_check,
            validation_inputs=(work / "publication-plan.json", work / "inputs"),
        )
    updated = dict(target, previous_version=target["versionId"], versionId=result["versionId"], published_at=datetime.now(UTC).isoformat(), code_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip())
    # Keep the exact planned files immutable after activation, then bind their new paths.
    frozen = work.parent / "published" / result["versionId"]
    if not frozen.exists():
        shutil.copytree(work / "inputs", frozen)
    for binding in updated["bindings"].values():
        if binding["path"].startswith(str((work / "inputs").relative_to(data_home())) + "/"):
            binding["path"] = str((frozen / Path(binding["path"]).name).relative_to(data_home()))
    profile = Path(plan["instance"]["path"])
    selection = current_selection(profile)
    save(authority_root(profile) / selection["publication_target"], updated)
    return updated["url"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, help="External instance selector; required for a new publication")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--visual-receipt", type=Path)
    parser.add_argument("--content", type=Path)
    parser.add_argument("--source", action="append", default=[], metavar="COHORT=RUN")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if not args.resume:
        if not args.visual_receipt or not args.content or not args.profile:
            parser.error("A new publication requires --profile, --visual-receipt and --content")
        prepare(args.report, args.visual_receipt, args.content, {v.split("=", 1)[0]: Path(v.split("=", 1)[1]) for v in args.source}, args.output, profile=args.profile)
    print(publish(args.report, args.output))
