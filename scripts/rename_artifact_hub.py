"""Update one report's display title while preserving its share and versions."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.artifact_auth import load_token  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "receipt",
        type=Path,
        nargs="?",
        default=ROOT / "publish/artifact-hub-receipt.json",
    )
    parser.add_argument("--title", required=True)
    args = parser.parse_args()

    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    base = os.environ.get(
        "ARTIFACT_HUB_BASE_URL",
        receipt.get("base_url", "https://artifact-hub.app.lexmount.com"),
    ).rstrip("/")
    token = load_token(base_url=base)
    request = urllib.request.Request(
        base + "/api/sites/" + receipt["slug"],
        data=json.dumps({"title": args.title}).encode(),
        method="PATCH",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
        receipt["title"] = data.get("title", args.title)
        args.receipt.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.chmod(args.receipt, 0o600)
        print(
            json.dumps(
                {"status": response.status, "title": data.get("title")},
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
