"""Validate task definitions independently of any execution harness."""

import re
from .scoring import URL_POLICIES, canonical_url


def validate_cases(cases, *, reference_checks=None, reference_scope=None):
    ids = [case["id"] for case in cases]
    if (
        not cases
        or len(ids) != len(set(ids))
        or any(not re.fullmatch("[a-z0-9-]+", value) for value in ids)
    ):
        raise ValueError("Empty, duplicate or unsafe task identities")
    for case in cases:
        if 'equivalent_reference_urls' in case or 'equivalent_reference_provenance' in case:
            from .simpleqa import validate_reference_extensions

            validate_reference_extensions(case)
        if case.get('url_equivalence', 'literal/1') not in URL_POLICIES:
            raise ValueError('Unsupported URL equivalence policy')
        for url in case.get('equivalent_reference_urls', []):
            canonical_url(url)
        if case.get("answer_derivation") is not None:
            from .answer_rules import reference_rule

            if "reference_answer" not in case:
                raise ValueError("Answer derivation requires a reference answer")
            reference_rule(
                case["reference_answer"], derivation=case["answer_derivation"]
            )
        for url in case.get("reference_urls", []):
            canonical_url(url)
        for alias, target in case.get("identity_aliases", {}).items():
            canonical_url(alias)
            if canonical_url(target) not in {
                canonical_url(u) for u in case.get("anchors", [])
            }:
                raise ValueError("Identity alias must refer to an existing target")
        for url in case.get("anchors", []):
            canonical_url(url)
            if reference_scope == "identity":
                check = (reference_checks or {}).get(url)
                if (
                    not check
                    or not check.get("contains")
                    or any(
                        not isinstance(s, str) or not s.strip()
                        for s in check["contains"]
                    )
                ):
                    raise ValueError(
                        "Identity references require nonempty publisher assertions"
                    )
                canonical_url(check["url"])
        for value in case.get("qrels", {}).values():
            if type(value) is not int or not 0 <= value <= 3:
                raise ValueError("Relevance grades must be integers 0..3")
