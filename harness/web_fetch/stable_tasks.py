"""Pure acceptance contracts for changing, structured page content.

Values are parsed, never compared to a snapshot. Supported Markdown/plain-text
layouts preserve record boundaries; fields are never searched across cards.
Passing establishes the declared output structure, not factual source fidelity.
"""
from __future__ import annotations

from datetime import date
import re
from html import unescape
import unicodedata
from urllib.parse import urlsplit

from .dynamic_lists import extract_named_entries
from .reference_quality import clean_reference_text


SCHEMA_FIELDS = {
    "quote": ("quote", "author", "tags"),
    "product": ("name", "price"),
    "catalog_product": ("title", "price", "description", "reviews"),
    "review": ("date", "text"),
    "name_description": ("name", "description"),
    "api_operation": ("method", "path", "summary"),
    "taxonomy_family": ("family",),
    "named_link": ("title", "url"),
    "page_identity": (),
}
UNIQUE_FIELDS = {
    "quote": ("quote",), "product": ("name",),
    "catalog_product": ("title", "description"), "review": ("date", "text"),
    "name_description": ("name",), "api_operation": ("method", "path"),
    "taxonomy_family": ("family",), "named_link": ("url",),
    "page_identity": (),
}
_AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
_MONEY = rf"(?:[$£€¥]\s*{_AMOUNT}|(?:USD|EUR|GBP|CNY)\s+{_AMOUNT}|{_AMOUNT}\s+(?:USD|EUR|GBP|CNY))"
_METHOD = r"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE|CONNECT)"
_REVIEW_COUNT = r"\d+\s+reviews?"
_PLACEHOLDER = re.compile(r"(?:n/?a|none|null|undefined|unknown|tbd|loading[. …]*|[-—]+|add to cart|read more|view details|description|name|title)", re.I)
_BLOCKED_PAGE = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:access denied|403 forbidden|verify (?:that )?you are human|"
    r"checking your browser|just a moment(?:\.{3}|…)?|attention required!?|captcha(?: challenge)?)[.!: \t]*$")
_STRONG_BLOCKED_PAGE_PREFIX = re.compile(r"(?i)^\s*your request has been blocked\b")
_LABELS = {"quote": "quote", "author": "(?:author|by)", "tags": "tags",
    "name": "name", "title": "title", "price": "price", "description": "description",
    "reviews": "reviews", "date": "date", "text": "(?:text|review)", "method": "method",
    "path": "path", "summary": "summary", "family": "family", "financing": "financing", "shipping": "shipping"}


