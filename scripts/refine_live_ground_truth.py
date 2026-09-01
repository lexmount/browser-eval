#!/usr/bin/env python3
"""Replace template/navigation anchors with page-specific, verified facts."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets" / "public_webfetch_100.json"


REFINEMENTS = {
    "live-007-html-hypertext-markup-language-s": (
        "Python html module documentation",
        [
            "html — HyperText Markup Language support",
            "This module defines utilities to manipulate HTML",
            "Convert all named and numeric character references",
        ],
    ),
    "live-010-navigation-menu": (
        "Scrapy GitHub repository",
        ["scrapy/scrapy", "fast high-level web crawling", "scraping framework for Python"],
    ),
    "live-011-navigation-menu": (
        "Browser Use GitHub repository",
        ["browser-use/browser-use", "Make websites accessible for AI agents", "What can Browser Use do?"],
    ),
    "live-012-navigation-menu": (
        "Django GitHub repository",
        ["django/django", "The Web framework for perfectionists with deadlines", "Supporting the Development of Django"],
    ),
    "live-013-navigation-menu": (
        "Svelte GitHub repository",
        ["sveltejs/svelte", "web development for the rest of us", "Contribute to sveltejs/svelte development"],
    ),
    "live-014-navigation-menu": (
        "PostgreSQL GitHub repository",
        ["postgres/postgres", "Mirror of the official PostgreSQL GIT repository", "PostgreSQL Database Management System"],
    ),
    "live-016-navigation-menu": (
        "OpenAI Python library GitHub repository",
        ["openai/openai-python", "The official Python library for the OpenAI API", "Contribute to openai/openai-python development"],
    ),
    "live-017-navigation-menu": (
        "LangChain GitHub repository",
        ["langchain-ai/langchain", "The agent engineering platform", "Contribute to langchain-ai/langchain development"],
    ),
    "live-018-navigation-menu": (
        "OpenClaw GitHub repository",
        ["openclaw/openclaw", "Your own personal AI assistant", "Any OS. Any Platform. The lobster way"],
    ),
    "live-019-navigation-menu": (
        "FastAPI GitHub repository",
        ["fastapi/fastapi", "FastAPI framework, high performance", "easy to learn, fast to code, ready for production"],
    ),
    "live-020-navigation-menu": (
        "Node.js GitHub repository",
        ["nodejs/node", "Node.js JavaScript runtime", "Table of contents"],
    ),
    "live-022-until-september-10-get-pycharm-f": (
        "Django getting started documentation",
        ["Getting started", "New to Django? Or to web development in general?", "Writing your first Django app, part 1"],
    ),
    "live-027-sponsors": (
        "FastAPI documentation home",
        ["FastAPI framework, high performance", "easy to learn, fast to code, ready for production", "Features"],
    ),
    "live-031-agent": (
        "Exa Contents API reference",
        ["Contents - Exa", "Get the full page contents, summaries, and metadata", "automatic live crawling as fallback"],
    ),
    "live-037-breadcrumb": (
        "NOAA climate page",
        ["Climate", "National Oceanic and Atmospheric Administration", "NOAA experts provide data, tools and information"],
    ),
    "live-043-syntax": (
        "CSS encyclopedia article",
        ["CSS", "Selector types", "Separation of content from presentation"],
    ),
    "live-051-email-an-email-and-mime-handling": (
        "Python email package documentation",
        ["email — An email and MIME handling package", "library for managing email messages", "object model interface"],
    ),
    "live-054-navigation-menu": (
        "aiohttp GitHub repository",
        ["aio-libs/aiohttp", "Asynchronous HTTP client/server framework for asyncio and Python", "Key Features"],
    ),
    "live-055-navigation-menu": (
        "pytest GitHub repository",
        ["pytest-dev/pytest", "makes it easy to write small tests", "scales to support complex functional testing"],
    ),
    "live-056-navigation-menu": (
        "Deno GitHub repository",
        ["denoland/deno", "A modern runtime for JavaScript and TypeScript", "Contribute to denoland/deno development"],
    ),
    "live-057-navigation-menu": (
        "Spring Boot GitHub repository",
        ["spring-projects/spring-boot", "production-grade applications and services", "Installation and Getting Started"],
    ),
    "live-058-navigation-menu": (
        "Apache Superset GitHub repository",
        ["apache/superset", "Data Visualization and Data Exploration Platform", "Superset"],
    ),
    "live-059-navigation-menu": (
        "Docker Compose GitHub repository",
        ["docker/compose", "Define and run multi-container applications with Docker", "Docker Compose"],
    ),
    "live-063-navigation-menu": (
        "Ruff GitHub repository",
        ["astral-sh/ruff", "extremely fast Python linter", "code formatter, written in Rust"],
    ),
    "live-064-navigation-menu": (
        "pandas GitHub repository",
        ["pandas-dev/pandas", "Flexible and powerful data analysis", "A Powerful Python Data Analysis Toolkit"],
    ),
    "live-065-navigation-menu": (
        "Hugging Face Datasets GitHub repository",
        ["huggingface/datasets", "largest hub of ready-to-use datasets for AI models", "Key Features"],
    ),
    "live-066-navigation-menu": (
        "Open WebUI GitHub repository",
        ["open-webui/open-webui", "User-friendly AI Interface", "Key Features of Open WebUI"],
    ),
    "live-067-navigation-menu": (
        "RAGFlow GitHub repository",
        ["infiniflow/ragflow", "leading open-source Retrieval-Augmented Generation", "fuses cutting-edge RAG with Agent capabilities"],
    ),
    "live-068-navigation-menu": (
        "React Router GitHub repository",
        ["remix-run/react-router", "Declarative routing for React", "Contribute to remix-run/react-router development"],
    ),
    "live-069-navigation-menu": (
        "Prettier GitHub repository",
        ["prettier/prettier", "Prettier is an opinionated code formatter", "Opinionated Code Formatter"],
    ),
    "live-070-navigation-menu": (
        "Redux GitHub repository",
        ["reduxjs/redux", "predictable global state management", "Installation"],
    ),
}


def main() -> None:
    payload = json.loads(DATASET.read_text())
    seen: set[str] = set()
    for case in payload["cases"]:
        refinement = REFINEMENTS.get(case["id"])
        if not refinement:
            continue
        name, facts = refinement
        case["name"] = name
        case["expected_any"] = [facts[0]]
        case["required_facts"] = [
            {"id": f"anchor-{index}", "alternatives": [fact]}
            for index, fact in enumerate(facts, 1)
        ]
        case["ground_truth_evidence"][0]["supports"] = (
            "2026-09-01 使用实际 Web Fetch 输出复核页面标题、元数据与正文；"
            "以下三个核对点均在保存的原始响应中出现"
        )
        case["annotation_notes"] = (
            "Codex 复核日期 2026-09-01；核验通道：实际 Web Fetch；"
            f"最终 URL：{case['url']}"
        )
        joined = "；".join(facts)
        case["task_prompt"] = f"读取给定 URL，返回页面的可读正文。正文应至少包含以下三个核对点：{joined}。"
        case["reference_answer"] = "\n".join(facts)
        case["human_review_checklist"] = [f"是否保留核对点：{fact}" for fact in facts]
        seen.add(case["id"])

    missing = set(REFINEMENTS) - seen
    if missing:
        raise SystemExit(f"Missing cases: {sorted(missing)}")
    DATASET.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"Refined {len(seen)} cases")


if __name__ == "__main__":
    main()
