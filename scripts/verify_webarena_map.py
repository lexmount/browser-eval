#!/usr/bin/env python3
"""Read-only Map deployment acceptance: search, reverse lookup, routing and tiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen


def verify(base: str) -> dict:
    base = base.rstrip("/")
    checks = []

    def get(path: str) -> bytes:
        with urlopen(base + path, timeout=120) as response:
            if response.status != 200:
                raise AssertionError(f"HTTP {response.status}: {path}")
            return response.read()

    for path in ("/", "/login", "/api/capabilities"):
        assert get(path), path
        checks.append({"path": path, "status": "pass"})
    query = urlencode({"q": "Carnegie Mellon University", "format": "jsonv2", "addressdetails": 1})
    places = json.loads(get("/nominatim/search?" + query))
    cmu = next(
        (place for place in places if place.get("address", {}).get("postcode") == "15213"),
        None,
    )
    assert cmu is not None, "CMU search result missing from the frozen Map snapshot"
    assert "Carnegie Mellon" in cmu["display_name"]
    checks.append({"feature": "search", "result": cmu, "status": "pass"})
    reverse = json.loads(
        get(
            "/nominatim/reverse?"
            + urlencode(
                {
                    "lat": cmu["lat"],
                    "lon": cmu["lon"],
                    "format": "jsonv2",
                    "addressdetails": 1,
                }
            )
        )
    )
    assert reverse.get("address") and "error" not in reverse
    checks.append({"feature": "reverse", "result": reverse, "status": "pass"})
    coordinates = f"{cmu['lon']},{cmu['lat']};-79.9608,40.4444"
    for profile in ("car", "bike", "foot"):
        route = json.loads(
            get(f"/osrm/routed-{profile}/route/v1/driving/{coordinates}?overview=false")
        )
        assert route["code"] == "Ok" and route["routes"][0]["distance"] > 0
        checks.append({"feature": f"route-{profile}", "result": route, "status": "pass"})
    tile = get("/tile/0/0/0.png")
    assert tile.startswith(b"\x89PNG\r\n\x1a\n") and len(tile) > 100
    checks.append({"feature": "tile", "bytes": len(tile), "status": "pass"})
    for path, name in (("/node/1479729158", "Bei Jing"),
                       ("/way/172206707", "Chatham University"),
                       ("/relation/2279034", "Carnegie Mellon University")):
        detail = get(path).decode("utf-8")
        assert "<!doctype html>" in detail.lower()
        assert name in detail and path.rsplit("/", 1)[1] in detail
        assert "Not Found" not in detail
        checks.append({"feature": "object-detail", "path": path, "status": "pass"})
    causeway = get("/relation/5475586").decode("utf-8")
    assert "Lake Pontchartrain Causeway" in causeway and "5475586" in causeway
    checks.append({
        "feature": "frozen-route-relation",
        "path": "/relation/5475586",
        "status": "pass",
    })
    try:
        get("/relation/999999999999")
    except HTTPError as error:
        assert error.code == 404
    else:
        raise AssertionError("An unknown frozen object must not be fabricated")
    checks.append({"feature": "unavailable-object", "status": "pass"})
    return {"base_url": base, "status": "pass", "checks": checks}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "checks": len(result["checks"])}))