def _key(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


_CARD_BOUNDARY = "\ue000"


def _closing(text, start, opening, closing):
    depth = 1
    i = start + 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == opening:
            depth += 1
        elif text[i] == closing:
            depth -= 1
            if not depth:
                return i
        i += 1
    return None


def _unwrap_links(text, schema):
    """Unwrap balanced inline links, including multiline card labels.

    Only explicit markup creates boundaries; text from separate cards is never
    flattened together. Image alt text is not a substitute for a visible title.
    """
    output, i = [], 0
    while i < len(text):
        image = text.startswith("![", i)
        start = i + 1 if image else i
        if text[start:start + 1] != "[":
            output.append(text[i])
            i += 1
            continue
        end = _closing(text, start, "[", "]")
        destination = end + 1 if end is not None else len(text)
        close = _closing(text, destination, "(", ")") if text[destination:destination + 1] == "(" else None
        if close is None:
            output.append(text[i])
            i += 1
            continue
        raw_label = text[start + 1:end]
        label = "" if image else _unwrap_links(raw_label, None).strip()
        image_only = image or (not label and raw_label.lstrip().startswith("!["))
        if image_only and schema == "name_description":
            prefix = "".join(output).rsplit("\n", 1)[-1]
            if re.fullmatch(r"\s*(?:[-*+]|\d+[.)])\s*", prefix):
                output.append(_CARD_BOUNDARY)
        if label:
            rendered = "".join(output)
            prefix = rendered.rsplit("\n", 1)[-1]
            separated = label.startswith(("/", "#")) or any(char.isspace() for char in label)
            if prefix and not prefix[-1].isspace() and separated:
                # Native Markdown can omit the DOM whitespace immediately
                # before a path or multi-word inline link. The markup itself
                # preserves that element boundary; do not apply this to links
                # embedded in a single word such as ``pre[fix]``.
                output.append(" ")
            if schema in {"product", "catalog_product"} and "\n" in text[start + 1:end]:
                label = f"\n{_CARD_BOUNDARY}\n{label}\n{_CARD_BOUNDARY}\n"
            elif schema == "name_description" and (re.fullmatch(r"\s*(?:[-*+]|\d+[.)])\s*", prefix)
                    or re.fullmatch(rf"\s*(?:[-*+]|\d+[.)])\s*{_CARD_BOUNDARY}\s*", prefix)
                    or re.search(rf"(?m)^\s*(?:[-*+]|\d+[.)])\s*(?:{_CARD_BOUNDARY})?\s*\n\s*$", rendered)):
                # A bullet's linked title owns the following prose, even when
                # both share one physical line. Some converters put an
                # image-only bullet on the previous line; that orphan bullet
                # still marks the following visible title as the card owner.
                label += "\n"
            output.append(label + " ")
        i = close + 1
    return "".join(output)


def _visible(text, schema=None):
    text = unescape(clean_reference_text(text))
    # Extractors represent Markdown hard breaks using one or two backslashes.
    text = re.sub(r"\\{1,2}[ \t]*(?=\n)", "", text)
    text = _unwrap_links(text, schema)
    # CommonMark punctuation escapes preserve the same visible characters.
    # Remove them before date/field parsing (for example ``2023\-05\-18``).
    text = re.sub(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\]\\^_`{|}~])', r'\1', text)
    if schema == "name_description":
        # Image-only card links intentionally contribute no visible title.
        # Remove their now-empty bullet line so blank-delimited card parsing
        # sees the following visible title and description as one record.
        text = re.sub(r"(?m)^\s*(?:[-*+]|\d+[.)])\s*$", "", text)
        text = re.sub(rf"(?m)^\s*(?:[-*+]|\d+[.)])\s*{_CARD_BOUNDARY}\s*$",
                      f"\n{_CARD_BOUNDARY}\n", text)
        text = "\n".join(line if line.strip() == _CARD_BOUNDARY
                         else line.replace(_CARD_BOUNDARY, "")
                         for line in text.splitlines())
    text = re.sub(r"\*\*|__|`", "", text)
    # Preserve newlines: flattening them would let one card borrow another's fields.
    return text.replace("\r\n", "\n")


def _line(text):
    return re.sub(r"^(?:\s*[#>]+\s*|\s*[-*+]\s+|\s*\d+[.)]\s+)", "", text).strip()


def _field(text, name):
    return re.sub(rf"^(?:{_LABELS.get(name, name)})\s*:\s*", "", text, flags=re.I).strip()


def _text(value, minimum=2):
    return (len(value.strip()) >= minimum and any(c.isalpha() for c in value)
            and not _PLACEHOLDER.fullmatch(value.strip()) and not re.match(r"https?://", value)
            and not re.fullmatch(_MONEY, value, re.I))


def _valid_field(name, value):
    if not isinstance(value, str) or not value.strip():
        return False
    if name == "price":
        return bool(re.fullmatch(_MONEY, value, re.I))
    if name == "reviews":
        return bool(re.fullmatch(r"\d+(?:\s*reviews?)?", value, re.I))
    if name == "date":
        try:
            return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) and date.fromisoformat(value))
        except ValueError:
            return False
    if name == "method":
        return bool(re.fullmatch(_METHOD, value))
    if name == "path":
        return bool(re.fullmatch(r"/(?:[A-Za-z0-9_{}./~%:@+!$&'()*,;=\-]*)", value)) and not value.startswith("//")
    if name == "family":
        return bool(re.fullmatch(r"[A-Za-z]+idae", value))
    if name == "financing":
        return bool(re.fullmatch(rf"(?:or\s+)?[1-9]\d*\s*[x×]\s*{_MONEY}", value, re.I))
    if name == "shipping":
        return bool(re.fullmatch(rf"(?:free shipping|shipping\s*:?\s*(?:free|{_MONEY}))", value, re.I))
    return _text(value, 3 if name in {"description", "text", "summary", "quote"} else 2)


