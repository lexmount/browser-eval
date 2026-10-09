"""Synthetic frozen sample and one-case replacement evidence; no provider calls."""

import hashlib
import json

from harness.web_fetch import webmainbench_case_correction as correction
from harness.web_fetch.webmainbench_scoring import CORE_METRICS, SCORER_COMMIT, aggregate


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def correction_fixture(root, providers=("kitesurf", "lightpanda", "obscura", "moli", "chromium")):
    root.mkdir(parents=True)
    source = root / "source"
    source.mkdir()
    dataset = root / "dataset.jsonl"
    records = [{"track_id": f"case-{i}", "html": f"<p>{i}</p>",
                "groundtruth_content": str(i), "meta": {}} for i in range(200)]
    write_rows(dataset, records)
    manifest = root / "sample.json"
    write_json(manifest, {"cases": [{"track_id": row["track_id"]} for row in records]})
    profile = root / "profile.json"
    write_json(profile, {"providers": list(providers), "case_count": 200,
                         "source_dataset_sha256": correction.digest(dataset),
                         "sample_manifest_sha256": correction.digest(manifest),
                         "timeout_seconds": 45, "max_failure_retries": 1,
                         "official_scorer_commit": SCORER_COMMIT})

    def shard(path, provider, selected, *, replacement=False):
        (path / "artifacts" / provider).mkdir(parents=True)
        rows, calls = [], []
        for record in selected:
            case = record["track_id"]
            body = "corrected" if replacement else "old"
            relative = f"artifacts/{provider}/{case}.md"
            (path / relative).write_text(body)
            output_hash = correction.digest(path / relative)
            for index, content in enumerate(("failed retry", body) if replacement else (body,), 1):
                physical = f"artifacts/{provider}/{case}-p{index:02d}.txt"
                (path / physical).write_text(content)
                calls.append({"provider": provider, "track_id": case,
                              "attempt_id": f"{provider}__{case}__p{index:02d}",
                              "physical_attempt": index, "elapsed_ms": 10,
                              "physical_artifact_path": physical,
                              "output_sha256": correction.digest(path / physical)})
            metrics = {name: {"score": 0.8 if replacement else 0.2, "success": True}
                       for name in (*CORE_METRICS, "overall")}
            rows.append({"provider": provider, "track_id": case, "ok": True,
                         "attempt_id": f"{provider}__{case}__r001", "artifact_path": relative,
                         "physical_artifact_path": calls[-1]["physical_artifact_path"],
                         "output_sha256": output_hash, "elapsed_ms": 10,
                         "physical_attempt_count": 2 if replacement else 1,
                         "total_elapsed_ms": 20 if replacement else 10,
                         "input_html_sha256": hashlib.sha256(record["html"].encode()).hexdigest(),
                         "reference_sha256": hashlib.sha256(record["groundtruth_content"].encode()).hexdigest(),
                         "meta": {}, "metrics": metrics})
        write_rows(path / "scored-results.jsonl", rows)
        write_rows(path / "results.jsonl", [{k: v for k, v in row.items() if k != "metrics"} for row in rows])
        write_rows(path / "physical-attempts.jsonl", calls)
        write_json(path / "dataset.json", {"case_count": len(selected),
                                           "case_ids": [r["track_id"] for r in selected]})
        write_json(path / "execution.json", {"provider": provider, "case_count": len(selected),
                                             "source_dataset_sha256": correction.digest(dataset),
                                             "timeout_seconds": 45, "max_failure_retries": 1,
                                             "implementation_identity": {"binary_sha256": provider + "-binary"},
                                             "run_id": "new" if replacement else "original",
                                             "runner_sha256": "runner"})
        write_json(path / "score-summary.json", {"provider": provider, "case_count": len(selected),
                                                 "scorer": {"commit": SCORER_COMMIT},
                                                 "official_aggregate": aggregate(rows)})
        (path / "private-worker.txt").write_text("private")
        (path / f"artifacts/{provider}/unbound.txt").write_text("unbound")

    for provider in providers:
        shard(source / provider, provider, records)
    replacement = root / "replacement"
    selected_provider = providers[-1]
    shard(replacement, selected_provider, records[:1], replacement=True)
    return source, {selected_provider: replacement}, root / "output", "case-0", (selected_provider,), dataset, manifest, profile
