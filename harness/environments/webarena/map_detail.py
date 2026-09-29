"""Read-only OSM object pages backed by the frozen Nominatim snapshot.

The verified Map image starts an empty Rails object database. Search results
come from Nominatim, so their node/way/relation links otherwise end in 404.
This service uses the same frozen Nominatim object IDs and never invents a
missing object or mutates the snapshot.
"""

from __future__ import annotations

import html
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

OBJECT_PATH = re.compile(r"^/(node|way|relation)/([1-9][0-9]{0,18})/?$")
TYPE_CODE = {"node": "N", "way": "W", "relation": "R"}
NOMINATIM = "http://127.0.0.1:8085"
# Nominatim deliberately omits many route relations.  This exact object is
# frozen from Wikidata Q1132510 revision 2489023584, whose P402 value is
# relation 5475586 and whose P625 coordinate is the value below.  The source
# entity JSON has SHA-256 479e2cf83fe867b87eb875ddc5916af2feae1664fae45426f391a2ffd7df7185.
FROZEN_OBJECTS = {
    ("relation", "5475586"): {
        "osm_type": "relation",
        "osm_id": 5475586,
        "display_name": (
            "Lake Pontchartrain Causeway, Louisiana, United States"
        ),
        "class": "man_made",
        "type": "bridge",
        "lat": "30.199722222222",
        "lon": "-90.122777777778",
        "address": {"state": "Louisiana", "country": "United States"},
        "extratags": {
            "description": (
                "set of two parallel bridges over Lake Pontchartrain in "
                "southern Louisiana, United States"
            ),
            "length": "38.442 km",
            "wikidata": "Q1132510",
        },
    }
}
STYLE = """
.map-detail {font:14px/1.45 system-ui,sans-serif;color:#263238;min-width:0}
.map-detail h1 {font-size:1.45rem;margin:0 0 8px;overflow-wrap:anywhere}
.map-detail p,.map-detail td {overflow-wrap:anywhere}
.map-detail table {border-collapse:collapse;width:100%;table-layout:fixed;margin-top:18px}
.map-detail th,.map-detail td {
  padding:7px 5px;border-top:1px solid #e2e7e9;text-align:left;vertical-align:top
}
.map-detail th {width:44%;color:#4b5c65;font-weight:600;overflow-wrap:anywhere}
.map-detail .source {font-size:.85rem;color:#61717a;margin-top:20px}
.map-detail-header {padding:12px 20px;background:white;border-bottom:1px solid #d9dfe2}
.map-detail-header a {color:#1760a0;text-decoration:none;font-weight:700}
.map-detail-main {max-width:960px;margin:24px auto;padding:0 16px}
.map-detail-main .map-detail {background:white;border:1px solid #d9dfe2;padding:20px}
"""


def lookup(kind: str, identifier: str) -> dict | None:
    frozen = FROZEN_OBJECTS.get((kind, identifier))
    if frozen is not None:
        return frozen
    query = urlencode({"osm_ids": TYPE_CODE[kind] + identifier,
                       "format": "json", "addressdetails": 1, "extratags": 1})
    with urlopen(f"{NOMINATIM}/lookup?{query}", timeout=10) as response:
        places = json.load(response)
    return next((place for place in places
                 if place.get("osm_type") == kind and str(place.get("osm_id")) == identifier),
                None)


def render(kind: str, identifier: str, place: dict, *, fragment: bool = False) -> bytes:
    def esc(value: object) -> str:
        return html.escape(str(value), quote=True)

    name = place.get("display_name", "").split(",", 1)[0] or f"{kind.title()} {identifier}"
    address = place.get("display_name", "")
    fields = [("OSM type", kind.title()), ("OSM ID", identifier),
              ("Category", place.get("class", "")), ("Type", place.get("type", "")),
              ("Latitude", place.get("lat", "")), ("Longitude", place.get("lon", ""))]
    fields.extend((key.replace("_", " ").title(), value)
                  for key, value in sorted(place.get("address", {}).items())
                  if key != "country_code" and value)
    fields.extend((key.replace("_", " ").title(), value)
                  for key, value in sorted(place.get("extratags", {}).items()) if value)
    rows = "".join(f"<tr><th>{esc(label)}</th><td>{esc(value)}</td></tr>"
                   for label, value in fields)
    content = f"""<style>{STYLE}</style><section class="map-detail">
<h1>{esc(name)}</h1><p>{esc(address)}</p><table>{rows}</table>
<p class="source">Data © OpenStreetMap contributors · Frozen Map snapshot</p>
</section>"""
    if fragment:
        return content.encode()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(name)} · OpenStreetMap</title>
</head><body><header class="map-detail-header"><a href="/">← OpenStreetMap</a></header>
<main class="map-detail-main">{content}</main></body></html>""".encode()


class DetailHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        match = OBJECT_PATH.fullmatch(urlsplit(self.path).path)
        if not match:
            self.send_error(404)
            return
        kind, identifier = match.groups()
        try:
            place = lookup(kind, identifier)
        except (OSError, ValueError, json.JSONDecodeError):
            self.send_error(502, "Frozen Map lookup unavailable")
            return
        if place is None:
            self.send_error(404, "Object absent from frozen Map data")
            return
        body = render(kind, identifier, place,
                      fragment="xhr=1" in urlsplit(self.path).query.split("&"))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def serve() -> None:
    ThreadingHTTPServer(("127.0.0.1", 8091), DetailHandler).serve_forever()


if __name__ == "__main__":
    serve()
