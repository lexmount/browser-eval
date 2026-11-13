"""Rebuild the preregistered length-qualified SimpleQA retrieval population."""

import csv
import io
import re

from .inputs import digest


MIRROR_POLICY = 'reference-title-lead/1'
MIRROR_LEAD_CHARACTERS = 4000
MIRROR_MINIMUM_CHARACTERS = 200
MIRROR_OVERLAP = .8


def mirror_candidates(reference_url):
    """Derive possible copies from publisher references, never search results."""
    from urllib.parse import quote, unquote, urlsplit

    parsed = urlsplit(reference_url)
    if (parsed.scheme not in ('http', 'https') or parsed.hostname != 'en.wikipedia.org'
            or parsed.port not in (None, 80, 443) or not parsed.path.startswith('/wiki/')
            or parsed.query or parsed.username or parsed.password):
        return []
    title = unquote(parsed.path[len('/wiki/'):])
    namespace = title.partition(':')[0].replace('_',' ').casefold()
    if not title or ':' in title and namespace in {
        'special','file','image','media','help','category','wikipedia','wp','portal',
        'template','talk','user','user talk','draft','module','mediawiki',
    }:
        return []
    path = quote(title, safe='()_,:-')
    return ['https://www.wikiwand.com/en/articles/' + path,
            'https://wiki2.org/en/' + path,
            'https://en.wikipedia-on-ipfs.org/wiki/' + path + '.html']


def qualify_mirror(reference, candidate):
    """Return a proof summary only for independently observed same-page copies."""
    from .outputs import block_overlap, normalized_text

    if candidate.get('url') not in mirror_candidates(reference.get('url', '')):
        raise ValueError('Mirror candidates must derive from a publisher reference URL')
    for observation in (reference, candidate):
        if (observation.get('status') != 200 or not observation.get('verified_at')
                or not re.fullmatch('[0-9a-f]{64}', observation.get('body_sha256', ''))
                or not observation.get('title') or not observation.get('lead')):
            raise ValueError('Mirror verification requires complete successful page evidence')
    left, right = [normalized_text(o['lead'][:MIRROR_LEAD_CHARACTERS])
                   for o in (reference, candidate)]
    if min(len(left), len(right)) < MIRROR_MINIMUM_CHARACTERS:
        raise ValueError('Insufficient article lead text to verify a mirror')
    overlap = block_overlap(right, left)
    title_matches = normalized_text(reference['title']) == normalized_text(candidate['title'])
    return {'url': candidate['url'], 'reference_url': reference['url'],
            'method': MIRROR_POLICY, 'verified_at': candidate['verified_at'],
            'reference_observation_sha256': reference['body_sha256'],
            'observation_sha256': candidate['body_sha256'],
            'title_matches': title_matches, 'lead_overlap': overlap,
            'qualified': title_matches and overlap >= MIRROR_OVERLAP}


def extend_reference_urls(dataset, observations):
    """Add verified copies without altering publisher questions, answers or URLs."""
    from copy import deepcopy

    cases = dataset['cases']
    references = {url for case in cases for url in case['reference_urls']}
    candidates = {url for reference in references for url in mirror_candidates(reference)}
    indexed = {item['url']: item for item in observations}
    if len(indexed) != len(observations) or set(indexed) - references - candidates:
        raise ValueError('Reference observations must be unique and independently derived')
    qualified = {}
    for reference in sorted(references):
        if reference not in indexed:
            continue
        for candidate in mirror_candidates(reference):
            if candidate not in indexed:
                continue
            try:
                proof = qualify_mirror(indexed[reference], indexed[candidate])
            except ValueError:
                # Failed requests and short pages establish no equivalence.
                continue
            if proof['qualified']:
                qualified[reference, candidate] = proof
    extended = deepcopy(dataset)
    for case in extended['cases']:
        proofs = [qualified[reference, candidate] for reference in case['reference_urls']
                  for candidate in mirror_candidates(reference) if (reference, candidate) in qualified]
        case['equivalent_reference_urls'] = list(dict.fromkeys(proof['url'] for proof in proofs))
        case['equivalent_reference_provenance'] = proofs
        case['url_equivalence'] = 'document-url/2'
    extended['reference_equivalence'] = dict(method=MIRROR_POLICY,
        minimum_lead_characters=MIRROR_MINIMUM_CHARACTERS,
        maximum_lead_characters=MIRROR_LEAD_CHARACTERS, minimum_block_overlap=MIRROR_OVERLAP,
        candidate_scope='Publisher reference URLs only', url_policy='document-url/2')
    return extended


