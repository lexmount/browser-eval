"""Version-one synthetic report exercising success, blocked inputs and evidence."""

import argparse
from pathlib import Path

from harness.web_scraper.pipeline import digest, save_json
from harness.web_scraper.report import PAGES, build


def create(output: Path, *, language="zh", mode="compatibility"):
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
    for cid in truth:
        p = root / "html" / (cid + ".html")
        p.parent.mkdir(exist_ok=True)
        p.write_text("<html><p>synthetic product</p></html>")
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
            "dataset": {
                "id": "synthetic",
                "repository": "example/synthetic",
                "commit": "synthetic-v1",
                "cases": 3,
                "files": {},
            }
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

        model = json.loads(content.read_text())
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
                    "method": method,
                    "dataset_manifest_sha256": sha,
                    "pipeline_sha256": digest(source),
                },
            )
            physical = []
            for cid in list(truth) if method == "synthetic-success" else list(truth)[:1]:
                row = json.loads((run / "calls" / f"{method}-{cid}.json").read_text())
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
                "".join(json.dumps(r) + "\n" for r in physical)
            )
            model["evidence_runs"].append(
                {
                    "run": method,
                    "plan": str(plan_path),
                    "plan_sha256": digest(plan_path.read_bytes()),
                }
            )
        save_json(content, model)
    build(output, content, home=home, profile=profile, language=language)
    return home


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--language", choices=["en", "zh"], default="zh")
    p.add_argument(
        "--mode", choices=["compatibility", "full-cohort-progress"], default="compatibility"
    )
    a = p.parse_args()
    create(a.output, language=a.language, mode=a.mode)
