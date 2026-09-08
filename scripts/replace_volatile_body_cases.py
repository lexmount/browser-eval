#!/usr/bin/env python3
"""Replace reviewed volatile whole-page cases with immutable source revisions."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PENDING = "PENDING IMMUTABLE REFERENCE CAPTURE"

REPLACEMENTS = {
    "english-encyclopedia": {
        "id": "stable-wikipedia-ai-revision",
        "name": "Artificial intelligence · Wikipedia 固定修订版",
        "url": "https://en.wikipedia.org/w/index.php?title=Artificial_intelligence&oldid=1373671334",
        "facts": [["Artificial intelligence"], ["machine learning"], ["computer science"]],
        "immutability": {"kind": "wikipedia_revision", "revision": "1373671334"},
    },
    "lm-vite-guide": {
        "id": "stable-rfc-8259-json",
        "name": "RFC 8259 · JSON",
        "url": "https://www.rfc-editor.org/rfc/rfc8259.html",
        "facts": [["JavaScript Object Notation (JSON)"], ["A JSON text is a serialized value"], ["six structural characters"]],
        "immutability": {"kind": "rfc", "revision": "8259"},
    },
    "lm-pnpm-install": {
        "id": "stable-rfc-6455-websocket",
        "name": "RFC 6455 · The WebSocket Protocol",
        "url": "https://www.rfc-editor.org/rfc/rfc6455.html",
        "facts": [["The WebSocket Protocol"], ["Opening Handshake"], ["Data Framing"]],
        "immutability": {"kind": "rfc", "revision": "6455"},
    },
    "live-011-navigation-menu": {
        "id": "stable-browser-use-readme",
        "name": "browser-use/browser-use · 固定提交 README",
        "url": "https://github.com/browser-use/browser-use/blob/2b1f9d377999a59fe7627c1a5aa88c12aa42e11f/README.md",
        "facts": [["Browser Use lets an AI agent use a web browser"], ["Python library: the easiest way to automate the web"], ["Made with ❤️ in Zurich and San Francisco"]],
        "immutability": {"kind": "git_commit", "revision": "2b1f9d377999a59fe7627c1a5aa88c12aa42e11f"},
    },
    "live-018-navigation-menu": {
        "id": "stable-openclaw-readme",
        "name": "openclaw/openclaw · 固定提交 README",
        "url": "https://github.com/openclaw/openclaw/blob/d662d5753720e9e700fb49c1f76fb4bfc21c4c04/README.md",
        "facts": [["OpenClaw 🦞 — Your assistant, on your devices, in your"], ["before connecting other users or exposing the Gateway remotely."], ["for incorporated or adapted code."]],
        "immutability": {"kind": "git_commit", "revision": "d662d5753720e9e700fb49c1f76fb4bfc21c4c04"},
    },
    "live-020-navigation-menu": {
        "id": "stable-nodejs-readme",
        "name": "nodejs/node · 固定提交 README",
        "url": "https://github.com/nodejs/node/blob/2987a5965f1f5c6d9f0023406389c57002e63629/README.md",
        "facts": [["Node.js is an open-source, cross-platform JavaScript runtime environment."], ["You can get a trusted keyring from nodejs/release-keys, e.g. using"], ["additions comply with the project’s license guidelines."]],
        "immutability": {"kind": "git_commit", "revision": "2987a5965f1f5c6d9f0023406389c57002e63629"},
    },
    "live-023-guides": {
        "id": "stable-wcag21-recommendation",
        "name": "WCAG 2.1 · 2018 Recommendation",
        "url": "https://www.w3.org/TR/2018/REC-WCAG21-20180605/",
        "facts": [["Web Content Accessibility Guidelines (WCAG) 2.1"], ["Perceivable"], ["Success Criteria"]],
        "immutability": {"kind": "dated_w3c_recommendation", "revision": "2018-06-05"},
    },
    "live-031-agent": {
        "id": "stable-xml10-recommendation",
        "name": "XML 1.0 · Fifth Edition Recommendation",
        "url": "https://www.w3.org/TR/2008/REC-xml-20081126/",
        "facts": [["Extensible Markup Language (XML) 1.0 (Fifth Edition)"], ["Well-Formed XML Documents"], ["Logical Structures"]],
        "immutability": {"kind": "dated_w3c_recommendation", "revision": "2008-11-26"},
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/dataset.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Candidate output already exists")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    if {case["id"] for case in dataset["cases"]} & set(REPLACEMENTS) != set(REPLACEMENTS):
        raise ValueError("Volatile-case inventory changed")
    pending_hash = hashlib.sha256(PENDING.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    migrated = []
    for original in dataset["cases"]:
        spec = REPLACEMENTS.get(original["id"])
        if not spec:
            migrated.append(original)
            continue
        case = deepcopy(original)
        case.update(id=spec["id"], name=spec["name"], url=spec["url"],
                    required_facts=[{"id": f"stable-anchor-{index}", "alternatives": values}
                                    for index, values in enumerate(spec["facts"], 1)],
                    ground_truth_main_content=PENDING)
        # Presence opts the freeze step into generating deterministic regions
        # from the independently captured immutable full text.
        case["body_requirements"] = {}
        case["source_benchmark"] = {"immutable_source": spec["immutability"]}
        case["source_verification"] = {
            "state": "pending", "checked_at": now,
            "method": "Pending independent capture of the declared immutable source revision",
            "selector": original.get("source_verification", {}).get("selector") or "main",
            "reference_sha256": pending_hash,
            "note": "The URL embeds an immutable revision identifier; capture must verify it before measurement.",
        }
        migrated.append(case)
    dataset["cases"] = migrated
    dataset.setdefault("comparison", {})["immutable_body_case_policy"] = {
        "required_for_full_text_ranking": True,
        "allowed_sources": ["rfc", "dated_w3c_recommendation", "wikipedia_revision", "git_commit"],
        "volatile_pages": "structure_or_stable-contract tasks only",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