def _record(fields, schema):
    errors = [f"invalid_field:{field}" for field in SCHEMA_FIELDS[schema]
              if not _valid_field(field, fields.get(field))]
    for field in ("financing", "shipping"):
        if field in fields and not _valid_field(field, fields[field]):
            errors.append(f"invalid_field:{field}")
    if schema == "name_description" and _key(fields.get("name", "")) == _key(fields.get("description", "")):
        errors.append("description_repeats_name")
    return {"fields": fields, "valid": not errors, "errors": errors}


def _table_records(lines, schema):
    """Explicit Markdown table headers own their columns, including empty cells."""
    records, occupied = [], set()
    required = set(SCHEMA_FIELDS[schema])
    for i in range(len(lines) - 1):
        if i in occupied or "|" not in lines[i]:
            continue
        headers = [_key(x).replace("review count", "reviews") for x in lines[i].strip().strip("|").split("|")]
        separator = lines[i + 1].strip().strip("|").split("|")
        if not required <= set(headers) or not all(re.fullmatch(r"\s*:?-{3,}:?\s*", x) for x in separator):
            continue
        occupied.update((i, i + 1))
        j = i + 2
        while j < len(lines) and "|" in lines[j] and lines[j].strip():
            values = [x.strip() for x in lines[j].strip().strip("|").split("|")]
            fields = dict(zip(headers, values))
            record = _record(fields, schema)
            if len(values) != len(headers) or len(headers) != len(set(headers)):
                record["valid"] = False
                record["errors"].append("invalid_table_columns")
            records.append(record)
            occupied.add(j)
            j += 1
    return records, occupied


def _products(lines, schema):
    records = []
    claimed_shipping = set()
    i = 0
    while i < len(lines):
        current = _field(lines[i], "price")
        price_first = bool(re.fullmatch(_MONEY, current, re.I))
        inline = re.fullmatch(rf"(.+?)\s*\|?\s+({_MONEY})", lines[i], re.I)
        if price_first:
            inline = None
        next_price = i + 1 < len(lines) and re.fullmatch(_MONEY, _field(lines[i + 1], "price"), re.I)
        if not price_first and not inline and not next_price:
            i += 1
            continue
        title_field = "title" if schema == "catalog_product" else "name"
        fields = {}
        if inline:
            fields.update({title_field: _field(inline[1].rstrip(" |"), title_field), "price": inline[2]})
            end = i + 1
        elif price_first:
            fields["price"] = current
            # Plain product lists are title/price; price-first is supported for
            # catalog cards only. An orphan price cannot borrow the next title.
            if schema == "catalog_product" and i + 1 < len(lines):
                fields[title_field] = _field(lines[i + 1], title_field)
                end = i + 2
            else:
                end = i + 1
        else:
            fields.update({title_field: _field(lines[i], title_field), "price": _field(lines[i + 1], "price")})
            end = i + 2
        if schema == "catalog_product":
            description = []
            while end < len(lines):
                line = lines[end]
                count = re.fullmatch(r"(?:reviews\s*:\s*)?(\d+)\s*reviews?", line, re.I)
                if count:
                    fields["reviews"] = count[1]
                    end += 1
                    break
                # Stop before any subsequent card, rather than borrowing its
                # description or review count to repair this card.
                if (re.fullmatch(_MONEY, _field(line, "price"), re.I)
                        or end + 1 < len(lines) and re.fullmatch(_MONEY, _field(lines[end + 1], "price"), re.I)):
                    break
                description.append(_field(line, "description"))
                end += 1
            fields["description"] = " ".join(description)
        else:
            if i and i-1 not in claimed_shipping and re.fullmatch(r'free shipping|shipping\s*:\s*free', lines[i-1], re.I):
                fields['shipping'] = lines[i-1]
                claimed_shipping.add(i-1)
            while end < len(lines) and re.match(r"(?:or(?=\s|\d)|financing\s*:|free shipping|shipping\s*:)", lines[end], re.I):
                field = "shipping" if "shipping" in lines[end].casefold() else "financing"
                fields[field] = re.sub(r'^or(?=\d)', 'or ', _field(lines[end], field)) if field == "financing" else lines[end]
                if field == 'shipping':
                    claimed_shipping.add(end)
                end += 1
        records.append(_record(fields, schema))
        i = end
    return records


