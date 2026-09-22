"""Counterexamples for the frozen task corrections, using the real evaluator."""

import json
import hashlib
from pathlib import Path
from urllib.parse import urlencode

import pytest

from harness.browser_use.runner import _configure_benchmark
from harness.trajectory_replay.webarena_corrections import apply_correction, evaluate_corrected_contract, load_corrections


ARCHIVED = Path("evaluation/trajectory-replay/webarena-verified/revisions/task-corrections-20260918.json")
SHOP = "http://host.docker.internal:7770"
ADMIN = "http://host.docker.internal:7780/admin"
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
    benchmark = _configure_benchmark()
    task = benchmark.get_task(task_id)
    corrected = apply_correction(task, load_corrections(catalog_path))
    path = tmp_path / f"synthetic-{task_id}.har"
    path.write_text(json.dumps({"log": {"version": "1.2", "creator": {"name": "counterexample", "version": "1"},
                                        "entries": entries}}))
    return (benchmark.evaluate_task(task_id=task_id, agent_response=RESPONSE, network_trace=path).score,
            evaluate_corrected_contract(benchmark, corrected, agent_response=RESPONSE, network_trace=path).score)


@pytest.mark.parametrize("attributes,new_product,name,expected", [
    ("Size: S, Color: Brown", 1, "Phoebe Zipper Sweatshirt", 1),
    ("Size: L, Color: Brown", 1, "Phoebe Zipper Sweatshirt", 0),
    ("Size: S, Color: Red", 1, "Phoebe Zipper Sweatshirt", 0),
    ("Size: S, Color: Brown", 0, "Phoebe Zipper Sweatshirt", 0),
    ("Size: S, Color: Brown", 1, "Different product", 0),
    ("", 1, "Phoebe Zipper Sweatshirt", 0),
], ids=["existing-brown-new-small-variation", "wrong-size", "wrong-color", "not-new", "wrong-product", "missing-attributes"])
def test_archived_547_keeps_required_variation_fields(tmp_path, attributes, new_product, name, expected):
    values = {"product[name]": name, "configurable-matrix-serialized": json.dumps([
        {"newProduct": new_product, "attributes": attributes}
    ])}
    entries = [entry(ADMIN + "/catalog/product/save/id/1130/type/configurable/store/0/set/4/back/edit",
                     data=values, status=302)]
    # No global attribute-save request: the accepted equivalent reuses Brown.
    assert scores(tmp_path, 547, entries) == (0, expected)


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
    before = load_corrections(receipt['predecessor_catalog_path'])
    after = load_corrections()
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
