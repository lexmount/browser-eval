#!/usr/bin/env python3
"""Build the committed 50-case live Web Fetch suite from pinned source sets."""

from __future__ import annotations

import json
import urllib.request
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WCXB_COMMIT = "c039d5ee9f5a3a984a0e167e63aacd04e76e78a9"
WCXB_IDS = (
    "0001", "0002", "0003", "0004",  # article
    "0172", "0360", "0403", "0501",  # forum
    "0185", "0580", "0581", "0582",  # product
    "0597", "0632", "0633", "0634",  # collection
    "0076", "0077", "0082", "0083",  # listing
    "0239", "0349", "0369", "0423",  # documentation
    "0021", "0025", "0028", "0029",  # service
)

LIGHTMOUNT_COMMIT = "228330684f3502fd573f98da08123e20090bdaae"
LIGHTMOUNT_CASES = (
    ("lm-feishu-help", "飞书管理后台帮助文档", "https://www.feishu.cn/hc/zh-CN/articles/485770964672-管理者查看企业-ai-功能使用数据", "documentation", ("企业 AI", "使用数据")),
    ("lm-python-tutorial", "Python 官方教程", "https://docs.python.org/3/tutorial/index.html", "documentation", ("Python Tutorial", "Python interpreter")),
    ("lm-nextjs-docs", "Next.js 文档", "https://nextjs.org/docs", "documentation", ("Next.js", "React")),
    ("lm-react-learn", "React 学习文档", "https://react.dev/learn", "documentation", ("React", "components")),
    ("lm-vite-guide", "Vite 入门指南", "https://vite.dev/guide/", "documentation", ("Vite", "build tool")),
    ("lm-node-intro", "Node.js 入门", "https://nodejs.org/en/learn/getting-started/introduction-to-nodejs", "documentation", ("Node.js", "JavaScript")),
    ("lm-axios-intro", "Axios 介绍", "https://axios-http.com/docs/intro", "documentation", ("Axios", "HTTP client")),
    ("lm-liaoxuefeng-python", "廖雪峰 Python 教程", "https://www.liaoxuefeng.com/wiki/1016959663602400/1017063413904832", "documentation", ("Python", "教程")),
    ("lm-pnpm-install", "pnpm 安装文档", "https://pnpm.io/installation", "documentation", ("pnpm", "Node.js")),
    ("lm-rust-learn", "Rust 学习入口", "https://www.rust-lang.org/learn", "service", ("Rust", "The Rust Programming Language")),
    ("lm-apple-iphone", "Apple iPhone 产品页", "https://www.apple.com/iphone/", "product", ("iPhone", "Apple")),
    ("lm-windows-api", "Windows API 索引", "https://learn.microsoft.com/en-us/windows/win32/apiindex/windows-api-list", "listing", ("Windows API", "application programming interface")),
)


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "browser-harness-eval-dataset-builder/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def wcxb_case(file_id: str) -> dict:
    url = f"https://raw.githubusercontent.com/Murrough-Foley/web-content-extraction-benchmark/{WCXB_COMMIT}/dev/ground-truth/{file_id}.json"
    source = fetch_json(url)
    truth = source["ground_truth"]
    page_type = source["_internal"]["page_type"]["primary"]
    required = list(truth.get("with") or [])[:3]
    forbidden = list(truth.get("without") or [])[:3]
    title = truth.get("title") or required[0]
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
        "description": f"WCXB 的 {page_type} 页面子集；同时检查正文召回与样板噪声排除。",
        "tags": ["wcxb", page_type, "live-url"],
        "ground_truth_evidence": [{"source": url, "supports": "WCXB CC-BY-4.0 人工复核标准答案"}],
        "annotation_notes": "从 WCXB 固定提交导入；原始 HTML 快照用于离线正文抽取，当前 harness 访问 live URL，因此页面漂移需在 HIL 中复核。",
        "task_prompt": f"读取给定 {page_type} 页面并返回主要内容。必须覆盖三个冻结正文片段，并排除导航、登录、版权或购买控件等样板噪声。",
        "reference_answer": truth.get("main_content") or "\n\n".join([title, *required]),
        "human_review_checklist": ["是否取得目标页面而非登录/错误页", "是否覆盖三个冻结正文片段", "是否排除列出的样板噪声", "若 live 页面已变化，是否标记为 ground-truth 漂移"],
        "source_benchmark": {"name": "WCXB", "file_id": file_id, "commit": WCXB_COMMIT, "license": "CC-BY-4.0"},
        "ground_truth_main_content": truth.get("main_content") or "",
    }