def validate_reference_extensions(case):
    """Require provenance for every added URL before a dataset is accepted."""
    from datetime import datetime

    aliases = case.get('equivalent_reference_urls',[])
    proofs = case.get('equivalent_reference_provenance',[])
    if (not isinstance(aliases,list) or any(not isinstance(url,str) for url in aliases)
            or len(aliases) != len(set(aliases)) or not isinstance(proofs,list)
            or any(not isinstance(proof,dict) for proof in proofs)
            or {proof.get('url') for proof in proofs} != set(aliases)):
        raise ValueError('Equivalent reference URLs require complete unique provenance')
    for proof in proofs:
        reference = proof.get('reference_url')
        overlap = proof.get('lead_overlap')
        if (reference not in case.get('reference_urls',[]) or proof['url'] not in mirror_candidates(reference)
                or proof.get('method') != MIRROR_POLICY or proof.get('title_matches') is not True
                or proof.get('qualified') is not True or type(overlap) not in (float,int)
                or not MIRROR_OVERLAP <= overlap <= 1
                or any(not re.fullmatch('[0-9a-f]{64}',proof.get(field,'')) for field in
                       ('reference_observation_sha256','observation_sha256'))):
            raise ValueError('Invalid equivalent reference URL proof')
        try:
            observed = datetime.fromisoformat(proof['verified_at'].replace('Z','+00:00'))
        except (KeyError,TypeError,ValueError,AttributeError) as error:
            raise ValueError('Equivalent references require an observation date') from error
        if observed.tzinfo is None:
            raise ValueError('Equivalent reference observation dates require a time zone')


def source_manifest():
    from harness.benchmarks.data import definition_path
    import json

    return json.loads(definition_path("simpleqa-verified", "factual-source.json").read_bytes())


def _urls(value):
    urls = []
    for part in value.split(","):
        match = re.match(r"https?://\S+", part.strip())
        if not match:
            continue
        url = match.group(0)
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        if url not in urls:
            urls.append(url)
    return urls


def eligible_cases(csv_bytes, *, manifest=None):
    """Select by the pre-call shared input limit, retaining original CSV order."""
    manifest = manifest or source_manifest()
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
    if len({row["original_index"] for row in rows}) != len(rows):
        raise ValueError("Duplicate publisher question identity")
    return [
        {
            **{k: v for k, v in manifest["case"].items() if k != "id_prefix"},
            "id": manifest["case"]["id_prefix"] + row["original_index"],
            "query": row["problem"],
            "origin": manifest["url"],
            "reference_answer": row["answer"],
            "reference_urls": _urls(row["urls"]),
            "topic": row["topic"],
            "answer_type": row["answer_type"],
        }
        for row in rows
        if len(row["problem"]) <= manifest["selection"]["max_query_characters"]
    ]


def rebuild_dataset(csv_bytes, *, manifest=None):
    """Verify the pinned publisher input before rebuilding the declared population."""
    manifest = manifest or source_manifest()
    if digest(csv_bytes) != manifest["sha256"]:
        raise ValueError("Publisher CSV SHA256 does not match the frozen source")
    rows = list(csv.DictReader(io.StringIO(csv_bytes.decode("utf-8-sig"))))
    cases = eligible_cases(csv_bytes, manifest=manifest)
    cases = [{key: case[key] for key in manifest["case_field_order"]} for case in cases]
    if (
        len(rows) != manifest["selection"]["source_count"]
        or len(cases) != manifest["selection"]["selected_count"]
    ):
        raise ValueError("Frozen population differs from the pinned source selection")
    return {
        "schema": "lexbench-websearch-dataset/1",
        "id": manifest["dataset"],
        "cases": cases,
        "reference_checks": {},
        "reference_artifacts": {
            manifest["url"]: {
                "sha256": manifest["sha256"],
                "license": manifest["license"],
                "scope": manifest["scope"],
            }
        },
    }