def _quotes(lines):
    records, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not re.match(r'[“"‘]|quote\s*:', line, re.I):
            i += 1
            continue
        parts = [line]
        j = i + 1
        while j < len(lines) and not re.match(r'[“"‘]|quote\s*:', lines[j], re.I):
            if re.match(r'(?:#{1,6}\s*)?(?:loading[. …]*|quotes by\s*:|made with\b)',
                        lines[j], re.I):
                break
            parts.append(lines[j])
            j += 1
        text = " ".join(parts)
        match = re.fullmatch(r'(?:quote\s*:\s*)?[“"‘]?(.*?)[”"’]?\s*(?:by\s*|author\s*:\s*)(.+?)\s*tags\s*:\s*(.+)', text, re.I)
        fields = {"quote": match[1].strip('“”"‘’ '), "author": match[2], "tags": match[3]} if match else {}
        if fields:
            fields["author"] = re.sub(r"\s*\(about\)\s*$", "", fields["author"], flags=re.I)
        records.append(_record(fields, "quote"))
        i = j
    return records


def _reviews(lines):
    records, i = [], 0
    while i < len(lines):
        match = re.match(r"(?:date\s*:\s*)?(\d{4}-\d{2}-\d{2})(?:\s+(.+))?$", lines[i], re.I)
        if not match:
            i += 1
            continue
        fields = {"date": match[1]}
        text = [match[2]] if match[2] else []
        j = i + 1
        while j < len(lines) and not re.match(r"(?:date\s*:\s*)?\d{4}-\d{2}-\d{2}", lines[j], re.I):
            text.append(_field(lines[j], "text"))
            j += 1
        fields["text"] = " ".join(text)
        records.append(_record(fields, "review"))
        i = j
    return records


def _operations(lines):
    records, i = [], 0
    while i < len(lines):
        # Some native Markdown serializers emit METHOD[/path](target)Summary;
        # visible-link normalization leaves METHOD/path Summary without a gap.
        match = re.match(rf"(?:method\s*:\s*)?({_METHOD})(?:(?:\s+|(?=/))(.+))?$", lines[i])
        if not match:
            i += 1
            continue
        fields = {"method": match[1]}
        rest = [match[2]] if match[2] else []
        j = i + 1
        while j < len(lines) and not re.match(rf"(?:method\s*:\s*)?{_METHOD}\b", lines[j]):
            rest.append(lines[j])
            j += 1
        if rest:
            path = re.fullmatch(r"(?:path\s*:\s*)?(/\S*)(?:\s+(.+))?", rest[0], re.I)
            if path:
                fields["path"] = path[1]
                summary = ([path[2]] if path[2] else []) + rest[1:]
                fields["summary"] = " ".join(_field(s, "summary") for s in summary)
        records.append(_record(fields, "api_operation"))
        i = j
    return records


