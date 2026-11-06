"""Version-one synthetic report exercising success, blocked inputs and evidence."""

import argparse
from pathlib import Path

from harness.web_extract.pipeline import digest, read_json, require_external_output, save_json
from harness.web_extract.report import PAGES, build


def create(output: Path, *, language="zh", mode="compatibility"):
    if mode == "schema-extraction-cohort":
        return create_schema(output, language=language)
    if mode == "field-extraction-cohort":
        return create_record(output, language=language)
    require_external_output(output)
    output = output.absolute()
    home = output.parent / (output.name + "-synthetic-inputs")
    home.mkdir(parents=True, exist_ok=False)
    truth = {
        str(i) * 64: {
            "url": "https://example.org/product/" + str(i),
            "sku": [str(i)],
            "availability": ["InStock"],
        }
        for i in range(1, 4)
    }
    root = home / "dataset"
    save_json(root / "dataset/ground-truth.json", truth)
    (root / "evaluate.py").write_text(
        """# Synthetic matching fixture; not measured benchmark output.
from decimal import Decimal
def hard_matching(true, pred):
    if pred is None:
        return (0, 0, int(bool(true)))
    return (1, 0, 0) if pred in true else (0, 1, int(bool(true)))
def decimal_matching(true, pred):
    value = Decimal(pred) if pred is not None else None
    return hard_matching({Decimal(x) for x in true}, value)
def metrics_from_tp_fp_fns(rows):
    tp, fp, fn = map(sum, zip(*rows))
    p = 1.0 if fp == fn == 0 else tp / (tp + fp) if tp + fp else 0.0
    r = 1.0 if fp == fn == 0 else tp / (tp + fn) if tp + fn else 0.0
    return {'precision': p, 'recall': r, 'f1': 2*p*r/(p+r) if p+r else 0.0}
def evaluate(truth, predictions, field, matcher, **options):
    return metrics_from_tp_fp_fns([
        matcher(set(reference.get(field, [])), predictions[case_id].get(field))
        for case_id, reference in truth.items()
    ])
""",
        encoding="utf-8",
    )
    for cid in truth:
        p = root / "html" / (cid + ".html")
        p.parent.mkdir(exist_ok=True)
        p.write_text("<html><p>synthetic product</p></html>", encoding="utf-8")
    files = {
        p.relative_to(root).as_posix(): {
            "sha256": digest(p.read_bytes()),
            "bytes": p.stat().st_size,
        }
        for p in root.rglob("*")
        if p.is_file()
    }
    save_json(root / "manifest.json", {"commit": "synthetic-v1", "cases": 3, "files": files})
    profile = home / "profile.json"
    save_json(
        profile,
        {
            "local_example": True,
            "dataset": {
                "id": "synthetic",
                "repository": "example/synthetic",
                "commit": "synthetic-v1",
                "cases": 3,
                "files": {},
            },
        },
    )
    sha = digest((root / "manifest.json").read_bytes())
    save_json(
        home / "historical-reproduction.json",
        {
            "kind": "synthetic",
            "dataset_manifest_sha256": sha,
            "scores": {
                "Synthetic A": {
                    f: {"f1": 0.8} for f in ("price", "sku", "availability", "OutOfStock")
                },
                "Synthetic B": {
                    f: {"f1": 0.5} for f in ("price", "sku", "availability", "OutOfStock")
                },
            },
        },
    )
    run = home / "runs/example-v1"
    run.mkdir(parents=True)
    save_json(
        run / "contract.json",
        {
            "case_ids": list(truth),
            "methods": ["synthetic-success", "synthetic-blocked"],
            "kind": "input_compatibility_only",
            "dataset_manifest_sha256": sha,
        },
    )
    entries = []
    for method in ("synthetic-success", "synthetic-blocked"):
        for cid in truth:
            good = method == "synthetic-success"
            row = {
                "method": method,
                "case_id": cid,
                "input_sha256": digest((root / "html" / (cid + ".html")).read_bytes()),
                "state": "response_received" if good else "input_blocked",
                "compatibility": "structured_output" if good else "no_structured_output",
                "normalized_fields": {"sku": cid[0]} if good else None,
                "response": {"json": {"sku": cid[0]}} if good else {},
                "http_status": 200 if good else None,
                "error": "" if good else "Synthetic route rejects archived HTML",
                "elapsed_seconds": 1.0,
            }
            p = run / "calls" / f"{method}-{cid}.json"
            save_json(p, row)
            entries.append(
                {"path": p.relative_to(run).as_posix(), "sha256": digest(p.read_bytes())}
            )
    save_json(
        run / "manifest.json",
        {"contract_sha256": digest((run / "contract.json").read_bytes()), "calls": entries},
    )
    content = home / "content.json"
    save_json(
        content,
        {
            "evidence_runs": [{"run": "example-v1"}],
            "pages": {
                p: {
                    "title": {"en": "Synthetic example · " + p, "zh": "合成示例 · " + p},
                    "body": {
                        "en": "<p>Synthetic evidence for renderer testing only.</p>",
                        "zh": "<p>用于测试报告展示的合成数据。</p>",
                    },
                }
                for p in PAGES
            },
        },
    )
    if mode == "full-cohort-progress":
        import json

        model = json.loads(content.read_text(encoding="utf-8"))
        methods = ["synthetic-success", "synthetic-blocked"]
        plan_path = home / "full-plan.json"
        plan = {
            "case_count": len(truth),
            "participants": {m: {} for m in methods},
            "case_ids_sha256": digest(("\n".join(sorted(truth)) + "\n").encode()),
        }
        save_json(plan_path, plan)
        model["mode"] = mode
        model["cohort_plan"] = {"path": str(plan_path), "sha256": digest(plan_path.read_bytes())}
        model["evidence_runs"] = []
        model["prices"] = {
            m: {
                "usd_per_1000": "$0 external API fee (synthetic)",
                "terms": {
                    "zh": "合成数据，不产生实际费用。",
                    "en": "Synthetic data; no actual charge.",
                },
                "verified_at": "2026-01-01",
                "sources": ["https://example.org/synthetic-pricing"],
            }
            for m in methods
        }
        for method in methods:
            target = home / "runs" / method
            source = b"# explicitly synthetic execution snapshot\n"
            save_json(
                target / "contract.json",
                {
                    "kind": "full_archived_extraction",
                    "case_ids": list(truth),
                    "methods": [method],
                    "dataset_manifest_sha256": sha,
                    "pipeline_sha256": digest(source),
                },
            )
            (target / "execution").mkdir()
            (target / "execution/pipeline.py").write_bytes(source)
            save_json(
                target / "certification-precall.json",
                {
                    "profile_sha256": digest(plan_path.read_bytes()),
                    "case_ids_sha256": digest(("\n".join(sorted(truth)) + "\n").encode()),
                    "method": method,
                    "dataset_manifest_sha256": sha,
                    "pipeline_sha256": digest(source),
                },
            )
            physical = []
            for cid in list(truth) if method == "synthetic-success" else list(truth)[:1]:
                row = json.loads(
                    (run / "calls" / f"{method}-{cid}.json").read_text(encoding="utf-8")
                )
                row["input_sha256"] = digest((root / "html" / (cid + ".html")).read_bytes())
                dest = target / "calls" / f"{method}-{cid}.json"
                save_json(dest, row)
                physical.append(
                    {
                        "path": dest.relative_to(target).as_posix(),
                        "sha256": digest(dest.read_bytes()),
                    }
                )
            (target / "calls.journal.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in physical), encoding="utf-8"
            )
            model["evidence_runs"].append(
                {
                    "run": method,
                    "plan": str(plan_path),
                    "plan_sha256": digest(plan_path.read_bytes()),
                }
            )
        save_json(content, model)
    if mode != "full-cohort-progress":
        model = read_json(content)
        model["prices"] = {
            m: {
                "usd_per_1000": "$0 synthetic",
                "terms": {"en": "Synthetic evidence", "zh": "合成数据"},
                "verified_at": "2026-01-01",
                "sources": ["https://example.org/synthetic-pricing"],
            }
            for m in ("synthetic-success", "synthetic-blocked")
        }
        save_json(content, model)
    build(output, content, home=home, profile=profile, language=language)
    return home


