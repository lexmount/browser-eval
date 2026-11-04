"""Read-only acceptance of a deployed frozen Map, run from this repository."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from harness.web_fetch.safety import unlinked_path, write_new_file


def verify_map_endpoint(base: str) -> dict:
    base = base.rstrip("/")
    checks = []

    def get(path: str) -> bytes:
        with urlopen(base + path, timeout=120) as response:
            if response.status != 200:
                raise AssertionError(f"HTTP {response.status}: {path}")
            return response.read()

    for path in ("/", "/login", "/api/capabilities"):
        if not get(path):
            raise ValueError(path)
        checks.append({"path": path, "status": "pass"})
    query = urlencode({"q": "Carnegie Mellon University", "format": "jsonv2", "addressdetails": 1})
    places = json.loads(get("/nominatim/search?" + query))
    cmu = next(
        (place for place in places if place.get("address", {}).get("postcode") == "15213"), None
    )
    if cmu is None:
        raise ValueError("CMU search result missing from the frozen Map snapshot")
    if "Carnegie Mellon" not in cmu["display_name"]:
        raise ValueError("Frozen Map acceptance failed")
    checks.append({"feature": "search", "result": cmu, "status": "pass"})
    reverse = json.loads(
        get(
            "/nominatim/reverse?"
            + urlencode(
                {"lat": cmu["lat"], "lon": cmu["lon"], "format": "jsonv2", "addressdetails": 1}
            )
        )
    )
    if not (reverse.get("address") and "error" not in reverse):
        raise ValueError("Frozen Map acceptance failed")
    checks.append({"feature": "reverse", "result": reverse, "status": "pass"})
    coordinates = f"{cmu['lon']},{cmu['lat']};-79.9608,40.4444"
    for profile in ("car", "bike", "foot"):
        route = json.loads(
            get(f"/osrm/routed-{profile}/route/v1/driving/{coordinates}?overview=false")
        )
        if not (route["code"] == "Ok" and route["routes"][0]["distance"] > 0):
            raise ValueError("Frozen Map acceptance failed")
        checks.append({"feature": f"route-{profile}", "result": route, "status": "pass"})
    tile = get("/tile/0/0/0.png")
    if not (tile.startswith(b"\x89PNG\r\n\x1a\n") and len(tile) > 100):
        raise ValueError("Frozen Map acceptance failed")
    checks.append({"feature": "tile", "bytes": len(tile), "status": "pass"})
    for path, name in (
        ("/node/1479729158", "Bei Jing"),
        ("/way/172206707", "Chatham University"),
        ("/relation/2279034", "Carnegie Mellon University"),
    ):
        detail = get(path).decode("utf-8")
        if "<!doctype html>" not in detail.lower():
            raise ValueError("Frozen Map acceptance failed")
        if not (name in detail and path.rsplit("/", 1)[1] in detail):
            raise ValueError("Frozen Map acceptance failed")
        if not "Not Found" not in detail:
            raise ValueError("Frozen Map acceptance failed")
        checks.append({"feature": "object-detail", "path": path, "status": "pass"})
    causeway = get("/relation/5475586").decode("utf-8")
    if not ("Lake Pontchartrain Causeway" in causeway and "5475586" in causeway):
        raise ValueError("Frozen Map acceptance failed")
    checks.append(
        {"feature": "frozen-route-relation", "path": "/relation/5475586", "status": "pass"}
    )
    try:
        get("/relation/999999999999")
    except HTTPError as error:
        if not error.code == 404:
            raise ValueError("Frozen Map acceptance failed") from error
    else:
        raise ValueError("An unknown frozen object must not be fabricated")
    checks.append({"feature": "unavailable-object", "status": "pass"})
    return {"base_url": base, "status": "pass", "checks": checks}


def map_check_main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only frozen Map deployment acceptance.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    output = unlinked_path(args.output)
    if output.exists():
        raise ValueError("Map acceptance output already exists")
    result = verify_map_endpoint(args.url)
    write_new_file(output, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({"status": result["status"], "checks": len(result["checks"])}))
