"""Re-evaluate saved evidence with explicit external correction authority."""

import json
from copy import deepcopy
from pathlib import Path

from lexbench_websearch.answer_rules import POLICY_NAME, RULE_VERSION, reference_rule
from lexbench_websearch.contracts import validate_cases
from lexbench_websearch.inputs import digest
from lexbench_websearch.qualification import _equivalent, _qualified, validate_regressions
from lexbench_websearch.scoring import canonical_url, evaluate, task_acceptance


def _rescore(profile, rows):
    cases = {case["id"]: case for case in profile["cases"]}
    updated, changes = [], []
    for row in rows:
        new = {**row, "metrics": evaluate(cases[row["case_id"]], row["results"])}
        if new["metrics"] != row["metrics"]:
            changes.append({"id": row["id"], "before": row["metrics"], "after": new["metrics"]})
        updated.append(new)
    return profile, updated, changes


NATIVE_CONTROLS = Path(__file__).with_name("data") / "native-normalization-regressions.json"

def normalization_regressions(expected_sha256, normalizer):
    """Exercise frozen projection controls through the execution owner's adapter."""
    path = NATIVE_CONTROLS
    if digest(path.read_bytes()) != expected_sha256:
        raise ValueError("Normalization regression binding mismatch")
    if not callable(normalizer):
        raise ValueError("Native normalization requires the execution adapter")
    for group in json.loads(path.read_bytes())["regressions"]:
        for control in group["controls"]:
            try:
                results = normalizer(
                    control["payload"],
                    control["method"],
                    control["protocol"],
                    control["request"],
                )
            except ValueError:
                if control["error"]:
                    continue
                raise
            if (
                control["error"]
                or [r["url"] for r in results] != control["expected_urls"]
            ):
                raise ValueError("Normalization regression failed: " + control["id"])
            metrics = evaluate(control["case"], results)
            if any(metrics[k] != v for k, v in control["expected_metrics"].items()):
                raise ValueError("Task metric regression failed: " + control["id"])


def canonical_metadata(final_url, tags):
    """Read explicit publisher canonical links, without evaluating page content."""
    from html.parser import HTMLParser
    from urllib.parse import urljoin

    targets = set()
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        return targets

    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'link' and 'canonical' in (attrs.get('rel') or '').lower().split() and attrs.get('href'):
                targets.add(canonical_url(urljoin(final_url, attrs['href'])))

    parser = Links()
    for tag in tags:
        parser.feed(tag)
    return targets


