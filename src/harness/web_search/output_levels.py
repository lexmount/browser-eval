"""Bind native output roles and source evidence to the shared level projections."""

import json
from copy import deepcopy

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from harness.record_io import bytes_sha256, json_bytes
from harness.web_search.adapters import adapter_for
from harness.web_search.core import verify_bundle
from lexbench_webfetch.reference_quality import blocked_page_hits
from lexbench_websearch.outputs import project_rows, qualify_field
from lexbench_websearch.scoring import canonical_url


def output_profile(profile):
    value = deepcopy(profile)
    for method in value["methods"]:
        method["output_fields"] = adapter_for(method["adapter"]).field_map(method)
    return value


def load_admissions(path):
    """Read an explicitly selected source bundle with complete artifact hashes."""
    from pathlib import Path

    path = Path(path)
    bundle = json.loads(path.read_bytes())
    if bundle.get("schema") != "web-search-field-admissions/1":
        raise ValueError("Unsupported field admission bundle")
    bindings = list(bundle["receipts"].values()) + bundle["artifacts"]
    verify_bundle(path.resolve().parent, bindings)
    artifacts = {
        binding["path"]: (path.parent / binding["path"]).read_bytes()
        for binding in bundle["artifacts"]
    }
    receipts = {
        mid: json.loads((path.parent / binding["path"]).read_bytes())
        for mid, binding in bundle["receipts"].items()
    }
    if any(receipt.get("plan", {}).get("method") != mid for mid, receipt in receipts.items()):
        raise ValueError("Field admission method binding mismatch")
    return receipts, artifacts


def field_sample_plan(profile, rows, method_id):
    """Fix the URL order before selecting fifty independently accessible pages."""
    selected = next(m for m in output_profile(profile)["methods"] if m["id"] == method_id)
    fields = selected["output_fields"]["extra_text"]
    if not fields:
        raise ValueError("This method declares no extra original text")
    candidates = {}
    for row in rows:
        if row["method"] != method_id:
            continue
        for item in row["results"][: profile["protocol"]["top_k"]]:
            text = "\n\n".join(item.get(field) or "" for field in fields)
            if item.get("invalid_slot") or not text.strip():
                continue
            url = canonical_url(item["url"], policy="document-url/2")
            sample = dict(
                url=item["url"], text=text, row_id=row["id"], native_evidence=row["evidence"]
            )
            key = bytes_sha256(json.dumps(sample, sort_keys=True, ensure_ascii=False).encode())
            if url not in candidates or key < candidates[url][0]:
                candidates[url] = key, sample
    ordered = sorted(candidates, key=lambda url: bytes_sha256(url.encode()))
    if len(ordered) < 50:
        raise ValueError("Field admission requires fifty distinct delivered URLs")
    return dict(
        schema="web-search-field-sample/2",
        method=method_id,
        adapter=selected["adapter"],
        output_options=selected.get("output_options") or {},
        fields=fields,
        sample_size=50,
        candidate_limit=200,
        candidates=[candidates[url][1] for url in ordered[:200]],
    )


def _bound(artifacts, binding):
    raw = artifacts[binding["path"]]
    if bytes_sha256(raw) != binding["sha256"]:
        raise ValueError("Field admission source artifact hash mismatch")
    return raw


def source_page_text(page, artifacts):
    """Return independently captured page text, without seeing a provider excerpt."""
    raw = _bound(artifacts, page["evidence"])
    capture = json.loads(_bound(artifacts, page["capture"]))
    request = capture.get("request", {})
    if (
        capture.get("sha256") != page["evidence"]["sha256"]
        or request.get("maxAgeHours") != 0
        or page["url"] not in request.get("urls", [])
        or not capture.get("completed_at")
    ):
        raise ValueError("Field admission requires a bound live-capture attempt")
    if capture.get("http_status") != 200 or capture.get("transport_exit"):
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    statuses = [v for v in payload.get("statuses", []) if v.get("id") == page["url"]]
    content = [v for v in payload.get("results", []) if v.get("id") == page["url"]]
    if (
        len(statuses) != 1
        or statuses[0].get("status") != "success"
        or statuses[0].get("source") != "crawled"
        or len(content) != 1
        or not isinstance(content[0].get("text"), str)
        or not content[0]["text"].strip()
        or blocked_page_hits(content[0]["text"])
    ):
        return None
    soup = BeautifulSoup(MarkdownIt("default").render(content[0]["text"]), "html.parser")
    for node in soup.select("script,style"):
        node.decompose()
    return soup.get_text(" ", strip=True) or None


