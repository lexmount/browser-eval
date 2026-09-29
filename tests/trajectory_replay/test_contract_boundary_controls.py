"""Counterexamples for the frozen task corrections, using the real evaluator."""

import json
import hashlib
from pathlib import Path
from urllib.parse import urlencode

import pytest
from webarena_verified.types.task import WebArenaSite

from harness.browser_use.runner import _configure_benchmark
from harness.trajectory_replay.remote_runtime import remote_benchmark
from harness.trajectory_replay.webarena_corrections import apply_correction, evaluate_corrected_contract, load_corrections


ARCHIVED = Path("evaluation/trajectory-replay/webarena-verified/revisions/task-corrections-20260918.json")
SHOP = "http://host.docker.internal:7770"
ADMIN = "http://host.docker.internal:7780/admin"
MAP = "http://host.docker.internal:3000"
WIKIPEDIA = "http://host.docker.internal:8888"
PRODUCT = "Atlantic Technology FS-7.0-GLB 7-channel Surround Bar (Gloss Black) (Discontinued by Manufacturer)"
RESPONSE = {"task_type": "MUTATE", "status": "SUCCESS", "retrieved_data": None}


def entry(url, *, data=None, body=None, status=200):
    request = {"method": "POST" if data is not None else "GET", "url": url,
               "headers": [{"name": "Accept", "value": "text/html"}]}
    if data is not None:
        request["postData"] = {"mimeType": "application/x-www-form-urlencoded", "text": urlencode(data)}
    return {"request": request, "response": {"status": status, "headers": [], "cookies": [],
            "content": {"mimeType": "application/json" if body is not None else "text/html",
                        "text": json.dumps(body) if body is not None else ""}}}


def scores(tmp_path, task_id, entries, catalog_path=ARCHIVED):
    benchmark = (
        remote_benchmark({
            WebArenaSite.SHOPPING: SHOP,
            WebArenaSite.SHOPPING_ADMIN: ADMIN,
            WebArenaSite.REDDIT: "http://host.docker.internal:9999",
            WebArenaSite.GITLAB: "http://host.docker.internal:8023",
            WebArenaSite.MAP: MAP,
            WebArenaSite.WIKIPEDIA: WIKIPEDIA,
        })
        if task_id in {429, 430}
        else _configure_benchmark()
    )
    task = benchmark.get_task(task_id)
    catalog = load_corrections(
        catalog_path,
        require_current_upstream=Path(catalog_path) != ARCHIVED,
    )
    path = tmp_path / f"synthetic-{task_id}.har"
    path.write_text(json.dumps({"log": {"version": "1.2", "creator": {"name": "counterexample", "version": "1"},
                                        "entries": entries}}))
    response = (
        {"task_type": "NAVIGATE", "status": "SUCCESS", "retrieved_data": None}
        if task_id in {429, 430}
        else RESPONSE
    )
    official = benchmark.evaluate_task(
        task_id=task_id, agent_response=response, network_trace=path
    ).score
    if task_id not in catalog.records:
        return (official, official)
    corrected = apply_correction(task, catalog)
    return (
        official,
        evaluate_corrected_contract(
            benchmark, corrected, agent_response=response, network_trace=path
        ).score,
    )



@pytest.mark.parametrize("rows,expected", [
    ([
        {"newProduct": 1, "attributes": "Size: S, Color: Blue"},
        {"newProduct": 1, "attributes": "Size: M, Color: Blue"},
    ], 1),
    ([{"newProduct": 1, "attributes": "Size: S, Color: Blue"}], 0),
    ([
        {"newProduct": 1, "attributes": "Size: S, Color: Blue"},
        {"newProduct": 1, "attributes": "Size: L, Color: Blue"},
    ], 0),
    ([
        {"newProduct": 0, "attributes": "Size: S, Color: Blue"},
        {"newProduct": 1, "attributes": "Size: M, Color: Blue"},
    ], 0),
], ids=["tr-548-accept-existing-blue-small-medium-variations",
        "tr-548-reject-missing-medium-blue",
        "tr-548-reject-wrong-size-blue",
        "tr-548-reject-non-new-small-blue"])
