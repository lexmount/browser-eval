"""Reader-visible object details from the frozen map's real object IDs."""

import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from harness.environments.webarena.map_detail import DetailHandler, lookup, render


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
        server = ThreadingHTTPServer(("127.0.0.1", 0), DetailHandler)
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
        server = ThreadingHTTPServer(("127.0.0.1", 0), DetailHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with pytest.raises(HTTPError) as error:
                urlopen(f"http://127.0.0.1:{server.server_port}/relation/5475586")
            assert error.value.code == 404
        finally:
            server.shutdown()
            server.server_close()


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
        assert lookup("node", "7")["osm_type"] == "node"
        assert lookup("relation", "7") is None