def lightmount_case(spec: tuple) -> dict:
    case_id, name, url, category, facts = spec
    return {
        "id": case_id,
        "name": name,
        "category": f"lightmount_render_{category}",
        "url": url,
        "expected_any": list(facts),
        "min_content_chars": 300,
        "required_facts": [{"id": f"marker-{i + 1}", "alternatives": [value]} for i, value in enumerate(facts)],
        "forbidden_any": ["Access denied", "CAPTCHA", "Page not found"],
        "description": "来自 lightmount-dark render-quality seed；用于暴露 hydration、运行时 API 与文档模板差异。",
        "tags": ["lightmount-dark", "render-quality", category],
        "ground_truth_evidence": [{"source": url, "supports": "页面主题与两个稳定正文标记"}],
        "annotation_notes": f"种子来源固定到 lightmount-dark {LIGHTMOUNT_COMMIT}；本项目增加正文事实组与 HIL，而不是沿用仅按可用内容分类的旧口径。",
        "task_prompt": "读取给定公开页面的主要可读正文；输出必须包含两个稳定主题标记，不能只是导航外壳、挑战页或错误页。",
        "reference_answer": f"{name}\n页面主要内容应同时包含：{facts[0]}；{facts[1]}。",
        "human_review_checklist": ["是否为目标页面而非错误页", f"是否包含 {facts[0]}", f"是否包含 {facts[1]}", "是否返回连贯正文而非导航外壳"],
        "source_benchmark": {"name": "lightmount-dark render-quality", "commit": LIGHTMOUNT_COMMIT},
    }


def main() -> None:
    core_path = ROOT / "datasets" / "public_webfetch.json"
    core = json.loads(core_path.read_text(encoding="utf-8"))
    cases = deepcopy(core["cases"])
    cases.extend(wcxb_case(file_id) for file_id in WCXB_IDS)
    cases.extend(lightmount_case(spec) for spec in LIGHTMOUNT_CASES)
    assert len(cases) == 50
    output = {
        "schema_version": 4,
        "id": "public-webfetch-v4.0-50-hil",
        "name": "公开网页 Web Fetch 50 条分层标准答案评测集",
        "tool": "web_fetch",
        "description": "10 条人工核心能力题 + 28 条 WCXB 七页面类型子集 + 12 条 lightmount-dark 渲染质量种子。所有案例都保留逐次输出和 HIL；live URL 漂移与供应商错误分开判定。",
        "ground_truth": {
            "annotator": "核心题由 Codex 对照源页标注；WCXB 子集导入其人工复核标准答案；lightmount-dark 种子新增最小事实组",
            "verified_at": "2026-09-01",
            "policy": "先冻结答案再评分；供应商输出不能定义答案。WCXB live URL 与固定快照不一致时标记 dataset_drift，不算供应商内容错误。",
            "review_status": "混合来源首轮标注完成；对外发布前仍需第二人逐条 HIL 确认"
        },
        "sources": [
            {"name": "本项目核心题", "count": 10, "role": "协议与能力边界主榜"},
            {"name": "WCXB", "count": 28, "url": "https://github.com/Murrough-Foley/web-content-extraction-benchmark", "commit": WCXB_COMMIT, "license": "CC-BY-4.0", "role": "七种页面结构的正文完整性与样板噪声"},
            {"name": "lightmount-dark render-quality", "count": 12, "commit": LIGHTMOUNT_COMMIT, "role": "浏览器渲染与文档模板回归"}
        ],
        "policy": {"login_required": False, "anti_bot_targeted": False, "content_validation": "主判定要求显著标记、长度、全部事实组和无反向噪声；HIL 可判定页面漂移。"},
        "cases": cases,
    }
    target = ROOT / "datasets" / "public_webfetch_50.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target} with {len(cases)} cases")


if __name__ == "__main__":
    main()
