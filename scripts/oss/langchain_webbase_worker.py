#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from importlib.metadata import version
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from harness.web_fetch.network_dns import public_dns_context, validate_dns_mode

os.environ.setdefault("USER_AGENT", "browser-harness-eval/1.0")

from langchain_community.document_loaders import WebBaseLoader

LIBRARY_VERSION = version("langchain-community")


for line in sys.stdin:
    if not line.strip():
        continue
    request = json.loads(line)
    dns_trace = []
    dns_mode = request.get("dns_mode", "system")
    try:
        loader = WebBaseLoader(
            web_paths=(request["url"],),
            requests_kwargs={"timeout": request["timeout_seconds"]},
            continue_on_failure=False,
            raise_for_status=True,
        )
        dns_mode = validate_dns_mode(request.get("dns_mode", "system"))
        with public_dns_context(dns_mode, request["timeout_seconds"]) as dns_trace:
            docs = loader.load()
        content = "\n\n".join(doc.page_content for doc in docs)
        metadata = docs[0].metadata if docs else {}
        output = {
            "ok": True,
            "raw_tool_response": [{"page_content": doc.page_content, "metadata": doc.metadata} for doc in docs],
            "content": content,
            "title": metadata.get("title"),
            "metadata": {
                "implementation": "langchain_community.WebBaseLoader",
                "library_version": LIBRARY_VERSION,
                "document_count": len(docs),
                "document_metadata": metadata,
                "timeout_seconds": request["timeout_seconds"],
                "dns_mode": dns_mode,
                "dns_resolutions": dns_trace,
            },
        }
    except Exception as error:
        output = {
            "ok": False,
            "content": repr(error),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "metadata": {"implementation": "langchain_community.WebBaseLoader",
                         "library_version": LIBRARY_VERSION, "dns_mode": dns_mode,
                         "dns_resolutions": dns_trace, "timeout_seconds": request["timeout_seconds"]},
        }
    print(json.dumps(output, ensure_ascii=False), flush=True)
