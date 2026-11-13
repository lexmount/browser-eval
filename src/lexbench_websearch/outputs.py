"""Declared native output roles and isolated evaluation inputs."""

import unicodedata
from copy import deepcopy

LEVELS = ("l1", "l2", "l3")
ROLES = ("primary_preview", "extra_text", "answer", "page_body")


def validate_fields(fields):
    if not isinstance(fields, dict) or set(fields) != set(ROLES):
        raise ValueError("Output fields must declare every role")
    if fields["primary_preview"] not in (None, "snippet"):
        raise ValueError("The primary preview must use the normalized snippet field")
    if not isinstance(fields["extra_text"], list) or len(set(fields["extra_text"])) != len(
        fields["extra_text"]
    ):
        raise ValueError("Extra-text fields must be a unique list")
    if any(f not in ("summary",) for f in fields["extra_text"]):
        raise ValueError("Full page bodies and answers cannot enter extra text")
    if fields["answer"] not in (None, "answer") or fields["page_body"] not in (None, "content"):
        raise ValueError("Invalid answer or page-body field")
    return fields


def levels(fields):
    fields = validate_fields(fields)
    return ["l1"] + (["l2"] if fields["extra_text"] else []) + (["l3"] if fields["answer"] else [])


def project_results(results, fields, level):
    """Keep only the selected output type; never infer roles from text length."""
    if level not in levels(fields) or level == "l3":
        raise ValueError("This configuration has no per-result output at the requested level")
    output = []
    for item in results:
        projected = {
            k: deepcopy(item[k])
            for k in ("url", "rank", "invalid_slot", "citation_id")
            if k in item
        }
        projected["title"] = item.get("title") or ""
        names = [fields["primary_preview"]] if level == "l1" else fields["extra_text"]
        parts = [item.get(f) or "" for f in names if f is not None]
        if any(not isinstance(text, str) for text in parts):
            raise ValueError("Scored text fields must be strings")
        projected["snippet"] = "\n\n".join(parts)
        output.append(projected)
    return output


def project_rows(profile, rows, level, *, qualified_fields=None):
    """Select participants from declared capabilities, preserving every task."""
    selected = []
    for method in profile["methods"]:
        fields = validate_fields(method["output_fields"])
        if level not in levels(fields):
            continue
        if level == "l2":
            receipt = (qualified_fields or {}).get(method["id"])
            if not receipt or receipt.get("fields") != fields["extra_text"]:
                raise ValueError("L2 requires the exact field qualification receipt")
            if not receipt.get("qualified"):
                continue
        selected.append(deepcopy(method))
    ids = {m["id"] for m in selected}
    projected = deepcopy(profile)
    projected["methods"] = selected
    projected["protocol"]["relevance_level"] = level
    projected["protocol"]["relevance_input"] = "delivered-previews/1"
    for case in projected["cases"]:
        case["evaluation_level"] = level
    projected.pop("relevance_methods", None)
    by_id = {m["id"]: m for m in selected}
    output = []
    for row in rows:
        if row["method"] not in ids:
            continue
        value = deepcopy(row)
        if level != "l3":
            value["results"] = project_results(
                row["results"][: profile["protocol"].get("top_k", 5)],
                by_id[row["method"]]["output_fields"],
                level,
            )
        output.append(value)
    expected = {(c["id"], m["id"]) for c in profile["cases"] for m in selected}
    actual = [(r["case_id"], r["method"]) for r in output]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Every level requires the complete method-by-task population")
    for method in selected:
        scoped = [r for r in output if r["method"] == method["id"]]
        if level == "l3":
            delivered = any((r.get("answer") or "").strip() for r in scoped)
        elif level == "l2":
            delivered = any(
                (item.get("snippet") or "").strip() for r in scoped for item in r["results"]
            )
        else:
            delivered = any(not item.get("invalid_slot") for r in scoped for item in r["results"])
        if not delivered:
            raise ValueError("A declared output level delivered nothing: " + method["id"])
    if level != "l3":
        from .scoring import evaluate

        cases = {case["id"]: case for case in projected["cases"]}
        for value in output:
            case = cases[value["case_id"]]
            cutoff = profile["protocol"].get("top_k", 5)
            value["metrics"] = evaluate(case, value["results"], cutoff=cutoff)
            if "reference_answer" in case:
                from .evidence_quality import factual_sensitivity
                from .scoring import answer_evidence_key

                key = answer_evidence_key(case, cutoff)
                value["metrics"][key] = factual_sensitivity(case, value["results"], cutoff=cutoff)["metrics"][key]
    return projected, output


def normalized_text(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def block_overlap(text, page, *, minimum_block=12):
    """Fraction of the field supported by contiguous original-page blocks."""
    text, page = normalized_text(text), normalized_text(page)
    if not text or not page:
        return 0.0
    width = min(minimum_block, len(text))
    requested = {text[i : i + width] for i in range(len(text) - width + 1)}
    found = requested.intersection(
        page[i : i + width] for i in range(max(0, len(page) - width + 1))
    )
    covered = bytearray(len(text))
    for i in range(len(text) - width + 1):
        if text[i : i + width] in found:
            covered[i : i + width] = b"\1" * width
    return sum(covered) / len(text)


def qualify_field(samples, *, sample_size=50, threshold=0.8):
    """Require a complete, independently fetched sample before L2 admission."""
    if len(samples) != sample_size or len({s["url"] for s in samples}) != sample_size:
        raise ValueError("Field admission requires the complete unique-URL sample")
    if any(
        not s.get("text") or not s.get("page_text") or not s.get("observation_sha256")
        for s in samples
    ):
        raise ValueError("Missing page evidence cannot qualify or disqualify a field")
    weights = [len(normalized_text(s["text"])) for s in samples]
    matched = sum(
        n * block_overlap(s["text"], s["page_text"]) for n, s in zip(weights, samples, strict=True)
    )
    ratio = matched / sum(weights) if sum(weights) else 0.0
    return dict(
        sample_size=sample_size,
        threshold=threshold,
        matched_fraction=ratio,
        qualified=ratio >= threshold,
    )
