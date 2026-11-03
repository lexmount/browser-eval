"""Reader-visible object details from the frozen map's real object IDs."""

import json
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

from harness.environments.webarena import map_detail
from harness.environments.webarena.map_detail import (
    FROZEN_OBJECTS,
    DetailServer,
    lookup,
    render,
)

NOMINATIM = "http://127.0.0.1:1"


@pytest.mark.parametrize("kind,identifier,name", [
    ("node", "1479729158", "Bei Jing"),
    ("way", "172206707", "Chatham University"),
    ("relation", "2279034", "Carnegie Mellon University"),
])
def test_search_result_detail_opens_under_its_original_map_url(kind, identifier, name):
    place = {"osm_type": kind, "osm_id": int(identifier),
             "display_name": f"{name}, Pittsburgh, United States", "class": "amenity",
             "type": "university", "lat": "40.44", "lon": "-79.94",
             "address": {"city": "Pittsburgh"}, "extratags": {"website": "https://example.org"}}
    with patch("harness.environments.webarena.map_detail.lookup", return_value=place):
        server = DetailServer(("127.0.0.1", 0), NOMINATIM)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_port}/{kind}/{identifier}") as response:
                page = response.read().decode()
                assert response.status == 200
                assert response.headers["Content-Type"].startswith("text/html")
            with urlopen(
                f"http://127.0.0.1:{server.server_port}/{kind}/{identifier}?xhr=1"
            ) as response:
                fragment = response.read().decode()
            assert name in page and identifier in page and "Pittsburgh" in page
            assert "← OpenStreetMap" in page and "Not Found" not in page
            assert name in fragment and "<!doctype html>" not in fragment.lower()
        finally:
            server.shutdown()
            server.server_close()


def test_missing_object_stays_missing_and_details_escape_untrusted_tags():
    page = render("node", "1", {"display_name": "<script>alert(1)</script>",
                                 "extratags": {"note": "<img src=x>"}}).decode()
    assert "<script>" not in page and "<img src=x>" not in page
    assert "&lt;script&gt;" in page and "&lt;img src=x&gt;" in page
    with patch("harness.environments.webarena.map_detail.lookup", return_value=None):
        server = DetailServer(("127.0.0.1", 0), NOMINATIM)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with pytest.raises(HTTPError) as error:
                urlopen(f"http://127.0.0.1:{server.server_port}/relation/999999999999")
            assert error.value.code == 404
        finally:
            server.shutdown()
            server.server_close()


def test_mounted_script_runs_alone_on_the_ports_it_is_given():
    """The upstream Map container has neither this package nor the topology registry."""

    class Nominatim(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps([{"osm_type": "node", "osm_id": 7,
                                "display_name": "Passed Port, Earth"}]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    nominatim = ThreadingHTTPServer(("127.0.0.1", 0), Nominatim)
    threading.Thread(target=nominatim.serve_forever, daemon=True).start()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    script = Path(map_detail.__file__)
    service = subprocess.Popen(
        [sys.executable, "-I", str(script), "--port", str(port),
         "--nominatim-port", str(nominatim.server_port)],
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{port}/node/7", timeout=2) as response:
                    assert b"Passed Port" in response.read()
                break
            except URLError:
                assert service.poll() is None and time.monotonic() < deadline
                time.sleep(0.05)
    finally:
        service.terminate()
        service.wait(timeout=10)
        nominatim.shutdown()
        nominatim.server_close()


def test_lookup_requires_exact_type_and_id():
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return json.dumps([{"osm_type": "way", "osm_id": 7},
                               {"osm_type": "node", "osm_id": 7}]).encode()

    with patch("harness.environments.webarena.map_detail.urlopen", return_value=Response()):
        assert lookup("node", "7", NOMINATIM)["osm_type"] == "node"
        assert lookup("relation", "7", NOMINATIM) is None


def test_frozen_route_relation_uses_the_bound_real_osm_identity():
    place = lookup("relation", "5475586", NOMINATIM)
    assert place is FROZEN_OBJECTS[("relation", "5475586")]
    assert place["display_name"].startswith("Lake Pontchartrain Causeway")
    assert place["lat"] == "30.199722222222"
    assert place["lon"] == "-90.122777777778"
    assert place["extratags"]["wikidata"] == "Q1132510"
    assert "5475586" in render("relation", "5475586", place).decode()