def create_record(output: Path, *, language="zh"):
    """Exercise multi-value schemas and an interrupted matrix without provider calls."""
    if language != "zh":
        raise ValueError("The record example currently supports Chinese only")
    import json

    require_external_output(output)
    output = output.absolute()
    home = output.parent / (output.name + "-synthetic-inputs")
    home.mkdir(parents=True, exist_ok=False)
    root = home / "dataset"
    truth = {
        cid: {
            "domain": "book",
            "site": "example",
            "url": "https://example.org/" + cid,
            "values": {"title": ["Example"], "author": ["A", "B"]},
        }
        for cid in ("one", "two", "three")
    }
    save_json(root / "dataset/ground-truth.json", truth)
    save_json(
        root / "schemas.json",
        {
            "book": {
                "type": "object",
                "properties": {
                    key: {"type": "array", "items": {"type": "string"}}
                    for key in ("title", "author")
                },
            }
        },
    )
    for cid in truth:
        path = root / "html" / (cid + ".html")
        path.parent.mkdir(exist_ok=True)
        path.write_text("<html><p>Example</p><p>A</p><p>B</p></html>", encoding="utf-8")
    files = {
        p.relative_to(root).as_posix(): {
            "sha256": digest(p.read_bytes()),
            "bytes": p.stat().st_size,
        }
        for p in root.rglob("*")
        if p.is_file()
    }
    save_json(
        root / "manifest.json",
        {"benchmark": "swde", "commit": "synthetic-record-v1", "cases": len(truth), "files": files},
    )
    profile = home / "profile.json"
    model = {
        "mode": "field-extraction-cohort",
        "title": "多值字段合成示例",
        "dataset_manifest": str(root / "manifest.json"),
        "participants": {m: {"label": m} for m in ("scrapegraph-extract", "firecrawl-json")},
        "evidence_runs": [],
        "snapshot_base": "https://example.org/frozen",
        "prices": {
            m: {
                "usd_per_1000": "$0 synthetic",
                "terms": {"zh": "合成数据，无实际费用"},
                "verified_at": "2026-01-01",
                "sources": ["https://example.org/pricing"],
            }
            for m in ("scrapegraph-extract", "firecrawl-json")
        },
    }
    plan = {
        "case_count": len(truth),
        "case_ids_sha256": digest(("\n".join(sorted(truth)) + "\n").encode()),
        "participants": {m: {} for m in model["participants"]},
    }
    plan_path = home / "plan.json"
    save_json(plan_path, plan)
    manifest_sha = digest((root / "manifest.json").read_bytes())
    for method in model["participants"]:
        run = home / "runs" / method
        source = b"# Synthetic execution; no network calls.\n"
        save_json(
            run / "contract.json",
            {
                "kind": "full_archived_extraction",
                "case_ids": list(truth),
                "methods": [method],
                "dataset_manifest_sha256": manifest_sha,
                "pipeline_sha256": digest(source),
            },
        )
        (run / "execution").mkdir()
        (run / "execution/pipeline.py").write_bytes(source)
        save_json(
            run / "certification-precall.json",
            {
                "profile_sha256": digest(plan_path.read_bytes()),
                "case_ids_sha256": digest(("\n".join(sorted(truth)) + "\n").encode()),
                "method": method,
                "dataset_manifest_sha256": manifest_sha,
                "pipeline_sha256": digest(source),
            },
        )
        entries = []
        for i, cid in enumerate(truth):
            if method == "firecrawl-json" and i > 0:
                continue
            good = i != 2
            row = {
                "case_id": cid,
                "method": method,
                "http_status": 200 if good else 422,
                "response": {
                    "json": {"title": ["Example"], "author": ["A", "B"] if i == 0 else ["A"]}
                }
                if good
                else {"error": "Synthetic navigation timeout"},
                "normalized_fields": {
                    "title": ["Example"],
                    "author": ["A", "B"] if i == 0 else ["A"],
                }
                if good
                else {},
                "elapsed_seconds": float(i + 1),
                "compatibility": "structured_output" if good else "no_structured_output",
                "state": "response_received",
                "input_sha256": digest((root / "html" / (cid + ".html")).read_bytes()),
            }
            if good and method == "firecrawl-json":
                row["response"] = {"data": row["response"]}
            dest = run / "calls" / (cid + ".json")
            save_json(dest, row)
            entries.append(
                {"path": dest.relative_to(run).as_posix(), "sha256": digest(dest.read_bytes())}
            )
        (run / "calls.journal.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8"
        )
        model["evidence_runs"].append(
            {"run": str(run), "plan": str(plan_path), "plan_sha256": digest(plan_path.read_bytes())}
        )
    content = home / "content.json"
    save_json(content, model)
    save_json(
        profile,
        {
            "local_example": True,
            "dataset": {"commit": "synthetic-record-v1", "cases": len(truth)},
            "research_report": {"content": {"sha256": digest(content.read_bytes())}},
        },
    )
    build(output, content, home=home, profile=profile, language=language)
    return home


