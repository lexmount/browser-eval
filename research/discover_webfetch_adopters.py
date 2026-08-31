#!/usr/bin/env python3
"""Discover high-star, recently active repositories with Web Fetch code signatures.

The search universe is deliberately explicit and saved in the output. GitHub code
search examines default branches; GraphQL then verifies the default branch HEAD
date instead of relying on the looser repository `pushed_at` field.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


SIGNATURES = {
    "firecrawl-v2": '"api.firecrawl.dev/v2/scrape"',
    "firecrawl-v1": '"api.firecrawl.dev/v1/scrape"',
    "firecrawl-sdk": '"FirecrawlApp"',
    "jina-reader-http": '"r.jina.ai/http"',
    "jina-reader-https": '"r.jina.ai/https"',
    "tavily-extract": '"api.tavily.com/extract"',
    "exa-contents": '"api.exa.ai/contents"',
    "browserbase-fetch": '"api.browserbase.com/v1/fetch"',
    "brightdata-request": '"api.brightdata.com/request"',
    "scrapingbee": '"app.scrapingbee.com/api/v1"',
    "zyte-extract": '"api.zyte.com/v1/extract"',
    "zenrows": '"api.zenrows.com/v1"',
    "scraperapi": '"api.scraperapi.com"',
    "scrapfly": '"api.scrapfly.io/scrape"',
    "oxylabs": '"realtime.oxylabs.io/v1/queries"',
    "generic-webfetch-class": '"class WebFetch"',
    "generic-web-fetch-tool": '"web_fetch" "tool"',
}


def gh(*args: str, retries: int = 3) -> Any:
    for attempt in range(retries + 1):
        process = subprocess.run(
            ["gh", "api", *args], text=True, capture_output=True
        )
        if process.returncode == 0:
            return json.loads(process.stdout)
        if attempt == retries:
            raise RuntimeError(process.stderr.strip() or "gh api failed")
        print("GitHub API retry", attempt + 1, process.stderr.strip()[:180], flush=True)
        time.sleep(15)
    raise AssertionError("unreachable")


def search_code(query: str, per_page: int) -> dict[str, Any]:
    return gh(
        "search/code", "-X", "GET", "-f", f"q={query}",
        "-f", f"per_page={per_page}",
    )


def metadata_batch(repos: list[str]) -> dict[str, dict[str, Any]]:
    fields = []
    aliases: dict[str, str] = {}
    for index, full_name in enumerate(repos):
        owner, name = full_name.split("/", 1)
        alias = f"r{index}"
        aliases[alias] = full_name
        fields.append(
            f'{alias}: repository(owner:{json.dumps(owner)}, name:{json.dumps(name)}) {{'
            " nameWithOwner url stargazerCount isFork isArchived description "
            " defaultBranchRef { name target { ... on Commit { oid committedDate } } } }"
        )
    query = "query {" + " ".join(fields) + "}"
    payload = gh("graphql", "-f", f"query={query}")
    return {
        aliases[alias]: value
        for alias, value in payload.get("data", {}).items()
        if value
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stars", type=int, default=1000)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--per-signature", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=args.days)
    hits: dict[str, list[dict[str, str]]] = defaultdict(list)
    totals: dict[str, int] = {}

    for label, query in SIGNATURES.items():
        result = search_code(query, args.per_signature)
        totals[label] = result.get("total_count", 0)
        for item in result.get("items", []):
            repo = item["repository"]["full_name"]
            hits[repo].append({
                "signature": label,
                "path": item["path"],
                "url": item["html_url"],
            })
        print(label, totals[label], len(result.get("items", [])), flush=True)
        time.sleep(7)

    metadata: dict[str, dict[str, Any]] = {}
    names = sorted(hits)
    for offset in range(0, len(names), 30):
        metadata.update(metadata_batch(names[offset:offset + 30]))
        print("metadata", min(offset + 30, len(names)), "/", len(names), flush=True)

    included = []
    for repo in names:
        meta = metadata.get(repo) or {}
        branch = meta.get("defaultBranchRef") or {}
        target = branch.get("target") or {}
        committed = target.get("committedDate")
        if not committed:
            continue
        commit_dt = dt.datetime.fromisoformat(committed.replace("Z", "+00:00"))
        if (
            meta.get("stargazerCount", 0) >= args.stars
            and commit_dt >= cutoff
            and not meta.get("isArchived")
        ):
            included.append({
                "repo": repo,
                "url": meta.get("url"),
                "stars": meta.get("stargazerCount"),
                "description": meta.get("description"),
                "is_fork": meta.get("isFork"),
                "default_branch": branch.get("name"),
                "default_branch_head_at": committed,
                "matches": hits[repo],
            })

    included.sort(key=lambda row: (-row["stars"], row["repo"].casefold()))
    output = {
        "generated_at": now.isoformat(),
        "cutoff": cutoff.isoformat(),
        "minimum_stars": args.stars,
        "method": (
            "GitHub REST code search on default branches; first N results per explicit "
            "signature, deduplicated by repository; GraphQL verification of stars and "
            "default-branch HEAD committedDate. This is exhaustive only within the "
            "listed signatures and per-signature result cap."
        ),
        "per_signature_cap": args.per_signature,
        "signatures": SIGNATURES,
        "search_total_counts": totals,
        "unique_repositories_examined": len(names),
        "included": included,
    }
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print("included", len(included), "wrote", args.output)


if __name__ == "__main__":
    main()
