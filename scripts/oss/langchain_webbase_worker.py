#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from importlib.metadata import version

os.environ.setdefault("USER_AGENT", "browser-harness-eval/1.0")

from langchain_community.document_loaders import WebBaseLoader

LIBRARY_VERSION = version("langchain-community")


for line in sys.stdin:
    if not line.strip():
        continue
    request = json.loads(line)
    try:
        loader = WebBaseLoader(
            web_paths=(request["url"],),
            requests_kwargs={"timeout": request["timeout_seconds"]},
            continue_on_failure=False,
            raise_for_status=True,
        )
        docs = loader.load()
        content = "\n\n".join(doc.page_content for doc in docs)
        metadata = docs[0].metadata if docs else {}
        output = {
            "ok": True,
            "content": content,
            "final_url": metadata.get("source", request["url"]),
            "title": metadata.get("title"),
            "metadata": {
                "implementation": "langchain_community.WebBaseLoader",
                "library_version": LIBRARY_VERSION,
                "document_count": len(docs),
                "document_metadata": metadata,
            },
        }
    except Exception as error:
        output = {
            "ok": False,
            "content": repr(error),
            "final_url": request["url"],
            "error_type": type(error).__name__,
            "error_message": str(error),
            "metadata": {"implementation": "langchain_community.WebBaseLoader",
                         "library_version": LIBRARY_VERSION},
        }
    print(json.dumps(output, ensure_ascii=False), flush=True)