def create_schema(output: Path, *, language="zh"):
    """Exercise arbitrary JSON, complete review, failures and three participants offline."""
    import json

    from harness.web_extract.messages import message
    from harness.web_extract.pipeline import execution_identity
    from harness.web_extract.review import json_leaf_paths

    require_external_output(output)
    output = output.absolute()
    home = output.parent / (output.name + "-synthetic-inputs")
    home.mkdir(parents=True, exist_ok=False)
    methods = ("scrapegraph-extract", "firecrawl-parse-json", "cloudflare-json")
    cases, rows, scores, results = {}, [], {}, {}
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    for cid in ("one", "two"):
        source = home / "dataset/markdown" / (cid + ".md")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("# Amber mug\nPrice: 12 EUR.\n", encoding="utf-8")
        cases[cid] = {"schema_text": json.dumps(schema), "response": {"name": "Amber mug"}}
    for index, method in enumerate(methods):
        per_case = {}
        for ordinal, cid in enumerate(cases):
            delivered = not (index == 2 and ordinal == 1)
            correct = index == 0 or ordinal == 0
            value = {"name": "Amber mug" if correct else "Blue bowl"} if delivered else None
            row = {"method": method, "case_id": cid, "elapsed_seconds": .1 * (index + 1),
                   "http_status": 200 if delivered else 400, "normalized_fields": value,
                   "response": {"json": value}, "request": {"schema": schema},
                   "compatibility": "structured_output" if delivered else "no_structured_output",
                   "error": None if delivered else "Synthetic schema rejection"}
            rows.append(row)
            per_case[cid] = {"delivered": delivered, "key_f1": float(delivered),
                "value_score": float(correct and delivered),
                "item_f1": float(correct and delivered),
                "exact_record_match": correct and delivered}
            results[method + ":" + cid] = {"method": method, "case_id": cid,
                "verdict": ("correct" if correct and delivered
                            else "partial" if delivered else "no_output"),
                "cause": ("correct_or_equivalent" if correct and delivered
                          else "extraction_error" if delivered else "delivery_failure"),
                "fields": [{"field": "name", "status": "supported" if correct else "incorrect",
                            "source_quote": "Amber mug", "reason": "Synthetic evidence",
                            "findings": []}],
                "summary": "Synthetic evidence",
                "execution_identity": execution_identity(row),
                "checked_paths": json_leaf_paths(value) if delivered else []}
        scores[method] = {"delivery_count": sum(v["delivered"] for v in per_case.values()),
            "per_case": per_case, "mean": {key: sum(v[key] for v in per_case.values()) / 2
                                           for key in next(iter(per_case.values()))}}
    data = home / "synthetic-data.json"
    save_json(data, {"cases": cases, "rows": rows, "scores": scores,
        "audit": {"reviewed_results": 6, "case_count": 2, "results": results,
                  "review_kind": "Synthetic fixture only", "reviewer": {"model": "synthetic"},
                  "cause_distribution": {}}})
    model = {"mode": "schema-extraction-cohort", "synthetic": True,
        "synthetic_data": {"path": str(data), "sha256": digest(data.read_bytes())},
        "title": {lang: "Web Extract · JSON Schema · " + message("schema_synthetic", lang)
                  for lang in ("en", "zh")},
        "participants": {m: {"label": m} for m in methods}, "candidates": [], "audit": None,
        "prices": {m: {"usd_per_1000": "$0 synthetic", "verified_at": "2026-01-01",
                       "terms": {lang: message("schema_synthetic", lang) for lang in ("en", "zh")},
                       "sources": ["https://example.org/synthetic"]} for m in methods}}
    content, profile = home / "content.json", home / "profile.json"
    save_json(content, model)
    save_json(profile, {"independent_result_review_scope": "all",
                       "research_report": {"content": {"sha256": digest(content.read_bytes())}}})
    build(output, content, home=home, language=language, profile=profile)
    return home


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--language", choices=["en", "zh"], default="zh")
    p.add_argument(
        "--mode",
        choices=["compatibility", "full-cohort-progress", "field-extraction-cohort",
                 "schema-extraction-cohort"],
        default="compatibility",
    )
    a = p.parse_args()
    create(a.output, language=a.language, mode=a.mode)
