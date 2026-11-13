"""Provider-independent URL, evidence and acceptance scoring."""

import math
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from .inputs import digest


def normalize_results(payload):
    if (
        not isinstance(payload, dict)
        or payload.get("error")
        or not isinstance(payload.get("results"), list)
    ):
        raise ValueError("Response must contain a results array without an error")
    rows = []
    for rank, item in enumerate(payload["results"], 1):
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            raise ValueError("Each result requires a URL")
        canonical_url(item["url"])
        row = {"rank": rank, "url": item["url"]}
        for key in ("title", "snippet", "summary", "content"):
            value = item.get(key, "")
            if value is None:
                value = ""
            if not isinstance(value, str):
                raise ValueError("Invalid result text field: " + key)
            row[key] = value
        row["date"] = item.get("date", item.get("published_at"))
        if item.get('citation_id') is not None:
            if type(item['citation_id']) not in (str, int):
                raise ValueError('Citation identifiers must be strings or integers')
            row['citation_id'] = item['citation_id']
        rows.append(row)
    return rows


URL_POLICIES = ('literal/1', 'document-url/2')


def canonical_url(url, *, policy='literal/1'):
    if policy not in URL_POLICIES:
        raise ValueError('Unsupported URL equivalence policy')
    p = urlsplit(url)
    _ = p.port
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("Result URL must be an HTTP(S) URL without credentials")
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
        and k.lower()
        not in (
            "fbclid",
            "gclid",
            "_bhlid",
            "pscd",
            "ps_partner_key",
            "ps_xid",
            "gsxid",
            "gspk",
        )
    ]
    scheme, authority = p.scheme.lower(), p.netloc.lower()
    if policy == 'document-url/2':
        host = p.hostname.lower()
        if host.startswith(('www.', 'm.')):
            host = host.split('.', 1)[1]
        if host.endswith('.m.wikipedia.org'):
            host = host.replace('.m.wikipedia.org', '.wikipedia.org')
        if host in ('wikipedia.com', 'en.wikipedia.com'):
            host = 'en.wikipedia.org'
        if ':' in host:
            host = '[' + host + ']'
        default_port = 80 if scheme == 'http' else 443
        authority = host + (':' + str(p.port) if p.port not in (None, default_port) else '')
        # A different explicit service port is not a web-page transport alias.
        if p.port in (None, default_port):
            scheme = 'https'
    return urlunsplit(
        (
            scheme,
            authority,
            p.path.rstrip("/") or "/",
            urlencode(query),
            "",
        )
    )


def in_domain(url, domain):
    try:
        host = (urlsplit(canonical_url(url)).hostname or "").lower()
    except ValueError:
        return False
    return host == domain.lower() or host.endswith("." + domain.lower())


def evaluate(case, results, *, cutoff=10):
    rows = results[:cutoff]
    urls = [canonical_url(r["url"]) for r in rows]
    anchors = {canonical_url(u) for u in case.get("anchors", [])}
    aliases = {
        canonical_url(alias): canonical_url(target)
        for alias, target in case.get("identity_aliases", {}).items()
    }

    def matches(u):
        u = aliases.get(u, u)
        if u in anchors:
            return True
        parsed = urlsplit(u)
        if in_domain(u, "arxiv.org"):
            paper = re.fullmatch(
                r"/(?:abs|pdf|html)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?", parsed.path
            )
            if paper and paper[1] in case.get("paper_ids", []):
                return True
        if parsed.hostname == "github.com":
            repo = "/".join(parsed.path.strip("/").split("/")[:2]).lower()
            if repo in [r.lower() for r in case.get("repositories", [])]:
                return True
        return False

    ranks = [i + 1 for i, u in enumerate(urls) if not rows[i].get('invalid_slot') and matches(u)]
    qrels = {canonical_url(k): v for k, v in case.get("qrels", {}).items()}
    ndcg = None
    if qrels and all(u in qrels for u in urls):
        seen = set()
        gains = []
        for u in urls:
            gains.append(0 if u in seen else qrels[u])
            seen.add(u)

        def dcg(values):
            return sum((2**v - 1) / math.log2(i + 2) for i, v in enumerate(values))

        ideal = dcg(sorted(qrels.values(), reverse=True)[:cutoff])
        ndcg = dcg(gains) / ideal if ideal else None
    domains = case.get("domains", [])
    metrics = {
        "anchor_hit_5": int(any(r <= 5 for r in ranks)) if anchors else None,
        "anchor_hit_10": int(any(r <= 10 for r in ranks)) if anchors else None,
        "reciprocal_rank": 1 / min(ranks) if ranks else (0 if anchors else None),
        "ndcg_10": ndcg,
        "domain_precision": sum(any(in_domain(u, d) for d in domains) for u in urls)
        / len(urls)
        if domains and urls
        else None,
        "duplicate_rate": 1 - len(set(urls)) / len(urls) if urls else None,
        "result_count": len(rows),
        "content_coverage": sum(bool((r.get("content") or "").strip()) for r in rows)
        / len(rows)
        if rows
        else None,
        "snippet_chars": sum(len(r.get("snippet") or "") for r in rows),
        "summary_chars": sum(len(r.get("summary") or "") for r in rows),
        "content_chars": sum(len(r.get("content") or "") for r in rows),
    }
    if "reference_answer" in case:
        metrics.update(factual_evidence(case, rows[:5]))
    return metrics


def answer_tokens(text):
    """Conservative lexical evidence, never semantic correctness or entailment."""
    return re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold())


def contains_answer(text, answer):
    wanted, actual = answer_tokens(answer), answer_tokens(text)
    return bool(wanted) and any(
        actual[i : i + len(wanted)] == wanted
        for i in range(len(actual) - len(wanted) + 1)
    )