def verify_field_admission(profile, rows, receipt, artifacts):
    """Recompute admission from retained live-capture bytes, never a saved flag."""
    if receipt.get("schema") != "web-search-field-admission/2":
        raise ValueError("Unsupported field admission receipt")
    expected = field_sample_plan(profile, rows, receipt["plan"]["method"])
    if receipt["plan"] != expected:
        raise ValueError("Field admission sample differs from native outputs")
    pages = receipt.get("pages", [])
    indexed = {page["url"]: page for page in pages}
    if len(indexed) != len(pages) or not set(indexed).issubset(
        {s["url"] for s in expected["candidates"]}
    ):
        raise ValueError(
            "Field admission pages must be unique and belong to the fixed candidate pool"
        )
    samples = []
    examined = 0
    for sample in expected["candidates"]:
        if len(samples) == expected["sample_size"]:
            break
        if sample["url"] not in indexed:
            raise ValueError("Missing capture evidence before the fifty-page sample is complete")
        examined += 1
        page = indexed[sample["url"]]
        text = source_page_text(page, artifacts)
        if text is None:
            continue
        samples.append(
            dict(
                url=sample["url"],
                text=sample["text"],
                page_text=text,
                observation_sha256=page["evidence"]["sha256"],
            )
        )
    if len(samples) != expected["sample_size"]:
        raise ValueError(
            "Field admission needs fifty accessible live pages; unavailable sources are not text mismatches"
        )
    return dict(
        fields=expected["fields"],
        examined_urls=examined,
        unavailable_source_pages=examined - len(samples),
        **qualify_field(samples),
    )


def level_inputs(profile, rows, level, *, admissions=None, artifacts=None):
    """Derive participation from adapters and verified field evidence."""
    if profile.get('protocol', {}).get('top_k') != 5:
        raise ValueError('Output-level assessments require top_k=5')
    declared = output_profile(profile)
    qualified = None
    if level == "l2":
        qualified = {}
        for method in declared["methods"]:
            if not method["output_fields"]["extra_text"]:
                continue
            receipt = (admissions or {}).get(method["id"])
            if receipt is None:
                raise ValueError("Missing field admission receipt: " + method["id"])
            qualified[method["id"]] = verify_field_admission(
                profile, rows, receipt, artifacts or {}
            )
    return project_rows(declared, rows, level, qualified_fields=qualified)


def assessed_levels(profile, rows, *, relevance=None, relevance_revisions=None,
                    answers=None, upstream_source=None, admissions=None, artifacts=None,
                    authority=None, timing_rows=None):
    """Build isolated, fully verified score views for every delivered native level."""
    from harness.web_search.answers import verify_answer_evidence
    from harness.web_search.relevance import verify_protocol, verify_provenance
    from lexbench_websearch.outputs import levels
    from lexbench_websearch.relevance import score

    declared = output_profile(profile)
    available = {level for method in declared['methods']
                 for level in levels(method['output_fields'])}
    projected_levels = {level: level_inputs(profile, rows, level, admissions=admissions, artifacts=artifacts)
                        for level in available}
    projected_levels = {level: value for level, value in projected_levels.items() if value[0]['methods']}
    available = set(projected_levels)
    if set(relevance or {}) != available.intersection({'l1', 'l2'}):
        raise ValueError('Every supported result level requires its own complete relevance pool')
    if ('l3' in available) != (answers is not None):
        raise ValueError('Generated-answer evidence must match declared L3 participation')
    judges = [evidence['judge'] for evidence in (relevance or {}).values()]
    model_names = {judge['name'] for judge in judges if judge.get('kind') == 'model'}
    if len(model_names) > 1 or len({judge.get('kind') for judge in judges}) > 1:
        raise ValueError('Output levels require one shared judge configuration')
    if answers is not None and model_names != {answers['configuration']['model']}:
        raise ValueError('Answer and relevance levels require the same judge model')
    views = {}
    for level in ('l1', 'l2', 'l3'):
        if level not in available:
            continue
        selected, projected = projected_levels[level]
        participants = {method['id'] for method in selected['methods']}
        view = dict(profile=selected, rows=projected,
                    timing_rows=[row for row in (rows if timing_rows is None else timing_rows)
                                 if row['method'] in participants])
        if level in ('l1', 'l2'):
            evidence = relevance[level]
            if evidence.get('evaluation_level') != level:
                raise ValueError('Relevance evidence belongs to another output level')
            verify_provenance(selected, projected, evidence, authority=authority)
            verify_protocol(selected, evidence, (relevance_revisions or {}).get(level),
                            authority=authority)
            view.update(relevance=evidence,
                        relevance_scores=score(selected, projected, evidence['plan'], evidence['labels']))
        else:
            if upstream_source is None:
                raise ValueError('L3 requires its pinned official grader source')
            view.update(answers=answers,
                        answer_scores=verify_answer_evidence(selected, projected, answers, upstream_source))
        view['benchmarks'] = level_benchmarks(view)
        views[level] = view
    return views