def test_current_548_reuses_existing_blue_and_preserves_seeded_name(tmp_path, rows, expected):
    values = {
        "product[name]": "Frankie  Sweatshirt",
        "configurable-matrix-serialized": json.dumps(rows),
    }
    events = [entry(
        ADMIN + "/catalog/product/save/id/110/type/configurable/store/0/set/9/back/edit",
        data=values,
        status=302,
    )]
    assert scores(
        tmp_path,
        548,
        events,
        Path("evaluation/trajectory-replay/webarena-verified/task-corrections.json"),
    ) == (expected, expected)


@pytest.mark.parametrize("task_id,new_label,attribute_id,rows,expected", [
    (549, "XXXL", "144", [{"newProduct": 1, "attributes": "Size: XXXL, Color: Green"}], 1),
    (549, "XXL", "144", [{"newProduct": 1, "attributes": "Size: XXXL, Color: Green"}], 0),
    (549, "XXXL", "93", [{"newProduct": 1, "attributes": "Size: XXXL, Color: Green"}], 0),
    (549, "XXXL", "144", [{"newProduct": 1, "attributes": "Size: XXXL, Color: Blue"}], 0),
    (550, "XXS", "144", [
        {"newProduct": 1, "attributes": "Size: XXS, Color: Blue"},
        {"newProduct": 1, "attributes": "Size: XXS, Color: Purple"},
    ], 1),
    (550, "XS", "144", [
        {"newProduct": 1, "attributes": "Size: XXS, Color: Blue"},
        {"newProduct": 1, "attributes": "Size: XXS, Color: Purple"},
    ], 0),
    (550, "XXS", "93", [
        {"newProduct": 1, "attributes": "Size: XXS, Color: Blue"},
        {"newProduct": 1, "attributes": "Size: XXS, Color: Purple"},
    ], 0),
    (550, "XXS", "144", [
        {"newProduct": 1, "attributes": "Size: XXS, Color: Blue"},
    ], 0),
], ids=[
    "tr-549-accept-native-create-options-and-required-variations",
    "tr-549-reject-wrong-new-size", "tr-549-reject-wrong-attribute",
    "tr-549-reject-wrong-variation",
    "tr-550-accept-native-create-options-and-required-variations",
    "tr-550-reject-wrong-new-size", "tr-550-reject-wrong-attribute",
    "tr-550-reject-incomplete-variations",
])
def test_current_configurable_size_tasks_use_native_create_options(
    tmp_path, task_id, new_label, attribute_id, rows, expected
):
    product_id, name = {
        549: (1492, "Minerva LumaTech&trade; V-Tee"),
        550: (1732, "Nona Fitness Tank"),
    }[task_id]
    events = [
        entry(
            ADMIN + "/catalog/product_attribute/createOptions/?isAjax=true",
            data={
                "options[0][label]": new_label,
                "options[0][attribute_id]": attribute_id,
                "options[0][is_new]": "true",
            },
            status=200,
        ),
        entry(
            ADMIN + f"/catalog/product/save/id/{product_id}/type/configurable/store/0/set/9/back/edit",
            data={
                "product[name]": name,
                "configurable-matrix-serialized": json.dumps(rows),
            },
            status=302,
        ),
    ]
    assert scores(
        tmp_path,
        task_id,
        events,
        Path("evaluation/trajectory-replay/webarena-verified/task-corrections.json"),
    ) == (expected, expected)


@pytest.mark.parametrize("status,search,excluded,expected", [
    ("1", "hollister", "false", 1),
    ("0", "hollister", "false", 0),
    ("1", "abercrombie", "false", 0),
    ("1", "hollister", "true", 0),
], ids=[
    "tr-423-accept-filtered-enable-all-hollister",
    "tr-423-reject-disable-hollister",
    "tr-423-reject-wrong-brand-filter",
    "tr-423-reject-excluded-filtered-records",
])
def test_current_423_uses_native_filtered_mass_status(
    tmp_path, status, search, excluded, expected
):
    events = [entry(
        ADMIN + f"/catalog/product/massStatus/status/{status}/",
        data={
            "excluded": excluded,
            "filters[placeholder]": "true",
            "search": search,
            "namespace": "product_listing",
        },
        status=302,
    )]
    assert scores(
        tmp_path,
        423,
        events,
        Path("evaluation/trajectory-replay/webarena-verified/task-corrections.json"),
    ) == (expected, expected)