def factual_evidence(case, results, *, cutoff=5):
    results = results[:cutoff]
    policy = case.get('url_equivalence', 'literal/1')
    references = list(case.get('reference_urls', []))
    references.extend(case.get('equivalent_reference_urls', []))
    sources = {canonical_url(u, policy=policy) for u in references}
    source_ranks = [
        i for i, item in enumerate(results, 1) if not item.get('invalid_slot') and canonical_url(item["url"], policy=policy) in sources
    ]

    # Test fields individually so words spanning unrelated fields cannot form a hit.
    def hit(fields):
        from .answer_rules import POLICY_NAME

        if case.get("answer_match_rule") == POLICY_NAME:
            from .answer_rules import match_reference_answer

            matcher = lambda text: match_reference_answer(
                case["reference_answer"], text, derivation=case.get("answer_derivation")
            )
        else:
            matcher = lambda text: contains_answer(text, case["reference_answer"])
        return int(
            any(matcher(item.get(field, "")) for item in results if not item.get('invalid_slot') for field in fields)
        )

    if case.get('evaluation_level') == 'l2':
        return {f'answer_in_extra_text_{cutoff}': hit(('snippet',))}
    return {
        f"labeled_source_hit_{cutoff}": int(bool(source_ranks)),
        f"labeled_source_rr_{cutoff}": 1 / min(source_ranks) if source_ranks else 0,
        f"answer_in_preview_{cutoff}": hit(("title", "snippet")),
        f"answer_in_delivered_text_{cutoff}": hit(
            ("title", "snippet", "summary", "content")
        ),
    }


def answer_evidence_key(case, cutoff=5):
    if case.get('evaluation_level') == 'l2':
        return f'answer_in_extra_text_{cutoff}'
    scope = 'preview' if case.get('answer_evidence_scope') == 'preview' else 'delivered_text'
    return f'answer_in_{scope}_{cutoff}'


def candidate_identity(case_id, url):
    return digest((case_id + "\0" + canonical_url(url)).encode())[:12]


def ranking_identity(case_id, item, *, preview=False, level=None):
    """A malformed native slot contributes zero without moving later ranks."""
    if item.get('invalid_slot'):
        return None
    if preview:
        import json
        identity = [case_id, canonical_url(item['url']), item.get('title') or '', item.get('snippet') or '']
        if level is not None:
            if level not in ('l1', 'l2'):
                raise ValueError('Invalid relevance evaluation level')
            identity.insert(0, level)
        return digest(json.dumps(identity, ensure_ascii=False, separators=(',', ':')).encode())[:12]
    return candidate_identity(case_id, item['url'])


def ranking_ids(case_id, results, *, preview=False, level=None):
    seen, ranking = {}, []
    for item in results:
        if item.get('invalid_slot'):
            ranking.append(None)
            continue
        url = canonical_url(item['url'])
        ranking.append(seen.setdefault(url, ranking_identity(case_id, item, preview=preview, level=level)))
    return ranking


def response_failed(row):
    return bool(row.get("error") or row.get("http_status", 200) != 200)


def task_acceptance(
    case,
    row,
    metric="primary",
    *,
    quality_excluded=False,
    cutoff=5,
    relevance_grades=None,
):
    if row is None:
        return None
    if quality_excluded and metric != "availability":
        return None
    if response_failed(row):
        return False
    if metric == "primary" and relevance_grades is not None:
        limit = min(cutoff, len(row.get("results", [])))
        return any(g is not None and g >= 2 for g in relevance_grades[:limit])
    if metric == "primary":
        metric = (
            "answer"
            if "reference_answer" in case
            else "source"
            if case.get("anchors")
            else "site"
            if case.get("domains") and case.get("family") in ("site", "vertical")
            else "unscored"
        )
    if metric == "availability":
        return any(not item.get('invalid_slot') for item in row.get('results', []))
    if metric == "site":
        return (
            any(
                any(in_domain(item["url"], d) for d in case["domains"])
                for item in row.get("results", [])[:cutoff]
            )
            if case.get("domains")
            else None
        )
    if metric == "unscored":
        return None
    if metric not in ("answer", "source"):
        raise ValueError("Unknown comparison metric")
    if "reference_answer" in case:
        from .evidence_quality import factual_sensitivity

        metrics = factual_sensitivity(case, row.get("results", []), cutoff=cutoff)[
            "metrics"
        ]
        return bool(
            metrics[answer_evidence_key(case, cutoff)]
            if metric == "answer"
            else metrics[f"labeled_source_hit_{cutoff}"]
        )
    if metric == "answer" or not case.get("anchors"):
        return None
    safe = [
        r
        for r in row.get("results", [])[:cutoff]
        if str(r.get("url", "")).startswith(("https://", "http://"))
    ]
    return bool(evaluate(case, safe, cutoff=cutoff)["reciprocal_rank"])


def classify(case, row, *, cutoff=5, quality_excluded=False, relevance_grades=None):
    """Primary acceptance and diagnostic labels share the same task predicate."""
    if response_failed(row):
        return "fail", "api_error"
    if not any(not item.get('invalid_slot') for item in row['results']):
        return "fail", "empty"
    if quality_excluded:
        return "probe", "quality_excluded"
    accepted = task_acceptance(
        case,
        row,
        cutoff=cutoff,
        quality_excluded=quality_excluded,
        relevance_grades=relevance_grades,
    )
    category = (
        "relevance"
        if relevance_grades is not None
        else "answer"
        if "reference_answer" in case
        else "target"
        if case.get("anchors")
        else "domain"
        if case.get("domains")
        else "probe"
    )
    return (
        (("pass" if accepted else "fail"), category + ("_hit" if accepted else "_miss"))
        if accepted is not None
        else ("probe", "probe")
    )