def _descriptions(text):
    records = []
    if _CARD_BOUNDARY in text:
        # A marker comes from an image-only bullet immediately before the
        # visible linked title. It is a stronger ownership boundary than blank
        # lines, which may legitimately occur inside a card description.
        for block in text.split(_CARD_BOUNDARY)[1:]:
            lines = [_line(line) for line in block.splitlines() if line.strip()]
            if len(lines) < 2:
                continue
            records.append(_record({"name": _field(lines[0], "name"),
                "description": " ".join(_field(line, "description") for line in lines[1:])},
                "name_description"))
        return records
    bullets = list(re.finditer(r"(?m)^\s*(?:[-*+]|\d+[.)])[ \t]+\S.*$", text))
    if bullets:
        for index, bullet in enumerate(bullets):
            end = bullets[index + 1].start() if index + 1 < len(bullets) else len(text)
            lines = [_line(line) for line in text[bullet.start():end].splitlines() if line.strip()]
            records.append(_record({"name": _field(lines[0], "name"),
                "description": " ".join(_field(line, "description") for line in lines[1:])}, "name_description"))
        return records
    # Both Markdown headings and blank-delimited plain cards express ownership.
    headings = list(re.finditer(r"(?m)^(#{1,6})\s+(.+)$", text))
    if headings:
        # Repeated deepest-level headings are cards; enclosing page/section
        # headings are not records. Blank lines within a card do not erase its
        # ownership boundary.
        depth = max(len(h[1]) for h in headings)
        for index, heading in enumerate(headings):
            if len(heading[1]) != depth:
                continue
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            body = [_line(line) for line in text[heading.end():end].splitlines() if line.strip()]
            records.append(_record({"name": _field(heading[2], "name"),
                "description": " ".join(_field(line, "description") for line in body)}, "name_description"))
        return records
    blocks = re.split(r"\n\s*\n", text)
    for block in blocks:
        lines = [_line(line) for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        fields = {"name": _field(lines[0], "name"),
                  "description": " ".join(_field(line, "description") for line in lines[1:])}
        records.append(_record(fields, "name_description"))
    return records


def _parse_records(visible, schema):
    raw_lines = visible.splitlines()
    records, occupied = _table_records(raw_lines, schema)
    remainder = "\n".join(line if i not in occupied else "" for i, line in enumerate(raw_lines))
    lines = [_line(line) for line in remainder.splitlines() if line.strip()]
    if schema in {"product", "catalog_product"}:
        for block in remainder.split(_CARD_BOUNDARY):
            records.extend(_products([_line(line) for line in block.splitlines() if line.strip()], schema))
    elif schema == "quote":
        records.extend(_quotes(lines))
    elif schema == "review":
        records.extend(_reviews(lines))
    elif schema == "api_operation":
        # Keep section boundaries until after parsing ownership. Otherwise the
        # last operation in each group absorbs the next heading, its overview,
        # or the models/footer section into its summary. A heading may itself
        # contain METHOD /path, so retain its text within the new section.
        sections = re.split(r"(?m)(?=^[ \t]{0,3}#{1,6}[ \t]+)", remainder)
        for section in sections:
            records.extend(_operations([_line(line) for line in section.splitlines() if line.strip()]))
    elif schema == "name_description":
        records.extend(_descriptions(remainder))
    elif schema == "taxonomy_family":
        records.extend(_record({"family": _field(line, "family")}, schema) for line in lines
                       if re.fullmatch(r"(?:family\s*:\s*)?[A-Za-z]+idae", line, re.I))
    return records


def _anchor_matches(anchor, records, visible, schema):
    from .metrics import fact_in_text, normalize_for_match
    if anchor.get('match_scope') == 'non_record_lines':
        # A known record label cannot stand in for page context. This is a
        # conservative ownership rule, not a claim of recovered DOM ownership.
        # Some providers flatten the whole rendered page onto one physical line;
        # dropping that line would also erase genuine page headings. Mask the
        # independently parsed record titles instead, preserving surrounding
        # page text while still preventing a title from supplying an anchor.
        titles = [normalize_for_match(r['fields']['title']) for r in records if r.get('valid') and r.get('fields',{}).get('title')]
        folded = normalize_for_match(visible)
        for title in sorted(set(titles), key=len, reverse=True):
            folded = folded.replace(title, ' ')
        return [value for value in anchor["alternatives"]
                if fact_in_text(folded, normalize_for_match(_visible(value)))]
    owned = []
    if schema != "named_link":
        for value in anchor["alternatives"]:
            expected = _parse_records(_visible(value, schema), schema)
            if len(expected) == 1 and expected[0]["valid"]:
                owned.append((value, expected[0]["fields"]))
    if owned:
        # Structured anchors compare complete owned fields, not a flattened
        # document substring. Table columns and Markdown links are equivalent.
        def field_matches(record, fields, field):
            actual = normalize_for_match(record["fields"][field]).strip('“”"‘’ ')
            expected = normalize_for_match(fields[field]).strip('“”"‘’ ')
            # Some native Markdown converters omit whitespace at inline DOM
            # boundaries. In name/description cards the owned record still
            # proves the boundary; quote tags are likewise a declared tag list,
            # so compact-equivalent serialization retains the same values.
            compact = schema == "name_description" or (schema == "quote" and field == "tags")
            return (actual.replace(" ", "") == expected.replace(" ", "")
                    if compact else actual == expected)
        return [value for value, fields in owned if any(record["valid"] and all(
            field_matches(record, fields, field)
            for field in SCHEMA_FIELDS[schema]) for record in records)]
    folded = normalize_for_match(visible)
    return [value for value in anchor["alternatives"] if fact_in_text(folded, normalize_for_match(_visible(value)))]


def validate_acceptance_contract(contract: dict) -> None:
    """Fail closed before a caller chooses stable scoring instead of snapshots."""
    if (not isinstance(contract, dict) or type(contract.get("version")) is not int
            or contract.get("version") != 1 or not isinstance(contract.get("schema"), str)
            or contract.get("schema") not in SCHEMA_FIELDS):
        raise ValueError("Unsupported stable acceptance contract")
    if set(contract) - {"version", "schema", "minimum_records", "maximum_records", "page_patterns", "stable_text_anchors", "hosts", "entry_path", "field_minimum_records"}:
        raise ValueError("Unknown stable acceptance contract fields")
    field_counts = contract.get('field_minimum_records', {})
    if not isinstance(field_counts, dict) or any(k not in _LABELS or type(v) is not int or v < 1 for k,v in field_counts.items()):
        raise ValueError('Invalid required field counts')
    minimum, maximum = contract.get("minimum_records"), contract.get("maximum_records")
    minimum_allowed = 0 if contract["schema"] == "page_identity" else 1
    if (type(minimum) is not int or minimum < minimum_allowed
            or contract["schema"] == "page_identity" and minimum != 0
            or maximum is not None and (type(maximum) is not int or maximum < minimum)):
        raise ValueError("Stable contract requires a valid bounded record count")
    patterns = contract.get("page_patterns", [])
    if not isinstance(patterns, list) or any(not isinstance(p, str) or not p for p in patterns):
        raise ValueError("Invalid stable page patterns")
    anchors = contract.get("stable_text_anchors", [])
    if not isinstance(anchors, list):
        raise ValueError("Stable text anchors must be explicitly declared groups")
    ids = set()
    for anchor in anchors:
        if (not isinstance(anchor, dict) or not {"id", "alternatives"} <= set(anchor)
                or set(anchor) - {"id", "alternatives", "match_scope"}
                or ('match_scope' in anchor and (anchor['match_scope'] != 'non_record_lines' or contract['schema'] != 'named_link'))
                or not isinstance(anchor.get("id"), str) or not anchor["id"].strip() or anchor["id"] in ids
                or not isinstance(anchor.get("alternatives"), list) or not anchor["alternatives"]
                or any(not isinstance(value, str) or not _visible(value).strip() for value in anchor["alternatives"])):
            raise ValueError("Invalid or duplicate stable text anchor group")
        ids.add(anchor["id"])
    if contract["schema"] == "named_link":
        hosts = contract.get("hosts")
        if (not isinstance(hosts, list) or not hosts
                or any(not isinstance(host, str) or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", host) for host in hosts)
                or not isinstance(contract.get("entry_path"), str) or not contract["entry_path"]):
            raise ValueError("Named links require allowed hosts and detail path pattern")
        patterns = [*patterns, contract["entry_path"]]
    elif "hosts" in contract or "entry_path" in contract:
        raise ValueError("Host/path contracts apply only to named_link records")
    if contract["schema"] == "page_identity" and not anchors:
        raise ValueError("Page identity contracts require stable text anchors")
    try:
        for pattern in patterns:
            re.compile(pattern)
    except re.error as error:
        raise ValueError("Invalid stable acceptance regex") from error


def evaluate_stable_task(content: str, contract: dict, base_url: str = "", eligible: bool = True, links=None) -> dict:
    """Validate a version-1 schema/count contract, without fetching or snapshots.

    Required keys: version, schema, minimum_records. Optional maximum_records
    imposes a finite exact scope when equal to the minimum. page_patterns is a
    list of required regexes for stable page/section identity (not current items).
    stable_text_anchors contains explicit {id, alternatives} groups; every group
    must match, independently of record shape and optional snapshot word metrics.
    named_link additionally requires hosts and entry_path, using the existing
    same-site detail-link parser. page_identity requires no records and passes
    only when every independently registered stable text group is present. Raw
    URL identity is not factual verification.
    """
    validate_acceptance_contract(contract)
    schema = contract["schema"]
    minimum, maximum = contract["minimum_records"], contract.get("maximum_records")
    patterns = contract.get("page_patterns", [])
    visible = _visible(content, schema)
    page_results = [{"pattern": p, "pass": bool(re.search(p, visible, re.I | re.M))} for p in patterns]
    content_errors = (["page_identity_mismatch"] if not all(r["pass"] for r in page_results) else [])
    # Strong first-party block banners are often flattened onto the same line
    # as cached navigation text. Keep the ordinary matcher line-scoped so an
    # article discussing access errors is not rejected, but recognize this
    # unambiguous banner when it starts the returned page.
    blocked_page = bool(
        _BLOCKED_PAGE.search(visible) or _STRONG_BLOCKED_PAGE_PREFIX.search(visible)
    )
    if blocked_page:
        content_errors.append("blocked_page")
    errors = []
    if schema == "named_link":
        try:
            entries = extract_named_entries(content, contract, base_url, links)
        except ValueError:
            # Malformed returned URLs are output validation failures, not
            # malformed contracts and not exceptions that abort a whole run.
            entries = []
            errors.append("invalid_link_output")
        records = []
        for entry in entries:
            parts = urlsplit(entry["url"])
            if parts.scheme not in {"http", "https"} or parts.username or parts.password:
                continue
            records.append({"fields": entry, "valid": True, "errors": []})
    else:
        records = _parse_records(visible, schema)
    anchors = []
    for anchor in contract.get("stable_text_anchors", []):
        matched = _anchor_matches(anchor, records, visible, schema)
        anchors.append({**anchor, "matched": matched, "pass": bool(matched)})
    content_errors += ["missing_stable_anchor:" + r["id"] for r in anchors if not r["pass"]]
    complete = [r for r in records if r["valid"]]
    unique = {tuple(_key(r["fields"][field]) for field in UNIQUE_FIELDS[schema]) for r in complete}
    record_warnings = []
    field_checks = []
    for field, count in contract.get('field_minimum_records', {}).items():
        identities = {tuple(_key(r['fields'][key]) for key in UNIQUE_FIELDS[schema])
                      for r in complete if _valid_field(field, r['fields'].get(field))}
        field_checks.append({'field': field, 'count': len(identities), 'minimum': count, 'pass': len(identities) >= count})
        if len(identities) < count:
            errors.append('missing_required_field:' + field)
    if len(complete) != len(records) and maximum is not None:
        errors.append("incomplete_records")
    elif len(complete) != len(records):
        # An at-least-N contract must not fail because unrelated script/UI text
        # happens to resemble the start of a record. Keep the parse noise
        # visible, while requiring N complete unique records independently.
        record_warnings.append("ignored_incomplete_candidates")
    if len(unique) != len(complete):
        errors.append("duplicate_records")
    if len(unique) < minimum:
        errors.append("too_few_complete_records")
    if maximum is not None and len(unique) > maximum:
        errors.append("too_many_records")
    structure_pass = not errors
    errors += content_errors
    if not eligible:
        errors.append("request_ineligible")
    return {"stable_task_pass": not errors, "stable_task_schema": schema,
            "stable_field_results": field_checks,
            "stable_structure_pass": structure_pass, "stable_content_pass": not content_errors,
            "stable_blocked_page": blocked_page,
            "stable_anchor_results": anchors, "stable_page_pattern_results": page_results,
            "stable_anchor_hits": sum(r["pass"] for r in anchors),
            "stable_anchor_total": len(anchors),
            "stable_anchor_coverage": sum(r["pass"] for r in anchors) / len(anchors) if anchors else None,
            "stable_text_scope": "declared_text_groups" if anchors else "not_annotated",
            "stable_record_count": len(records), "stable_complete_record_count": len(complete),
            "stable_unique_record_count": len(unique), "stable_minimum_records": minimum,
            "stable_maximum_records": maximum, "stable_task_errors": errors, "stable_records": records,
            "stable_record_warnings": record_warnings,
            "factual_correctness": None,
            "stable_verification_scope": "Mechanical field/group/count checks; source fidelity and factual values are not verified"}