@pytest.mark.parametrize("task_id,paths,expected", [
    (429, ["/way/583390395", "/way/172206707"], 1),
    (429, ["/relation/583390395", "/relation/172206707"], 0),
    (430, ["/way/583390395"], 1),
    (430, ["/relation/583390395"], 0),
], ids=["tr-429-accept-live-way-identities",
        "tr-429-reject-relation-kind-for-way-identities",
        "tr-430-accept-live-washington-jefferson-way",
        "tr-430-reject-unresolved-placeholder"])
def test_current_college_map_corrections_use_live_object_kinds(tmp_path, task_id, paths, expected):
    events = [entry(WIKIPEDIA + "/wiki/The_Chair_(2021_TV_series)")] + [
        entry(MAP + path) for path in paths
    ]
    assert scores(
        tmp_path,
        task_id,
        events,
        Path("evaluation/trajectory-replay/webarena-verified/task-corrections.json"),
    ) == (0, expected)


def cart_entries(quantity=1, *, name=PRODUCT, include_totals=True):
    entries = [
        entry(SHOP + "/checkout/cart/add/uenc/aHR0cDovL2V4YW1wbGUudGVzdA==/product/75640", data={"product": "75640", "qty": quantity}),
        entry(SHOP + "/rest/default/V1/carts/mine/totals", body={"items_qty": quantity, "items": [{"name": name}]}),
        entry(SHOP + "/rest/default/V1/carts/mine/payment-information", data={"billingAddress": json.dumps({"customerId": "27"})}),
        entry(SHOP + "/checkout/onepage/success"),
    ]
    if not include_totals:
        del entries[1]
    return entries


@pytest.mark.parametrize("quantity,expected", [(1, (1, 1)), (2, (0, 1))])
def test_archived_510_removal_exposes_quantity_acceptance_gap(tmp_path, quantity, expected):
    assert scores(tmp_path, 510, cart_entries(quantity)) == expected


@pytest.mark.parametrize("values,expected", [
    ({"quantity": 1}, 1), ({"quantity": 2}, 0),
    ({"include_totals": False}, 0), ({"name": "Different product"}, 0),
], ids=["tr-510-quantity-one", "tr-510-reject-quantity-two",
        "tr-510-reject-missing-totals", "tr-510-reject-wrong-product"])
def test_current_510_restores_upstream_quantity_and_name(tmp_path, values, expected):
    current = Path("evaluation/trajectory-replay/webarena-verified/task-corrections.json")
    assert scores(tmp_path, 510, cart_entries(**values), current) == (expected, expected)


def test_510_revision_receipt_binds_rules_and_candidate():
    receipt = json.loads(Path('research/trajectory-replay-task510-quantity-revision.json').read_text())
    before = load_corrections(receipt['predecessor_catalog_path'], require_current_upstream=False)
    after = load_corrections(receipt['revised_catalog_path'], require_current_upstream=False)
    assert receipt['predecessor_correction_catalog_sha256'] == before.source_sha256
    assert receipt['predecessor_correction_record_sha256'] == before.record_sha256s[510]
    assert receipt['revised_correction_catalog_sha256'] == after.source_sha256
    assert receipt['revised_correction_record_sha256'] == after.record_sha256s[510]
    assert receipt['regression_ids'] == after.records[510]['regression_ids']
    candidate = receipt['trajectory_candidate']
    assert candidate['sha256'] == hashlib.sha256(Path(candidate['path']).read_bytes()).hexdigest()
    assert receipt['provider_call_count'] == 0


def test_assertion_audit_rejects_changes_outside_declared_step(tmp_path):
    from scripts.audit_replay_cart_assertion import bound_scripts
    dataset = tmp_path / 'dataset.json'
    candidate = tmp_path / 'candidate.json'
    calls = [{'action': 'evaluate', 'script': 'true'}, {'action': 'evaluate', 'script': '1'}]
    dataset.write_text(json.dumps({'trajectories': [{'task_id': 42, 'tool_calls': calls}]}))
    revised = json.loads(json.dumps(calls))
    revised[0]['script'] = 'false'
    payload = {'task_id': 42, 'tool_calls': revised, 'repair': {
        'changed_step': 1, 'predecessor_dataset_sha256': hashlib.sha256(dataset.read_bytes()).hexdigest()}}
    candidate.write_text(json.dumps(payload))
    assert bound_scripts(dataset, candidate) == ('true', 'false')
    revised[1]['script'] = '2'
    candidate.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='only its declared assertion'):
        bound_scripts(dataset, candidate)