def level_benchmarks(view):
    """Keep relevance, source retrieval, extra-text cues and answer grades separate."""
    profile, rows = view['profile'], view['rows']
    level = profile['protocol']['relevance_level']
    excluded = set(profile.get('factual_quality_exclusions', []))
    cases = {c['id']: c for c in profile['cases']}
    scenario_scores = {(s['case_id'], s['method']): s for s in view.get('relevance_scores', [])}
    answer_scores = {(s['case_id'], s['method']): s for s in view.get('answer_scores', [])}
    if level == 'l3':
        definitions = dict(scenario=[('answer_grade', 'Answer usefulness / 3')],
                           factual=[('correct', 'SimpleQA accuracy')])
    else:
        definitions = dict(scenario=[('ndcg', 'nDCG@5'), ('precision', 'Precision@5'),
                                     ('success', 'Success@5')],
                           factual=([('source_rr', 'MRR@5'), ('source_hit', 'Source Hit@5'),
                                     ('answer_match', 'Answer Match@5')]
                                    if level == 'l1' else [('answer_match', 'Extra-text Answer Match@5')]))
    output = {}
    for benchmark in ('scenario', 'factual'):
        timing_ids = [cid for cid, case in cases.items()
                      if ('reference_answer' in case) == (benchmark == 'factual')]
        quality_ids = [cid for cid in timing_ids if cid not in excluded]
        if not timing_ids:
            continue
        scored = []
        for row in rows:
            cid, mid = row['case_id'], row['method']
            if cid not in quality_ids:
                continue
            values = dict(case_id=cid, method=mid,
                          context_characters=sum(len(item.get('snippet') or '') for item in row['results']))
            if level == 'l3':
                item = answer_scores[cid, mid]
                values.update({k: item[k] for k in ('citation_count', 'supported_citations', 'citations_in_results')})
                if benchmark == 'factual':
                    values.update(correct=int(item['factual_grade'] == 'CORRECT'),
                                  incorrect=int(item['factual_grade'] == 'INCORRECT'),
                                  not_attempted=int(item['factual_grade'] == 'NOT_ATTEMPTED'))
                else:
                    values['answer_grade'] = item['answer_grade']
            elif benchmark == 'scenario':
                values.update({k: scenario_scores[cid, mid][k] for k in ('ndcg', 'precision', 'success')})
            else:
                metrics = row['metrics']
                if level == 'l1':
                    values.update(source_rr=metrics['labeled_source_rr_5'], source_hit=metrics['labeled_source_hit_5'],
                                  answer_match=metrics['answer_in_preview_5'])
                else:
                    values['answer_match'] = metrics['answer_in_extra_text_5']
            scored.append(values)
        expected = {(cid, method['id']) for cid in quality_ids for method in profile['methods']}
        if {(s['case_id'], s['method']) for s in scored} != expected or len(scored) != len(expected):
            raise ValueError('An output level is missing scored benchmark cells')
        output[benchmark] = dict(case_ids=quality_ids, timing_case_ids=timing_ids,
                                 primary_metric=definitions[benchmark][0][0],
                                 metrics=[dict(key=key, label=label, direction='max')
                                          for key, label in definitions[benchmark]],
                                 scores=scored)
    return output


