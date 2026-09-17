import copy
import hashlib
import zipfile

import pytest

from harness.browser_use.wire_witness import (
    fill_missing_referers,
    parse_access_log,
    validate_reconciled_har,
    validate_wire_source,
)

ORIGIN = "http://192.168.5.2:9999"
TARGET = "/f/space/subscribe.json"
REFERER = ORIGIN + "/f/space/69581/the-moon-saturn-and-jupiter-through-my-4-telescope-and"


def access_line(*, target=TARGET, referer=REFERER, status=200):
    return (
        '172.17.0.1 - - [17/Sep/2026:11:20:00 +0000] '
        f'"POST {target} HTTP/1.1" {status} 19 "{referer}" "private-user-agent" "-"\n'
    ).encode()


def har(*, url=ORIGIN + TARGET, headers=None, status=200):
    return {
        "log": {
            "entries": [
                {
                    "request": {"method": "POST", "url": url, "headers": headers or []},
                    "response": {"status": status},
                }
            ]
        }
    }


def test_real_site_shape_recovers_exact_on_wire_header_without_mutating_input():
    original = har()
    witnesses = parse_access_log(access_line(), origin=ORIGIN)

    corrected, additions = fill_missing_referers(original, witnesses)

    assert original["log"]["entries"][0]["request"]["headers"] == []
    assert corrected["log"]["entries"][0]["request"]["headers"] == [
        {"name": "Referer", "value": REFERER}
    ]
    assert len(additions) == 1
    assert REFERER not in str(additions)
    assert "private-user-agent" not in str(additions)
    assert validate_reconciled_har(
        original, corrected, {"unique_header_additions": additions}
    )
    forged = copy.deepcopy(corrected)
    forged["log"]["entries"][0]["response"]["status"] = 201
    assert not validate_reconciled_har(
        original, forged, {"unique_header_additions": additions}
    )


@pytest.mark.parametrize(
    "segment, record",
    [
        (access_line(referer="-"), har()),
        (access_line(status=302), har()),
        (access_line(target="/other"), har()),
        (access_line() * 2, har()),
        (access_line(), har(url="http://other.test" + TARGET)),
        (access_line(), {"log": {"entries": har()["log"]["entries"] * 2}}),
    ],
)
def test_missing_ambiguous_or_different_wire_events_never_supply_header(segment, record):
    witnesses = parse_access_log(segment, origin=ORIGIN)
    original = copy.deepcopy(record)
    corrected, additions = fill_missing_referers(record, witnesses)
    assert corrected == original
    assert additions == []


def test_existing_client_header_is_never_overwritten():
    original = har(headers=[{"name": "referer", "value": "http://example.test/source"}])
    corrected, additions = fill_missing_referers(
        original, parse_access_log(access_line(), origin=ORIGIN)
    )
    assert corrected == original
    assert additions == []


def test_invalid_log_or_origin_cannot_create_a_witness():
    assert parse_access_log(b'POST /f/space/subscribe.json 200 "secret"\n', origin=ORIGIN) == []
    with pytest.raises(ValueError, match="absolute HTTP URL"):
        parse_access_log(access_line(), origin="/relative")


def test_private_server_log_must_reproduce_every_added_header(tmp_path):
    raw = har()
    line = access_line()
    evaluated, additions = fill_missing_referers(raw, parse_access_log(line, origin=ORIGIN))
    source = tmp_path / "network-wire-source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("0-reddit.log", line)
    receipt = {
        "sources": [{
            "archive_member": "0-reddit.log",
            "origin": ORIGIN,
            "segment_sha256": hashlib.sha256(line).hexdigest(),
            "segment_bytes": len(line),
            "parsed_request_count": 1,
        }],
        "unique_header_additions": additions,
    }
    assert validate_wire_source(raw, evaluated, receipt, source)

    forged = copy.deepcopy(evaluated)
    forged["log"]["entries"][0]["request"]["headers"][-1]["value"] = ORIGIN + "/wrong"
    assert validate_reconciled_har(
        raw,
        forged,
        {"unique_header_additions": [{
            **additions[0],
            "referer_sha256": hashlib.sha256((ORIGIN + "/wrong").encode()).hexdigest(),
        }]},
    )
    assert not validate_wire_source(raw, forged, receipt, source)
    receipt["sources"] = []
    assert not validate_wire_source(raw, evaluated, receipt, source)
