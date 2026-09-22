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


def test_site_gateway_alias_recovers_only_unique_matching_request(tmp_path):
    alias_url = "http://host.docker.internal:9999" + TARGET
    alias_referer = "http://host.docker.internal:9999/f/space/thread"
    line = access_line(referer=alias_referer)
    raw = har(url=alias_url)
    witnesses = parse_access_log(line, origin=ORIGIN)

    evaluated, additions = fill_missing_referers(raw, witnesses, gateway_host="192.168.5.2")

    assert evaluated["log"]["entries"][0]["request"]["headers"] == [
        {"name": "Referer", "value": alias_referer}
    ]
    assert additions[0]["url_sha256"] == hashlib.sha256(alias_url.encode()).hexdigest()
    assert validate_reconciled_har(raw, evaluated, {"unique_header_additions": additions})
    source = tmp_path / "network-wire-source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("0-reddit.log", line)
    receipt = {
        "schema": "lex_browser_use_server_wire_witness/2",
        "gateway_host": "192.168.5.2",
        "sources": [{
            "archive_member": "0-reddit.log",
            "origin": ORIGIN,
            "segment_sha256": hashlib.sha256(line).hexdigest(),
            "segment_bytes": len(line),
            "parsed_request_count": 1,
        }],
        "unique_header_additions": additions,
    }
    assert validate_wire_source(raw, evaluated, receipt, source, gateway_host="192.168.5.2")
    assert not validate_wire_source(raw, evaluated, receipt, source, gateway_host="192.168.5.3")


@pytest.mark.parametrize("scheme,port", [("http", 80), ("https", 443)])
@pytest.mark.parametrize(
    "alias_explicit,wire_explicit", [(False, False), (True, False), (False, True)]
)
def test_gateway_alias_matches_default_ports_without_weakening_uniqueness(
    scheme, port, alias_explicit, wire_explicit
):
    alias = f"{scheme}://host.docker.internal" + (f":{port}" if alias_explicit else "")
    origin = f"{scheme}://192.168.5.2" + (f":{port}" if wire_explicit else "")
    raw = har(url=alias + TARGET)
    witnesses = parse_access_log(access_line(), origin=origin)
    corrected, additions = fill_missing_referers(raw, witnesses, gateway_host="192.168.5.2")
    assert corrected["log"]["entries"][0]["request"]["headers"] == [
        {"name": "Referer", "value": REFERER}
    ]
    assert len(additions) == 1
    assert raw["log"]["entries"][0]["request"]["headers"] == []

    equivalent = parse_access_log(access_line(), origin=f"{scheme}://192.168.5.2:{port}")
    ambiguous, additions = fill_missing_referers(
        raw, witnesses + equivalent, gateway_host="192.168.5.2"
    )
    assert ambiguous == raw
    assert additions == []


@pytest.mark.parametrize("origin", ["http://192.168.5.2:81", "https://192.168.5.2:80"])
def test_default_port_alias_does_not_match_other_port_or_scheme(origin):
    raw = har(url="http://host.docker.internal" + TARGET)
    corrected, additions = fill_missing_referers(
        raw, parse_access_log(access_line(), origin=origin), gateway_host="192.168.5.2"
    )
    assert corrected == raw
    assert additions == []


def test_site_gateway_alias_refuses_ambiguous_or_foreign_matches():
    alias_url = "http://host.docker.internal:9999" + TARGET
    witnesses = parse_access_log(access_line(), origin=ORIGIN)
    for record, sources in (
        ({"log": {"entries": har(url=alias_url)["log"]["entries"] * 2}}, witnesses),
        (har(url=alias_url), witnesses * 2),
        (har(url=alias_url), parse_access_log(access_line(), origin="http://192.168.5.3:9999")),
        (har(url=alias_url), parse_access_log(access_line(), origin="http://8.8.8.8:9999")),
        (har(url="http://other.test:9999" + TARGET), witnesses),
        (har(url="http://host.docker.internal:9988" + TARGET), witnesses),
        (har(url=alias_url + "?changed=1"), witnesses),
    ):
        corrected, additions = fill_missing_referers(
            record, sources, gateway_host="192.168.5.2"
        )
        assert corrected == record
        assert additions == []


def test_site_gateway_alias_rejects_other_witness_regardless_of_order():
    alias_url = "http://host.docker.internal:9999" + TARGET
    gateway = parse_access_log(access_line(), origin=ORIGIN)
    for foreign_origin in ("http://8.8.8.8:9999", "http://192.168.5.3:9999"):
        foreign = parse_access_log(
            access_line(referer="http://other.test/source"), origin=foreign_origin
        )
        for witnesses in (foreign + gateway, gateway + foreign):
            corrected, additions = fill_missing_referers(
                har(url=alias_url), witnesses, gateway_host="192.168.5.2"
            )
            assert corrected["log"]["entries"][0]["request"]["headers"] == [
                {"name": "Referer", "value": REFERER}
            ]
            assert len(additions) == 1


def test_legacy_receipt_keeps_exact_matching_when_new_alias_would_match(tmp_path):
    alias_url = "http://host.docker.internal:9999" + TARGET
    line = access_line()
    raw = har(url=alias_url)
    source = tmp_path / "network-wire-source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("0-reddit.log", line)
    receipt = {
        "schema": "lex_browser_use_server_wire_witness/1",
        "sources": [{
            "archive_member": "0-reddit.log",
            "origin": ORIGIN,
            "segment_sha256": hashlib.sha256(line).hexdigest(),
            "segment_bytes": len(line),
            "parsed_request_count": 1,
        }],
        "unique_header_additions": [],
    }
    assert validate_wire_source(raw, raw, receipt, source, gateway_host="192.168.5.2")


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
    invalid = access_line().replace(b"private-user-agent", b"\xff")
    assert parse_access_log(invalid, origin=ORIGIN) == []
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


def test_empty_wire_source_never_passes_by_vacuous_equality(tmp_path):
    source = tmp_path / "empty.zip"
    with zipfile.ZipFile(source, "w"):
        pass
    raw = har()
    assert not validate_wire_source(
        raw, raw, {"sources": [], "unique_header_additions": []}, source
    )
