#!/usr/bin/env python3
"""Build the committed 100-case webpage-only Web Fetch suite.

The source benchmark is pinned.  A local WCXB checkout may be supplied for an
offline/review build; otherwise the selected JSON records are downloaded from
the pinned GitHub commit.
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from copy import deepcopy
from pathlib import Path

from build_public_webfetch_50 import (
    LIGHTMOUNT_CASES,
    LIGHTMOUNT_COMMIT,
    WCXB_COMMIT,
    WCXB_IDS,
    lightmount_case,
)


ROOT = Path(__file__).resolve().parents[1]

# The first 28 are the existing four-per-type sample.  The additional 51 use
# distinct domains within each page type where possible.  Article and
# documentation receive one extra case because long-form extraction and code /
# documentation templates are the two most common failure clusters in the
# first 50-case run.
WCXB_ADDITIONAL: tuple[tuple[str, str], ...] = (
    ("0173", "dev"), ("0486", "dev"), ("0342", "dev"), ("0149", "dev"),
    ("0048", "dev"), ("0031", "dev"), ("0459", "dev"), ("5005", "test"),
    ("0524", "dev"), ("5009", "test"), ("0510", "dev"), ("0567", "dev"),
    ("5101", "test"), ("1674", "dev"), ("0519", "dev"),
    ("4206", "test"), ("3058", "dev"), ("0612", "dev"), ("3061", "dev"),
    ("0604", "dev"), ("0590", "dev"), ("0626", "dev"),
    ("4465", "test"), ("2960", "dev"), ("0650", "dev"), ("0655", "dev"),
    ("4396", "test"), ("4736", "dev"), ("1310", "dev"),
    ("0132", "dev"), ("2967", "dev"), ("2973", "dev"), ("4372", "test"),
    ("2965", "dev"), ("2968", "dev"), ("2916", "dev"),
    ("0703", "dev"), ("2393", "dev"), ("1766", "dev"), ("5286", "test"),
    ("5287", "test"), ("0701", "dev"), ("5456", "test"), ("1442", "dev"),
    ("0130", "dev"), ("4874", "dev"), ("0088", "dev"), ("4282", "dev"),
    ("5687", "test"), ("0168", "dev"), ("4477", "dev"),
)


def load_wcxb(file_id: str, split: str, wcxb_root: Path | None) -> tuple[dict, str]:
    source_url = (
        "https://raw.githubusercontent.com/Murrough-Foley/"
        f"web-content-extraction-benchmark/{WCXB_COMMIT}/{split}/ground-truth/{file_id}.json"
    )
    if wcxb_root:
        path = wcxb_root / split / "ground-truth" / f"{file_id}.json"
        return json.loads(path.read_text(encoding="utf-8")), source_url
    request = urllib.request.Request(source_url, headers={"User-Agent": "browser-harness-eval-dataset-builder/2"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response), source_url


def wcxb_case(file_id: str, split: str, wcxb_root: Path | None) -> dict:
    source, source_url = load_wcxb(file_id, split, wcxb_root)
    truth = source["ground_truth"]
    page_type = source["_internal"]["page_type"]["primary"]
    required = []
    seen_required = set()
    for snippet in truth.get("with") or []:
        normalized = " ".join(snippet.split()).casefold()
        if normalized in seen_required:
            continue
        seen_required.add(normalized)
        required.append(snippet)
        if len(required) == 3:
            break
    forbidden = list(truth.get("without") or [])[:3]
    title = truth.get("title") or required[0]
    if len(required) < 3:
        raise ValueError(f"WCXB {file_id} has fewer than three required snippets")
    return {
        "id": f"wcxb-{file_id}",
        "name": f"WCXB {page_type} · {title}",
        "category": f"wcxb_{page_type}",
        "url": source["url"],
        "expected_any": [title, required[0]],
        "min_content_chars": 300,
        "required_facts": [
            {"id": f"required-{index + 1}", "alternatives": [snippet]}
            for index, snippet in enumerate(required)
        ],
        "forbidden_any": forbidden,
        "description": f"WCXB 的 {page_type} 页面；检查正文召回与样板噪声排除。",
        "tags": ["wcxb", page_type, "live-url", "cross-domain-template"],
        "ground_truth_evidence": [{"source": source_url, "supports": "WCXB CC-BY-4.0 人工复核标准答案"}],
        "annotation_notes": "从 WCXB 固定提交导入。评测访问 live URL；页面与冻结答案不一致时应标记数据漂移，不算供应商错误。",
        "task_prompt": f"读取给定 {page_type} 页面并返回主要内容。必须覆盖三个冻结正文片段，并排除导航、登录、版权或购买控件等样板噪声。",
        "reference_answer": truth.get("main_content") or "\n\n".join([title, *required]),
        "human_review_checklist": [
            "是否取得目标页面而非登录、挑战或错误页",
            "是否覆盖三个冻结正文片段",
            "是否排除列出的样板噪声",
            "若 live 页面已变化，是否标记为标准答案漂移",
        ],
        "source_benchmark": {
            "name": "WCXB", "file_id": file_id, "split": split,
            "commit": WCXB_COMMIT, "license": "CC-BY-4.0",
        },
        "ground_truth_main_content": truth.get("main_content") or "",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wcxb-root", type=Path, help="WCXB checkout containing dev/ and test/")
    parser.add_argument("--output", type=Path, default=ROOT / "datasets" / "public_webfetch_100.json")
    args = parser.parse_args()

    core = json.loads((ROOT / "datasets" / "public_webfetch.json").read_text(encoding="utf-8"))
    cases = [deepcopy(case) for case in core["cases"] if case["category"] != "pdf"]
    cases.extend(wcxb_case(file_id, "dev", args.wcxb_root) for file_id in WCXB_IDS)
    cases.extend(wcxb_case(file_id, split, args.wcxb_root) for file_id, split in WCXB_ADDITIONAL)
    cases.extend(lightmount_case(spec) for spec in LIGHTMOUNT_CASES)
    assert len(cases) == 100
    assert len({case["id"] for case in cases}) == 100
    assert all(case["category"] not in {"pdf", "document_pdf", "document_docx", "document_txt", "media_image", "media_video"} for case in cases)

    output = {
        "schema_version": 5,
        "id": "public-webfetch-v5.0-100-hil",
        "name": "Web Fetch 100 条公开网页分层标准答案评测集",
        "tool": "web_fetch",
        "description": "9 条核心网页能力题、79 条 WCXB 跨站正文题和 12 条 lightmount-dark 渲染题。主集只含网页 URL；PDF、DOCX、TXT、图片和视频在独立能力集评测。",
        "ground_truth": {
            "annotator": "核心题由 Codex 对照源页标注；WCXB 导入固定提交的人审标准答案；lightmount-dark 种子增加稳定事实组",
            "verified_at": "2026-09-01",
            "policy": "先冻结答案再评分；供应商输出不能定义答案。live URL 漂移单独标记，不算供应商内容错误。",
            "review_status": "Codex 已完成结构、来源和事实组一致性复核；对外发布前仍建议第二人逐案例确认 live 页面漂移",
        },
        "sources": [
            {"name": "本项目核心网页题", "count": 9, "role": "静态页、长文、技术文档、开发者页面、列表、JavaScript、表格与重定向"},
            {"name": "WCXB", "count": 79, "url": "https://github.com/Murrough-Foley/web-content-extraction-benchmark", "commit": WCXB_COMMIT, "license": "CC-BY-4.0", "role": "七种页面结构、跨域名正文完整性与样板噪声"},
            {"name": "lightmount-dark render-quality", "count": 12, "commit": LIGHTMOUNT_COMMIT, "role": "真实浏览器渲染与文档模板回归"},
        ],
        "strata": [
            {"name": "协议与核心能力", "count": 9},
            {"name": "跨站正文模板", "count": 79},
            {"name": "浏览器渲染回归", "count": 12},
        ],
        "policy": {
            "login_required": False,
            "anti_bot_targeted": False,
            "webpage_only": True,
            "content_validation": "显著标记、最小长度、全部事实组和反向噪声四道确定性规则；HIL 复核误判与页面漂移。",
        },
        "cases": cases,
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output} with {len(cases)} webpage cases")


if __name__ == "__main__":
    main()
