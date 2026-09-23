"""Check the full WebArena report's measured semantics and evidence links."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from pathlib import Path


def check_report(root: Path) -> None:
    if root.is_symlink() or any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("report package contains a symlink")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("schema") != "lex_webarena_full_acceptance_report/1"
            or manifest.get("generated_languages") != ["zh"]
            or manifest.get("task_count") != 812 or manifest.get("pass") != 812
            or len(manifest.get("initial_failures") or []) != 26):
        raise ValueError("report manifest differs from the audited population")
    home = (root / "index.html").read_text(encoding="utf-8")
    dataset = (root / "dataset.html").read_text(encoding="utf-8")
    contract_viewer = (root / "contract.html").read_text(encoding="utf-8")
    contracts = json.loads((root / "dataset-contracts.json").read_text(encoding="utf-8"))
    method = (root / "method.html").read_text(encoding="utf-8")
    viewer = (root / "evidence.html").read_text(encoding="utf-8")
    english = (root / "en" / "index.html").read_text(encoding="utf-8")
    if (home.count('data-search="') != 812
            or home.count('href="case/') != 812
            or home.count("<h2>核心结果</h2>") != 1
            or "812 / 812" not in home or "786 / 812" not in home
            or any(label not in home for label in ("Mean 耗时", "P50 耗时", "P95 耗时", "网站分布", "逐题结果"))):
        raise ValueError("result summary, denominator or inventory is missing")
    if (dataset.count("<tr>") != 813 or dataset.count('href="contract.html?task=') != 812
            or "测得分数" not in dataset or "score 1.0" in dataset
            or contracts.get("schema") != "lex_webarena_full_contracts/1"
            or len(contracts.get("tasks") or []) != 812
            or "dataset-contracts.json" not in contract_viewer):
        raise ValueError("dataset population or measurement separation changed")
    for task_id, contract in enumerate(contracts["tasks"]):
        if (contract.get("task_id") != task_id or not contract.get("official_evaluators")
                or not contract.get("fixed_actions")):
            raise ValueError(f"task {task_id} acceptance contract or trajectory is missing")
    if ("部署与镜像" not in method or "第 790 题" not in method
            or "没有数据库恢复到初始种子的证据" not in method
            or "早期" not in home or "evidence/" not in viewer
            or "English edition not generated" not in english
            or '../index.html' not in english):
        raise ValueError("method, evidence viewer or language notice is incomplete")
    failures = set(manifest["initial_failures"])
    for task_id in range(812):
        case = root / "case" / f"{task_id:03d}.html"
        evidence = root / "evidence" / f"{task_id:03d}.json"
        if not case.is_file() or not evidence.is_file():
            raise ValueError(f"task {task_id} result evidence is missing")
        page = case.read_text(encoding="utf-8")
        record = json.loads(evidence.read_text(encoding="utf-8"))
        if (page.count('data-report-detail') != 1
                or f"第 {task_id} 题" not in page
                or f"../evidence.html?task={task_id:03d}" not in page
                or f"../evidence/{task_id:03d}.json" not in page
                or record.get("task_id") != task_id
                or record["attempts"][-1]["status"] != "pass"
                or float(record["attempts"][-1]["evaluation"]["score"]) != 1):
            raise ValueError(f"task {task_id} result, verdict or evidence target differs")
        if task_id in failures:
            if ("首轮失败与处理" not in page or "查看原始错误" not in page
                    or record["attempts"][0]["status"] == "pass"
                    or not any(item.get("error") or item.get("evaluation") for item in record["attempts"][:-1])):
                raise ValueError(f"task {task_id} failure diagnosis is missing")
        elif "首轮失败与处理" in page:
            raise ValueError(f"task {task_id} invented a failure")
        if "/Users/" in page or "/Users/" in evidence.read_text(encoding="utf-8"):
            raise ValueError(f"task {task_id} exposes a local path")
    if "目标差评已不存在" not in (root / "case" / "790.html").read_text(encoding="utf-8"):
        raise ValueError("task 790 validation boundary is missing")
    for source, page in ((root / "index.html", home), (root / "dataset.html", dataset),
                         (root / "contract.html", contract_viewer),
                         (root / "method.html", method), (root / "evidence.html", viewer),
                         (root / "en" / "index.html", english)):
        for link in re.findall(r'href="([^"]+)"', page):
            if link.startswith(("http:", "https:", "#")) or link == "case/":
                continue
            if not (source.parent / link.split("?", 1)[0]).is_file():
                raise ValueError(f"broken report navigation: {link}")


def negative_controls(root: Path) -> None:
    edits = (
        ("index.html", "<h2>核心结果</h2>", ""),
        ("case/525.html", "../evidence/525.json", "../evidence/missing.json"),
        ("case/525.html", "查看原始错误", "缺少原始错误"),
        ("method.html", "没有数据库恢复到初始种子的证据", "数据库已恢复到初始种子"),
    )
    for name, original, changed in edits:
        with tempfile.TemporaryDirectory(prefix="webarena-report-negative-") as directory:
            copy = Path(directory) / "report"
            shutil.copytree(root, copy, copy_function=os.link)
            target = copy / name
            source = target.read_text(encoding="utf-8")
            if original not in source:
                raise ValueError(f"negative control source marker is absent: {original}")
            target.unlink()
            target.write_text(source.replace(original, changed, 1), encoding="utf-8")
            try:
                check_report(copy)
            except ValueError:
                pass
            else:
                raise ValueError(f"negative control was not rejected: {name} {original}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--negative-controls", action="store_true")
    args = parser.parse_args()
    check_report(args.report)
    if args.negative_controls:
        negative_controls(args.report)
    print("full WebArena report: 812 results and evidence targets verified")


if __name__ == "__main__":
    main()