def load_assessed_levels(path, profile, rows, *, authority=None, timing_rows=None):
    """Resolve a selected external assessment bundle and its complete file inventory."""
    from pathlib import Path

    path = Path(path)
    root = path.parent.resolve()
    raw = path.read_bytes()
    verify_bundle(root, [{'path': path.name, 'sha256': bytes_sha256(raw)}])
    manifest = json.loads(raw)
    if manifest.get('schema') != 'web-search-level-assessments/1':
        raise ValueError('Unsupported output-level assessment bundle')
    bindings = list(manifest.get('relevance', {}).values())
    bindings += [manifest[key] for key in ('answers', 'upstream', 'field_admissions') if manifest.get(key)]
    files = {path.name: raw}

    def retain(base, entries):
        verify_bundle(base, entries)
        for binding in entries:
            target = base/binding['path']
            if not target.resolve().is_relative_to(root):
                raise ValueError('Output-level artifact escapes its assessment bundle')
            name = target.resolve().relative_to(root).as_posix()
            files[name] = target.read_bytes()

    retain(root, bindings)
    admissions, artifacts = {}, {}
    if manifest.get('field_admissions'):
        admission_path = root/manifest['field_admissions']['path']
        admission_manifest = json.loads(admission_path.read_bytes())
        admissions, artifacts = load_admissions(admission_path)
        retain(admission_path.parent, list(admission_manifest['receipts'].values()) + admission_manifest['artifacts'])
    evidence = {level: json.loads((root/binding['path']).read_bytes())
                for level, binding in manifest.get('relevance', {}).items()}
    answers = json.loads((root/manifest['answers']['path']).read_bytes()) if manifest.get('answers') else None
    upstream = (root/manifest['upstream']['path']).read_bytes() if manifest.get('upstream') else None
    views = assessed_levels(profile, rows, relevance=evidence,
                            relevance_revisions=manifest.get('relevance_revisions'),
                            answers=answers, upstream_source=upstream,
                            admissions=admissions, artifacts=artifacts, authority=authority,
                            timing_rows=timing_rows)
    return views, files


def export_level_bundle(path, destination):
    """Package only declared evidence under public names, preserving original source bytes."""
    from pathlib import Path

    from lexbench_webfetch.paths import contained_path

    path, destination = Path(path), Path(destination)
    source = json.loads(path.read_bytes())
    if source.get('schema') != 'web-search-level-assessments/1':
        raise ValueError('Unsupported output-level assessment bundle')
    if not isinstance(source.get('relevance', {}), dict) or not set(source.get('relevance', {})).issubset({'l1', 'l2'}):
        raise ValueError('Unsupported public relevance level')
    root = path.parent
    verify_bundle(root, [{'path': path.name, 'sha256': bytes_sha256(path.read_bytes())}])
    destination.mkdir(parents=True, exist_ok=True)

    def write(name, raw):
        target = contained_path(destination, name, reject_symlinks=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists() and target.read_bytes() != raw:
            raise ValueError('Public assessment destination already contains different bytes')
        target.write_bytes(raw)
        return dict(path=name, sha256=bytes_sha256(raw))

    public = dict(source, relevance={})
    bindings = list(source.get('relevance', {}).values())
    bindings += [source[key] for key in ('answers', 'upstream', 'field_admissions') if source.get(key)]
    verify_bundle(root, bindings)
    for level, binding in source.get('relevance', {}).items():
        public['relevance'][level] = write('judgments/' + level + '.json', (root/binding['path']).read_bytes())
    for key, name in [('answers', 'judgments/answers.json'), ('upstream', 'references/simpleqa_eval.py')]:
        if source.get(key):
            public[key] = write(name, (root/source[key]['path']).read_bytes())
    if source.get('field_admissions'):
        admission_path = root/source['field_admissions']['path']
        receipts, artifacts = load_admissions(admission_path)
        mapped = {name: write('sources/' + bytes_sha256(raw) + '.json', raw) for name, raw in artifacts.items()}
        public_receipts = {}
        for method, receipt in receipts.items():
            value = deepcopy(receipt)
            for page in value['pages']:
                for key in ('capture', 'evidence'):
                    page[key] = mapped[page[key]['path']]
            raw = json_bytes(value)
            public_receipts[method] = write('fields/' + bytes_sha256(raw) + '.json', raw)
        fields = dict(schema='web-search-field-admissions/1', receipts=public_receipts,
                      artifacts=list({item['path']: item for item in mapped.values()}.values()))
        public['field_admissions'] = write('field-admissions.json', json_bytes(fields))
    return write('assessments.json', json_bytes(public))
