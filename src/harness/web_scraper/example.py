"""Version-one synthetic report exercising success, blocked inputs and evidence."""

import argparse
from pathlib import Path

from harness.web_scraper.pipeline import digest, save_json
from harness.web_scraper.report import PAGES, build


def create(output: Path, *, language="zh"):
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
    build(output, content, home=home, profile=profile, language=language)
    return home


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--language", choices=["en", "zh"], default="zh")
    a = p.parse_args()
    create(a.output, language=a.language)