def capture_source_identity(url, timeout=6):
    """Capture redirects and publisher canonical metadata as reference inputs."""
    import time
    import urllib.error
    import urllib.request
    from datetime import UTC, datetime
    from html.parser import HTMLParser
    from http.client import HTTPException

    canonical_url(url)
    chain, tags = [], []
    deadline = time.monotonic() + timeout

    class Redirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            canonical_url(newurl)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Source identity timeout')
            req.timeout = remaining
            chain.append(dict(url=req.full_url, status=code, location=newurl))
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == 'link' and 'canonical' in (dict(attrs).get('rel') or '').lower().split():
                tags.append(self.get_starttag_text())

    observed = dict(url=url, verified_at=datetime.now(UTC).isoformat(), chain=chain)
    try:
        opener = urllib.request.build_opener(Redirects())
        try:
            response = opener.open(urllib.request.Request(url, headers={'User-Agent': 'Webstone reference identity'}), timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            observed.update(status=response.status, final=response.url)
            if response.status == 200 and 'html' in response.headers.get('Content-Type', '').lower():
                prefix = response.read(262144).decode(response.headers.get_content_charset() or 'utf-8', errors='replace')
                parser = Links()
                parser.feed(prefix)
                targets = canonical_metadata(response.url, tags)
                if len(targets) == 1:
                    observed.update(canonical=next(iter(targets)), canonical_evidence=tags)
    except (OSError, ValueError, UnicodeError, HTTPException) as exc:
        observed['error'] = str(exc)
    return observed


def factual_source_identity(case, item):
    """Qualify a reference URI alias without judging page relevance or availability."""
    from urllib.parse import urlsplit

    try:
        alias, target = item['alias'], item['target']
        observed = item['observation']
        if (item['case_id'] != case['id'] or item['query'] != case['query']
                or 'reference_answer' not in case or target not in case.get('reference_urls', [])
                or observed.get('url') != alias or not observed.get('verified_at')):
            return False
        canonical_url(alias)
        wanted = canonical_url(target)
        current = alias
        for step in observed.get('chain', []):
            if step.get('url') != current or step.get('status') not in (301, 302, 303, 307, 308):
                return False
            current = step['location']
            if canonical_url(current) == wanted:
                return True
        # Same-publisher canonical metadata can identify query/path variants.
        final = canonical_url(observed.get('final', ''))
        canonical = observed.get('canonical')
        return bool(observed.get('status') == 200 and not observed.get('error')
                    and final == canonical_url(current)
                    and canonical and canonical_url(canonical) == wanted
                    and urlsplit(final).netloc == urlsplit(wanted).netloc
                    and canonical_metadata(observed['final'], observed.get('canonical_evidence', [])) == {wanted})
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def validate_revision_controls(revision, normalizer=None):
    from importlib.resources import files

    ids = revision.get('regression_ids', [])
    if not revision.get('root_cause') or not ids or len(ids) != len(set(ids)):
        raise ValueError('Correction requires cause and unique regression provenance')
    if revision.get('factual_source_identities') and 'verified-factual-source-identities' not in ids:
        raise ValueError('Factual source identities require their paired regression controls')
    registry = json.loads(files('harness.benchmarks').joinpath('scoring-regressions.json').read_bytes())
    controls = registry.get('web_search', {})
    selected = set(ids) & controls.keys()
    for identity in selected:
        group = controls[identity]
        if {item['expected'] for item in group} != {True, False}:
            raise ValueError('Source identity regressions require paired controls')
        for control in group:
            if control.get('kind') == 'factual_dataset':
                case, results = control['case'], control['results']
                row = dict(results=results, metrics=evaluate(case, results))
                if task_acceptance(case, row, 'answer') != control['expected']:
                    raise ValueError('Dataset contamination regression failed')
                continue
            if control.get('kind') == 'delivered_preview':
                from lexbench_websearch.relevance import candidate_packet, score

                profile, rows = control['profile'], control['rows']
                packet, _ = candidate_packet(profile, rows)
                candidates = packet['blind_candidate_packets'][0]['candidates']
                if len(candidates) != 2 or any(len(c['previews']) != 1 for c in candidates):
                    raise ValueError('Delivered preview isolation regression failed')
                labels = {profile['cases'][0]['id']: {
                    c['candidate_id']: control['grades'][c['previews'][0]['snippet']] for c in candidates}}
                scored = next(row for row in score(profile, rows, packet['plan'], labels) if row['method'] == control['method'])
                row = next(row for row in rows if row['method'] == control['method'])
                if task_acceptance(profile['cases'][0], row, relevance_grades=scored['ranked_grades']) != control['expected']:
                    raise ValueError('Delivered preview acceptance regression failed')
                continue
            case = deepcopy(control['case'])
            if factual_source_identity(case, control['identity']):
                case['reference_urls'].append(control['identity']['alias'])
            results = control['results']
            row = {'results': results, 'metrics': evaluate(case, results)}
            if (bool(row['metrics']['labeled_source_hit_5']) != control['expected']
                    or task_acceptance(case, row, metric='source') != control['expected']):
                raise ValueError('Source identity regression failed: ' + identity)
    if selected:
        revision = dict(revision, regression_ids=[item for item in ids if item not in selected])
        if not revision['regression_ids'] and not revision.get('normalization'):
            return
    if not revision.get("normalization"):
        validate_regressions(revision)
        return
    native = {"relative-serp-links", "unsafe-serp-links", "top-five-boundary"}
    if revision['normalization'] == 'request-url/2':
        native.add('invalid-result-slots')
        if not callable(normalizer):
            raise ValueError('Native normalization requires the execution adapter')
        for control in registry['web_search_normalization']['invalid-result-slots']:
            try:
                results = normalizer(control['payload'], control['method'], control['protocol'], control.get('request'))
            except ValueError:
                if control.get('error'):
                    continue
                raise
            if control.get('error'):
                raise ValueError('Error envelope regression failed')
            row = dict(results=results, metrics=evaluate(control['case'], results))
            if (row['metrics']['labeled_source_rr_5'] != control['expected_rr']
                    or task_acceptance(control['case'], row, 'source') != control['expected']):
                raise ValueError('Invalid result slot regression failed')
    ids = revision.get("regression_ids", [])
    if (
        revision["normalization"] not in ("request-url/1", "request-url/2")
        or not native.issubset(ids)
        or len(ids) != len(set(ids))
        or not revision.get("root_cause")
    ):
        raise ValueError(
            "Native normalization revision needs paired URL and cutoff controls"
        )
    shared = [i for i in ids if i not in native]
    if shared:
        validate_regressions(dict(revision, regression_ids=shared))
    normalization_regressions(revision.get("regressions_sha256"), normalizer)


def apply_report_revision(profile, rows, revision, *, artifacts=None, normalizer=None):
    """Add independently observed reference annotations to immutable saved calls."""
    if revision.get("schema") != "web-search-assessment-revision/1":
        raise ValueError("Unsupported assessment revision")
    validate_revision_controls(revision, normalizer)
    if revision.get("provider_calls") != 0:
        raise ValueError("An annotation revision cannot introduce provider calls")
    revised = deepcopy(profile)
    preview_rule = revision.get('relevance_input')
    if preview_rule:
        if preview_rule != 'delivered-previews/1' or 'delivered-preview-isolation' not in revision['regression_ids']:
            raise ValueError('Delivered preview correction requires paired controls')
        revised['protocol']['relevance_input'] = preview_rule
    contamination_rule = revision.get('factual_contamination_rule')
    if contamination_rule:
        from lexbench_websearch.evidence_quality import DATASET_ROWS_RULE

        if contamination_rule != DATASET_ROWS_RULE or 'structured-dataset-contamination' not in revision['regression_ids']:
            raise ValueError('Dataset contamination correction requires paired controls')
        for case in revised['cases']:
            if 'reference_answer' in case:
                case['factual_contamination_rule'] = contamination_rule
    answer_scope = revision.get('answer_evidence_scope')
    if answer_scope:
        if answer_scope != 'preview' or 'serp-answer-field-scope' not in revision['regression_ids']:
            raise ValueError('Answer field correction requires paired controls')
        for case in revised['cases']:
            if 'reference_answer' in case:
                case['answer_evidence_scope'] = answer_scope
    normalization = revision.get("normalization")
    if normalization:
        revised["protocol"]["result_url_resolution"] = normalization
    cases = {c["id"]: c for c in revised["cases"]}
    original_sources = {cid: deepcopy(case) for cid, case in cases.items()}
    identities = set()
    for item in revision.get('factual_source_identities', []):
        cid, alias = item.get('case_id'), item.get('alias')
        if ((cid, alias) in identities
                or not factual_source_identity(original_sources.get(cid, {}), item)):
            raise ValueError('Unqualified factual source identity')
        identities.add((cid, alias))
        if alias not in cases[cid]['reference_urls']:
            cases[cid]['reference_urls'].append(alias)
    seen = set()
    for item in revision.get("cases", []):
        cid = item["case_id"]
        if cid in seen or cid not in cases:
            raise ValueError("Duplicate or unknown annotated case")
        seen.add(cid)
        if cases[cid].get("anchors"):
            raise ValueError("Additional annotations cannot replace existing anchors")
        anchors = item.get("anchors", [])
        if not _qualified(item):
            raise ValueError("Unqualified source anchor")
        cases[cid]["anchors"] = anchors
        cases[cid]["reference_annotation"] = item
        if revised.get('protocol', {}).get('reference_scope') == 'identity':
            checks = revised.setdefault('reference_checks', {})
            for anchor in anchors:
                source = next(s for s in item['sources'] if anchor in (s['url'], s.get('final_url'), s.get('qualifying_link')))
                checks[anchor] = {'url': source['url'], 'contains': [source['title']]}
    if revision.get("answer_match_rule"):
        if revision["answer_match_rule"] != POLICY_NAME:
            raise ValueError("Unsupported answer matching rule")
        for case in cases.values():
            if case.get("reference_answer"):
                case["answer_match_rule"] = revision["answer_match_rule"]

    if revision.get("answer_annotation_rule") not in (None, RULE_VERSION):
        raise ValueError("Answer derivation revision uses an unsupported annotation rule")
    derivations = revision.get("answer_derivations", [])
    if derivations:

        if (revision.get("answer_annotation_rule") != RULE_VERSION
                or not {"published-answer-citation-metadata", "literal-bracketed-answer"}.issubset(revision["regression_ids"])):
            raise ValueError("Reference citation cleanup requires a bound scoring revision")
        seen = set()
        for item in derivations:
            case = cases.get(item["case_id"], {})
            metadata = {k: v for k, v in item.items() if k not in {"case_id", "query"}}
            if (item["case_id"] in seen or item.get("query") != case.get("query")
                    or item.get("reference") != case.get("reference_answer")
                    or case.get("answer_derivation", metadata) != metadata):
                raise ValueError("Answer derivation does not bind the unchanged case")
            reference_rule(case["reference_answer"], derivation=metadata)
            seen.add(item["case_id"])
            case["answer_derivation"] = deepcopy(metadata)
    exclusions = set(revised.get("factual_quality_exclusions", []))
    decisions = {}
    incoming = revision.get("reference_adjudications", [])
    for batch in (revised.get("reference_adjudications", []), incoming):
        seen = set()
        for item in batch:
            cid = item["case_id"]
            if cid in seen or cid in decisions and decisions[cid] != item:
                raise ValueError("Duplicate or conflicting reference adjudication")
            seen.add(cid)
            decisions[cid] = item
    for item in decisions.values():
        cid = item["case_id"]
        case = cases.get(cid, {})
        if (
            "reference_answer" not in case
            or item.get("query") != case.get("query")
            or item.get("reference_answer") != case["reference_answer"]
            or item.get("quality_excluded") is not True
            or not item.get("reason")
            or not item.get("evidence")
        ):
            raise ValueError(
                "Reference adjudication does not bind the unchanged factual case"
            )
        exclusions.add(cid)
    if exclusions:
        revised["factual_quality_exclusions"] = sorted(exclusions)
    if decisions:
        revised["reference_adjudications"] = [
            decisions[cid] for cid in sorted(decisions)
        ]
    validate_cases(revised["cases"], reference_checks=revised.get("reference_checks"), reference_scope=revised.get("protocol", {}).get("reference_scope"))
    updated, changes = [], []
    for row in rows:
        new = dict(row)
        if (
            normalization
            and row["http_status"] == 200
            and not row.get("transport_exit")
        ):
            path = row["evidence"]["path"]
            if (
                artifacts is None
                or path not in artifacts
                or digest(artifacts[path]) != row["evidence"]["sha256"]
            ):
                raise ValueError("Native artifact binding mismatch")
            try:
                method = next(m for m in revised["methods"] if m["id"] == row["method"])
                new["results"] = normalizer(
                    json.loads(artifacts[path]),
                    method,
                    revised["protocol"],
                    row.get("request"),
                )
                new["error"] = ""
            except (ValueError, TypeError, AttributeError, KeyError):
                # Preserve the original failed response and its unchanged evidence.
                new = dict(row)
        new["metrics"] = evaluate(cases[row["case_id"]], new["results"])
        if (
            new["metrics"] != row["metrics"]
            or new["results"] != row["results"]
            or new.get("error") != row.get("error")
        ):
            change = {
                "id": row["id"],
                "before": row["metrics"],
                "after": new["metrics"],
            }
            if normalization:
                change["normalization"] = {
                    "before_sha256": digest(
                        json.dumps(
                            row["results"], sort_keys=True, ensure_ascii=False
                        ).encode()
                    ),
                    "after_sha256": digest(
                        json.dumps(
                            new["results"], sort_keys=True, ensure_ascii=False
                        ).encode()
                    ),
                    "before_count": len(row["results"]),
                    "after_count": len(new["results"]),
                    "before_error": row.get("error"),
                    "after_error": new.get("error"),
                }
            changes.append(change)
        updated.append(new)
    return revised, updated, changes


def collected_projection(profile, rows, artifacts, normalizer):
    """Reconstruct collection projections through the frozen execution adapter."""
    cases = {c["id"]: c for c in profile["cases"]}
    methods = {m["id"]: m for m in profile["methods"]}
    restored = []
    for row in rows:
        original = dict(row, results=[])
        if row["http_status"] == 200 and not row.get("transport_exit"):
            path = row["evidence"]["path"]
            if (
                path not in artifacts
                or digest(artifacts[path]) != row["evidence"]["sha256"]
            ):
                raise ValueError("Native artifact binding mismatch")
            try:
                original["results"] = normalizer(
                    json.loads(artifacts[path]),
                    methods[row["method"]],
                    profile["protocol"],
                    row.get("request"),
                )
                original["error"] = ""
            except (ValueError, TypeError, AttributeError, KeyError) as exc:
                original["error"] = str(exc)
        original["metrics"] = evaluate(cases[row["case_id"]], original["results"])
        restored.append(original)
    return restored


def apply_identity_correction(profile, rows, correction):
    validate_regressions(correction)
    observations = {x["url"]: x for x in correction["observations"]}
    revised = deepcopy(profile)
    cases = {c["id"]: c for c in revised["cases"]}
    for cid, aliases in correction["aliases"].items():
        case = cases[cid]
        for alias, target in aliases.items():
            a, b = observations[alias], observations[target]
            if not _equivalent(a, b):
                raise ValueError("Unproven equivalent page")
            if target not in case.get("anchors", []):
                raise ValueError("Correction must retain the original known target")
        case["identity_aliases"] = dict(aliases)
    validate_cases(
        revised["cases"],
        reference_checks=revised.get("reference_checks"),
        reference_scope=revised.get("protocol", {}).get("reference_scope"),
    )
    return _rescore(revised, rows)

def validate_revision_binding(revision, *, authority=None, normalizer=None):
    registered = (authority or {}).get("assessment_revisions", {})
    if registered.get("schema") != "search-assessment-authority/1":
        raise ValueError("Assessment revision requires an explicit external scoring authority")
    identity = digest(json.dumps(revision, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    records = [p for p in registered.get("provenance", []) if p.get("sha256") == identity]
    if identity not in registered.get("approved_sha256", []) or len(records) != 1:
        raise ValueError("Assessment revision is not bound by the versioned scoring authority")
    for key in ("root_cause", "regression_ids", "provider_calls"):
        if records[0].get(key) != revision.get(key):
            raise ValueError("Assessment revision provenance mismatch")
    validate_revision_controls(revision, normalizer)
