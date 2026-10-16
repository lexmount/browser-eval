"""Pure acceptance contracts for changing, structured page content.

Values are parsed, never compared to a snapshot. Supported Markdown/plain-text
layouts preserve record boundaries; fields are never searched across cards.
Passing establishes the declared output structure, not factual source fidelity.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from datetime import date, datetime
from html import unescape
from urllib.parse import urlsplit

from .dynamic_lists import extract_named_entries, named_entry_source_pair_hash
from .reference_quality import (
    clean_reference_text, is_markdown_image_start, markdown_delimiter_pairs,
)

SCHEMA_FIELDS = {
    "table_row": (),
    "earthquake": ("magnitude", "location", "time", "depth"),
    "body_text": (),
    "quote": ("quote", "author", "tags"),
    "product": ("name", "price"),
    "catalog_product": ("title", "price", "description", "reviews"),
    "review": ("date", "text"),
    "name_description": ("name", "description"),
    "api_operation": ("method", "path", "summary"),
    "taxonomy_family": ("family",),
    "named_link": ("title", "url"),
    "linked_product": ("title", "url", "price"),
    "page_identity": (),
}
UNIQUE_FIELDS = {
    "table_row": (),
    "earthquake": ("time", "location"),
    "body_text": (),
    "quote": ("quote",), "product": ("name",),
    "catalog_product": ("title", "description"), "review": ("date", "text"),
    "name_description": ("name",), "api_operation": ("method", "path"),
    "taxonomy_family": ("family",), "named_link": ("url",),
    "linked_product": ("url",), "page_identity": (),
}
_AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
_MONEY = rf"(?:[$£€¥]\s*{_AMOUNT}|(?:USD|EUR|GBP|CNY)\s+{_AMOUNT}|{_AMOUNT}\s+(?:USD|EUR|GBP|CNY))"
_METHOD = r"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|TRACE|CONNECT)"
_REVIEW_COUNT = r"\d+\s+reviews?"
_PLACEHOLDER = re.compile(r"(?:n/?a|none|null|undefined|unknown|tbd|loading[. …]*|[-—]+|add to cart|read more|view details|description|name|title)", re.I)
_LABELS = {"quote": "quote", "author": "(?:author|by)", "tags": "tags",
    "magnitude": "magnitude", "location": "location", "time": "time", "depth": "depth",
    "name": "name", "title": "title", "price": "price", "description": "description",
    "reviews": "reviews", "date": "date", "text": "(?:text|review)", "method": "method",
    "path": "path", "summary": "summary", "family": "family", "financing": "financing", "shipping": "shipping"}


def _key(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).casefold()).strip()


_CARD_BOUNDARY = "\ue000"
_MEDIA_BOUNDARY = "\ue001"


def _unwrap_links(text, schema):
    """Unwrap balanced inline links, including multiline card labels.

    Only explicit markup creates boundaries; text from separate cards is never
    flattened together. Image alt text is not a substitute for a visible title.
    """
    pairs = markdown_delimiter_pairs(text)
    output, i = [], 0
    while i < len(text):
        image = is_markdown_image_start(text, i)
        start = i + 1 if image else i
        if text[start:start + 1] != "[":
            output.append(text[i])
            i += 1
            continue
        end = pairs.get(start)
        destination = end + 1 if end is not None else len(text)
        close = pairs.get(destination) if text[destination:destination + 1] == "(" else None
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
            elif image and not prefix.strip():
                line_end = text.find("\n", close + 1)
                suffix = text[close + 1:line_end if line_end >= 0 else len(text)]
                if not suffix.strip():
                    # A standalone image is an explicit local card boundary.
                    # Keep it as ownership evidence without treating alt text as
                    # the record name or joining prose across adjacent cards.
                    output.append(_MEDIA_BOUNDARY)
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
            if schema in {"product", "catalog_product", "earthquake"} and "\n" in text[start + 1:end]:
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


def _visible(text, schema=None, *, normalized_html=False):
    from .reference_quality import is_html_document_or_fragment

    projected_html = (
        not normalized_html and schema in SCHEMA_FIELDS
        and schema not in {"named_link", "linked_product", "table_row", "body_text", "page_identity"}
        and is_html_document_or_fragment(text)
    )
    if projected_html:
        from .dynamic_lists import visible_html_text

        text = visible_html_text(text)
    if schema == "name_description" and not normalized_html and not projected_html and re.search(r"<(?:div|section|article|span|script|template)\b", text, re.I):
        from .dynamic_lists import _scoped_html
        text = _scoped_html(text).get_text(separator="\n")
        projected_html = True
    text = clean_reference_text(text)
    # An actual inline HTML break in Markdown is layout. Preserve escaped
    # markup and code spans, and decode character references only afterward.
    text = re.sub(r'(`+|~{3,}).*?\1|(?<!\\)<br\s*/?>' if schema != "table_row" and not normalized_html else r'(`+|~{3,}).*?\1',
                  lambda m: m.group(0) if m.group(1) else " ", text,
                  flags=re.I | re.S)
    # Decode one character-reference layer, as a browser does for both HTML
    # and Markdown. In particular, ``&amp;`` is the visible ``&`` while an
    # intentionally escaped ``&amp;amp;`` remains the literal text ``&amp;``.
    if not projected_html and not normalized_html:
        text = unescape(text)
    # Extractors represent Markdown hard breaks using one or two backslashes.
    text = re.sub(r"\\{1,2}[ \t]*(?=\n)", "", text)
    text = _unwrap_links(text, schema)
    # CommonMark punctuation escapes preserve the same visible characters.
    # Remove them before date/field parsing (for example ``2023\-05\-18``).
    text = re.sub(r'\\([!"#$%&\'()*+,\-./:;<=>?@\[\]\\^_`{|}~])', lambda m: m[0] if schema == 'table_row' and m[1] in {'|', '\\'} else m[1], text)
    if schema == "name_description":
        # Image-only card links intentionally contribute no visible title.
        # Remove their now-empty bullet line so blank-delimited card parsing
        # sees the following visible title and description as one record.
        text = re.sub(r"(?m)^\s*(?:[-*+]|\d+[.)])\s*$", "", text)
        text = re.sub(rf"(?m)^\s*(?:[-*+]|\d+[.)])\s*{_CARD_BOUNDARY}\s*$",
                      f"\n{_CARD_BOUNDARY}\n", text)
        boundaries = {_CARD_BOUNDARY, _MEDIA_BOUNDARY}
        text = "\n".join(line if line.strip() in boundaries
                         else line.replace(_CARD_BOUNDARY, "").replace(_MEDIA_BOUNDARY, "")
                         for line in text.splitlines())
    text = re.sub(r"\*\*|__|`", "", text)
    # Link unwrapping may insert boundary spaces inside surrounding emphasis
    # (``*[label](url)*`` becomes ``* label *``). Treat that as the same visible
    # text as ordinary emphasis, without flattening other punctuation.
    text = re.sub(r"(?<!\w)([*_])\s*([^\n]*?\S)\s*\1(?!\w)", r"\2", text)
    # Preserve newlines: flattening them would let one card borrow another's fields.
    return text.replace("\r\n", "\n")


def _line(text):
    return re.sub(r"^(?:\s*[#>]+\s*|\s*[-*+]\s+|\s*\d+[.)]\s+)", "", text).strip()


def _field(text, name):
    # Formatting tags sometimes survive a Markdown dump inside a visible
    # scalar value (for example, ``or 9 x<strong>$1.21</strong>``). They do
    # not change the field's displayed text. Strip only simple inline emphasis
    # tags; keep every other tag/field boundary available for validation.
    visible = re.sub(r"</?(?:strong|b|em|i)>", "", text, flags=re.I)
    return re.sub(rf"^(?:{_LABELS.get(name, name)})\s*:\s*", "", visible, flags=re.I).strip()


def _text(value, minimum=2):
    return (len(value.strip()) >= minimum and any(c.isalpha() for c in value)
            and not _PLACEHOLDER.fullmatch(value.strip()) and not re.match(r"https?://", value)
            and not re.fullmatch(_MONEY, value, re.I))


def _valid_field(name, value):
    if not isinstance(value, str) or not value.strip():
        return False
    if name == "price":
        return bool(re.fullmatch(_MONEY, value, re.I))
    if name == "magnitude":
        return bool(re.fullmatch(r"-?\d{1,2}(?:\.\d+)?", value))
    if name == "depth":
        return bool(re.fullmatch(r"-?\d+(?:\.\d+)?\s*km", value, re.I))
    if name == "time":
        # Accept source-local UTC offsets and ISO timestamps without freezing
        # a date. A date alone is not an event timestamp.
        normalized = re.sub(r"\s*\(UTC([+-]\d{2}:\d{2})\)$", r"\1", value)
        normalized = re.sub(r"\s*\(UTC\)$", "+00:00", normalized)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?", normalized):
            return False
        try:
            datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            return True
        except ValueError:
            return False
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


def _product_records(text, schema):
    """Parse product cards without erasing explicit record ownership.

    A blank-line block is a record boundary only when every nonempty block is
    independently a single complete card.  This preserves extractors that put
    an optional badge before the product name, while retaining the permissive
    line-layout parser for prose/headings that use blank lines decoratively.
    """
    records = []
    for marked_block in text.split(_CARD_BOUNDARY):
        blocks = [block for block in re.split(r"\n\s*\n", marked_block)
                  if block.strip()]
        parsed = [
            _products([_line(line) for line in block.splitlines() if line.strip()], schema)
            for block in blocks
        ]
        if (len(parsed) > 1
                and all(len(block_records) == 1 and block_records[0]["valid"]
                        for block_records in parsed)):
            for block_records in parsed:
                records.extend(block_records)
        else:
            records.extend(_products(
                [_line(line) for line in marked_block.splitlines() if line.strip()], schema
            ))
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
            if re.match(r'(?:#{1,6}\s*)?(?:loading[. …]*|quotes by\s*:|made with\b|'
                        r'(?:next|previous)(?:\s+(?:page\s*)?\d+)?\s*[→←»«]?\s*$|'
                        r'page\s+\d+(?:\s+of\s+\d+)?\s*$)',
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


def _closed_title_descriptions(text):
    """Treat title-description-title triples as explicitly closed cards."""
    paragraphs = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    records = []
    for first, description, repeated in zip(
        paragraphs, paragraphs[1:], paragraphs[2:], strict=False
    ):
        if any("\n" in value for value in (first, description, repeated)):
            continue
        name = _line(first)
        if _key(name) != _key(_line(repeated)):
            continue
        record = _record(
            {"name": name, "description": _line(description)},
            "name_description",
        )
        if record["valid"]:
            records.append(record)
    return records


def _media_closed_title_descriptions(text):
    """Read title-description-title closures only inside image-owned blocks."""
    if _MEDIA_BOUNDARY not in text:
        return []
    return [
        record
        for block in text.split(_MEDIA_BOUNDARY)[1:]
        for record in _closed_title_descriptions(block)
    ]


def _earthquakes(text):
    """Read contiguous magnitude/location/time/depth cards, never bag-of-fields.

    Four-line ordering is independently observed on the source list. Explicit
    Markdown card/link boundaries cannot be crossed to repair a missing field.
    Tables are handled separately with column ownership.
    """
    records = []
    for block in text.split(_CARD_BOUNDARY):
        lines = [_line(line) for line in block.splitlines() if line.strip()]
        # Inline custom elements can serialize location and timestamp on one
        # line. Split only a complete trailing timestamp, within this card.
        separated = []
        for line in lines:
            compact = re.fullmatch(
                r"(.+?)(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
                r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}|\s*\(UTC(?:[+-]\d{2}:\d{2})?\))?)"
                r"\s+(-?\d+(?:\.\d+)?\s*km)", line, re.I,
            )
            if (compact and _text(compact[1])
                    and _valid_field("time", compact[2])
                    and _valid_field("depth", compact[3])):
                separated.extend((compact[1].strip(), compact[2], compact[3]))
                continue
            joined = re.fullmatch(
                r"(.+?)(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
                r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}|\s*\(UTC(?:[+-]\d{2}:\d{2})?\))?)",
                line,
            )
            if joined and _valid_field("time", joined[2]) and _text(joined[1]):
                separated.extend((joined[1].strip(), joined[2]))
            else:
                separated.append(line)
        lines = separated
        for i, line in enumerate(lines):
            magnitude = _field(line, "magnitude")
            if not _valid_field("magnitude", magnitude):
                continue
            fields = {"magnitude": magnitude}
            for offset, name in enumerate(("location", "time", "depth"), 1):
                if i + offset < len(lines):
                    fields[name] = _field(lines[i + offset], name)
            records.append(_record(fields, "earthquake"))
    return records


def _parse_records(visible, schema, *, allow_closed_title_cards=False):
    raw_lines = visible.splitlines()
    records, occupied = _table_records(raw_lines, schema)
    remainder = "\n".join(line if i not in occupied else "" for i, line in enumerate(raw_lines))
    lines = [_line(line) for line in remainder.splitlines() if line.strip()]
    if schema == "earthquake":
        records.extend(_earthquakes(remainder))
    elif schema in {"product", "catalog_product"}:
        records.extend(_product_records(remainder, schema))
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
        # Scoped title-description-title cards and image-owned cards are two
        # ways to establish the same ownership boundary.  When the caller has
        # already bounded the target section, parsing both routes would emit
        # every image-backed card twice and reject an otherwise valid answer as
        # a duplicate-name list.
        closed = (
            _closed_title_descriptions(remainder)
            if allow_closed_title_cards
            else _media_closed_title_descriptions(remainder)
        )
        weak = _descriptions(remainder)
        for record in weak:
            description = _key(record["fields"].get("description", ""))
            name = _key(record["fields"].get("name", ""))
            if any(
                name != _key(owner["fields"]["name"])
                and _key(owner["fields"]["name"]) in description
                and _key(owner["fields"]["description"]) in description
                for owner in closed
            ):
                record["valid"] = False
                record["errors"].append("description_contains_separate_owned_record")
        records.extend(weak)
        records.extend(closed)
    elif schema == "taxonomy_family":
        records.extend(_record({"family": _field(line, "family")}, schema) for line in lines
                       if re.fullmatch(r"(?:family\s*:\s*)?[A-Za-z]+idae", line, re.I))
    return records


def _non_record_visible(content, contract, records, base_url):
    """Remove owned record links, without erasing identical navigation text."""
    from urllib.parse import urljoin

    from .dynamic_lists import _scoped_html, named_entry_identity
    from .reference_quality import markdown_links, visible_markdown_links

    identities = {named_entry_identity(r['fields']['url'], contract)
                  for r in records if r.get('valid') and r.get('fields', {}).get('url')}

    def is_record(url):
        try:
            absolute = urljoin(base_url, url)
            if urlsplit(absolute).scheme not in {'http', 'https'} or not urlsplit(absolute).hostname:
                return False
            return named_entry_identity(absolute, contract) in identities
        except ValueError:
            return False

    if re.search(r'<(?:html|body|main|div|section|article|a|p)\b', content, re.I):
        soup = _scoped_html(content)
        for node in list(soup.select('a[href]')):
            if node.parent is None or node.attrs is None:
                continue
            if is_record(node['href']):
                node.decompose()
        return _visible(str(soup))
    links = markdown_links(content)
    if links:
        for link in reversed(links):
            if is_record(link.destination):
                content = content[:link.start] + ' ' + content[link.end:]
        # A linked logo can be the only reader-visible site identity. Preserve
        # its alt text for page-level anchors after owned record links are
        # removed. Record parsing still uses ``_visible`` above, so image alt
        # text cannot manufacture a named-link record title.
        return _visible(visible_markdown_links(content))
    # Plain text has no URL ownership; retain the conservative fallback.
    return None


def _stable_literal_match(surface, value, registered_words=()):
    """An atomic Latin label is a word, not part of another word.

    Native camel boundaries and fully covered chains of registered labels
    preserve adjacent UI text without accepting arbitrary word fragments.
    Longer declared text retains the existing exact-phrase containment rule.
    """
    from .metrics import fact_in_text, normalize_for_match
    expected = normalize_for_match(_visible(value))
    folded = normalize_for_match(surface)
    if not fact_in_text(folded, expected):
        return False
    if not re.fullmatch(r"[A-Za-z]+", value):
        return True
    boundary = lambda needle, text: bool(re.search(
        r"(?<![a-z])" + re.escape(needle) + r"(?![a-z])", text))
    if boundary(expected, folded):
        return True
    camel = lambda text: re.sub(
        r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    if boundary(normalize_for_match(camel(value)),
                normalize_for_match(camel(surface))):
        return True
    labels = {normalize_for_match(word) for word in registered_words}
    for token in set(re.findall(r"[a-z]+", folded)):
        if expected not in token:
            continue
        states = {(0, False)}
        for position in range(len(token)):
            for start, found in tuple(states):
                if start != position:
                    continue
                for label in labels:
                    if token.startswith(label, position):
                        states.add((position + len(label), found or label == expected))
        if (len(token), True) in states:
            return True
    return False


def _anchor_matches(anchor, records, visible, schema, non_record_visible=None,
                    anchor_visible=None, registered_words=()):
    from .metrics import normalize_for_match
    if anchor.get('match_scope') == 'non_record_lines':
        if non_record_visible is not None:
            folded = normalize_for_match(non_record_visible)
            return [value for value in anchor['alternatives']
                    if _stable_literal_match(non_record_visible, value, registered_words)]
        # A known record label cannot stand in for page context. This is a
        # conservative ownership rule, not a claim of recovered DOM ownership.
        # Flattened page output can put headings and records on one physical
        # line, so mask parsed record titles instead of discarding that line.
        titles = [normalize_for_match(r['fields']['title']) for r in records if r.get('valid') and r.get('fields',{}).get('title')]
        folded = normalize_for_match(anchor_visible if anchor_visible is not None else visible)
        for title in sorted(set(titles), key=len, reverse=True):
            folded = folded.replace(title, ' ')
        return [value for value in anchor["alternatives"]
                if _stable_literal_match(folded, value, registered_words)]
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
            # Name/description card ownership provides a structural boundary
            # even if an inline DOM separator is omitted. Quote tags are a
            # set-like field, so their separators must remain observable.
            compact = schema == "name_description"
            return (actual.replace(" ", "") == expected.replace(" ", "")
                    if compact else actual == expected)
        return [value for value, fields in owned if any(record["valid"] and all(
            field_matches(record, fields, field)
            for field in SCHEMA_FIELDS[schema]) for record in records)]
    folded = normalize_for_match(anchor_visible if anchor_visible is not None else visible)
    return [value for value in anchor["alternatives"]
            if _stable_literal_match(anchor_visible if anchor_visible is not None else visible,
                                     value, registered_words)]


def validate_acceptance_contract(contract: dict) -> None:
    """Fail closed before a caller chooses stable scoring instead of snapshots."""
    if (not isinstance(contract, dict) or type(contract.get("version")) is not int
            or contract.get("version") != 1 or not isinstance(contract.get("schema"), str)
            or contract.get("schema") not in SCHEMA_FIELDS):
        raise ValueError("Unsupported stable acceptance contract")
    if set(contract) - {"version", "schema", "minimum_records", "maximum_records", "page_patterns", "stable_text_anchors", "stable_text_role", "hosts", "entry_path", "entry_identity_pattern", "entry_context", "entry_scope", "name_validation", "identity_query_keys", "field_minimum_records", "record_name_exclusions", "title_exclusion_patterns", "price_patterns", "price_exclusion_patterns", "price_unavailable_patterns", "record_count_policy", "source_record_pair_hashes", "source_record_pairs_sha256", "table_columns", "record_identity_fields", "table_scope", "duplicate_policy", "link_base_url", "text_exclusions", "content_scope"}:
        raise ValueError("Unknown stable acceptance contract fields")
    if contract.get("stable_text_role", "required") not in {"required", "diagnostic"}:
        raise ValueError("Invalid stable text role")
    if contract.get("stable_text_role") == "diagnostic" and contract["schema"] != "body_text":
        raise ValueError("Diagnostic stable text requires a body-text contract")
    if contract.get('content_scope', 'legacy') not in {'legacy', 'target_content', 'full_page_stable_coverage'}:
        raise ValueError('Invalid stable content scope')
    if contract.get('content_scope') == 'target_content' and any(
            re.search(r'(^|[ >])(nav|footer)([ >.#:]|$)', a.get('source_location', ''), re.I)
            for a in contract.get('stable_text_anchors', [])):
        raise ValueError('Excluded navigation/footer cannot supply target-content anchors')
    exclusions = contract.get('text_exclusions', {})
    if (not isinstance(exclusions, dict)
            or set(exclusions) - {'html_selectors', 'text_patterns'}):
        raise ValueError('Invalid stable text exclusions')
    for key, values in exclusions.items():
        if (not isinstance(values, list)
                or any(not isinstance(value, str) or not value.strip() for value in values)):
            raise ValueError('Invalid stable text exclusions')
        for value in values:
            if key == 'text_patterns':
                re.compile(value)
            else:
                import soupsieve
                soupsieve.compile(value)
    if contract['schema'] == 'table_row':
        from .structured_tables import validate_table
        validate_table(contract)
    elif any(key in contract for key in ('table_columns', 'record_identity_fields', 'table_scope')):
        raise ValueError('Table declarations apply only to table-row contracts')
    field_counts = contract.get('field_minimum_records', {})
    if not isinstance(field_counts, dict) or any(k not in _LABELS or type(v) is not int or v < 1 for k,v in field_counts.items()):
        raise ValueError('Invalid required field counts')
    minimum, maximum = contract.get("minimum_records"), contract.get("maximum_records")
    if contract["schema"] == "body_text":
        if minimum != 0 or maximum not in {None, 0} or field_counts:
            raise ValueError("Stable body-text contracts do not declare records")
    elif contract["schema"] == "page_identity":
        if minimum != 0 or maximum not in {None, 0} or field_counts:
            raise ValueError("Page identity contracts do not declare records")
    elif type(minimum) is not int or minimum < 1 or maximum is not None and (type(maximum) is not int or maximum < minimum):
        raise ValueError("Stable contract requires positive bounded record counts")
    if 'duplicate_policy' in contract and (contract['schema'] != 'table_row'
            or contract['duplicate_policy'] != 'deduplicate_identical'):
        raise ValueError('Invalid duplicate policy')
    count_policy = contract.get("record_count_policy")
    if count_policy is not None:
        allowed_policy_fields = {
            "list_type", "source_observed_count", "required_ratio",
            "minimum_records", "source_observation_counts",
            "source_evidence_sha256", "reason",
        }
        required_policy_fields = allowed_policy_fields - {"minimum_records"}
        if (contract["schema"] in {"body_text", "page_identity"}
                or not isinstance(count_policy, dict)
                or not required_policy_fields <= set(count_policy) <= allowed_policy_fields
                or count_policy.get("list_type") not in {"fixed", "variable"}
                or type(count_policy.get("source_observed_count")) is not int
                or count_policy["source_observed_count"] < 1
                or type(count_policy.get("required_ratio")) not in {int, float}
                or not 0 < count_policy["required_ratio"] <= 1
                or not isinstance(count_policy.get("source_observation_counts"), list)
                or len(count_policy["source_observation_counts"]) < 2
                or any(type(value) is not int or value < 1
                       for value in count_policy["source_observation_counts"])
                or not isinstance(count_policy.get("source_evidence_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", count_policy["source_evidence_sha256"])
                or not isinstance(count_policy.get("reason"), str)
                or not count_policy["reason"].strip()):
            raise ValueError("Invalid record count policy")
        expected_ratio = 1.0 if count_policy["list_type"] == "fixed" else 0.8
        expected_minimum = math.ceil(
            count_policy["source_observed_count"] * count_policy["required_ratio"])
        if (count_policy["required_ratio"] != expected_ratio
                or minimum != expected_minimum
                or count_policy.get("minimum_records", minimum) != minimum):
            raise ValueError("Record minimum does not match the frozen source policy")
    patterns = contract.get("page_patterns", [])
    if not isinstance(patterns, list) or any(not isinstance(p, str) or not p for p in patterns):
        raise ValueError("Invalid stable page patterns")
    anchors = contract.get("stable_text_anchors", [])
    if not isinstance(anchors, list):
        raise ValueError("Stable text anchors must be explicitly declared groups")
    ids = set()
    for anchor in anchors:
        if (not isinstance(anchor, dict) or not {"id", "alternatives"} <= set(anchor)
                or set(anchor) - {
                    "id", "alternatives", "match_scope", "source_location",
                    "observation_status", "source_evidence_sha256",
                }
                or ('match_scope' in anchor and (anchor['match_scope'] != 'non_record_lines' or contract['schema'] != 'named_link'))
                or ('source_location' in anchor and (
                    not isinstance(anchor['source_location'], str)
                    or not anchor['source_location'].strip()))
                or ('observation_status' in anchor and (
                    not isinstance(anchor['observation_status'], str)
                    or not anchor['observation_status'].strip()))
                or ('source_evidence_sha256' in anchor and (
                    not isinstance(anchor['source_evidence_sha256'], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", anchor['source_evidence_sha256'])))
                or not isinstance(anchor.get("id"), str) or not anchor["id"].strip() or anchor["id"] in ids
                or not isinstance(anchor.get("alternatives"), list) or not anchor["alternatives"]
                or any(not isinstance(value, str) or not _visible(value).strip() for value in anchor["alternatives"])):
            raise ValueError("Invalid or duplicate stable text anchor group")
        ids.add(anchor["id"])
    if contract["schema"] == "body_text" and not anchors and not patterns:
        raise ValueError("Stable body-text contracts require text anchors or page patterns")
    if contract["schema"] in {"named_link", "linked_product"}:
        hosts = contract.get("hosts")
        if (not isinstance(hosts, list) or not hosts
                or any(not isinstance(host, str) or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", host) for host in hosts)
                or not isinstance(contract.get("entry_path"), str) or not contract["entry_path"]):
            raise ValueError("Linked records require allowed hosts and detail path pattern")
        if contract.get("name_validation", "descriptive") not in {"descriptive", "nonempty"}:
            raise ValueError("Invalid record name validation policy")
        scope = contract.get("entry_scope")
        if scope is not None:
            if (not isinstance(scope, dict) or not scope.get("html_selector")
                    or set(scope) - {"html_selector", "start_pattern", "end_pattern", "title_selector", "link_selector", "price_selector", "auxiliary_links_end_pattern", "markdown_title_identity_pattern", "markdown_detail_identity_pattern", "markdown_label_pattern", "markdown_card_title_pattern", "markdown_title_column", "markdown_link_column"}
                    or any(not isinstance(v, str) or not v.strip() for v in scope.values())
                    or (("title_selector" in scope) != ("link_selector" in scope))):
                raise ValueError("Invalid linked record section scope")
            import soupsieve
            soupsieve.compile(scope["html_selector"])
            for key in ("title_selector", "link_selector", "price_selector"):
                if key in scope:
                    soupsieve.compile(scope[key])
            for key in ("start_pattern", "end_pattern", "auxiliary_links_end_pattern"):
                if key in scope:
                    compiled = re.compile(scope[key], re.M)
                    if compiled.search(""):
                        raise ValueError("Record section boundary cannot match empty text")
            column_keys = ("markdown_title_column", "markdown_link_column")
            if any(key in scope for key in column_keys):
                if (not all(key in scope for key in column_keys)
                        or scope[column_keys[0]] == scope[column_keys[1]]
                        or contract["schema"] != "named_link"):
                    raise ValueError("Named table records require distinct title and link columns")
            pair_keys = ("markdown_title_identity_pattern", "markdown_detail_identity_pattern")
            if 'markdown_label_pattern' in scope:
                pattern = re.compile(scope['markdown_label_pattern'])
                if 'label' not in pattern.groupindex or pattern.search(''):
                    raise ValueError('Markdown card label requires a nonempty named label capture')
            if 'markdown_card_title_pattern' in scope:
                pattern = re.compile(scope['markdown_card_title_pattern'])
                if ('title' not in pattern.groupindex or pattern.search('')
                        or not scope.get('start_pattern') or not scope.get('end_pattern')):
                    raise ValueError(
                        'Markdown heading cards require bounded scope and a nonempty title capture'
                    )
            if any(key in scope for key in pair_keys):
                if not all(key in scope for key in pair_keys):
                    raise ValueError("Markdown row identity patterns must be paired")
                for key in pair_keys:
                    pattern = re.compile(scope[key])
                    if pattern.groups != 1 or pattern.search(""):
                        raise ValueError("Markdown row identity requires one nonempty capture")
        pair_hashes = contract.get("source_record_pair_hashes")
        pair_set_sha256 = contract.get("source_record_pairs_sha256")
        if pair_hashes is not None or pair_set_sha256 is not None:
            if (not isinstance(pair_hashes, list) or not pair_hashes
                    or pair_hashes != sorted(set(pair_hashes))
                    or any(not isinstance(value, str)
                           or not re.fullmatch(r"[0-9a-f]{64}", value)
                           for value in pair_hashes)
                    or not isinstance(pair_set_sha256, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", pair_set_sha256)
                    or hashlib.sha256("\n".join(pair_hashes).encode()).hexdigest()
                    != pair_set_sha256):
                raise ValueError("Invalid frozen source name-link pairs")
        if contract.get('entry_context', 'list_or_card') != 'list_or_card':
            raise ValueError('Named-link entry context must be list_or_card')
        patterns = [*patterns, contract["entry_path"]]
    if contract["schema"] == "named_link":
        exclusions = contract.get('record_name_exclusions', [])
        if (not isinstance(exclusions, list)
                or any(not isinstance(value, str) or not _key(value) for value in exclusions)
                or len({_key(value) for value in exclusions}) != len(exclusions)):
            raise ValueError('Invalid or duplicate record-name exclusions')
        identity_pattern = contract.get('entry_identity_pattern')
        if identity_pattern is not None:
            if not isinstance(identity_pattern, str) or not identity_pattern:
                raise ValueError('Invalid named-link identity pattern')
            patterns.append(identity_pattern)
        title_exclusions = contract.get('title_exclusion_patterns', [])
        if (not isinstance(title_exclusions, list)
                or any(not isinstance(pattern, str) or not pattern
                       for pattern in title_exclusions)):
            raise ValueError('Invalid named-link title exclusion patterns')
        patterns.extend(title_exclusions)
        if "link_base_url" in contract:
            value = contract["link_base_url"]
            try:
                base = urlsplit(value) if isinstance(value, str) else None
                valid = (base and base.scheme in {"http", "https"}
                         and base.hostname in hosts and not base.username
                         and not base.password and not base.query
                         and not base.fragment
                         and not any(char.isspace() for char in value))
                if base:
                    base.port
            except ValueError:
                valid = False
            if not valid:
                raise ValueError("Link base must be an observed HTTP(S) URL on an allowed host")
    elif contract["schema"] not in {"linked_product"} and (
            "hosts" in contract or "entry_path" in contract
            or "record_name_exclusions" in contract
            or "title_exclusion_patterns" in contract
            or "identity_query_keys" in contract or "link_base_url" in contract):
        raise ValueError("Host/path contracts apply only to linked records")
    query_keys = contract.get("identity_query_keys", [])
    if (not isinstance(query_keys, list)
            or any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.~-]+", key)
                   for key in query_keys)):
        raise ValueError("Invalid linked-record identity query keys")
    if query_keys and contract["schema"] not in {"named_link", "linked_product"}:
        raise ValueError("Identity query keys apply only to linked records")
    if contract["schema"] == "linked_product":
        price_patterns = contract.get("price_patterns")
        price_exclusions = contract.get("price_exclusion_patterns", [])
        if (not isinstance(price_patterns, list) or not price_patterns
                or any(not isinstance(pattern, str) or not pattern for pattern in price_patterns)
                or not isinstance(price_exclusions, list)
                or any(not isinstance(pattern, str) or not pattern for pattern in price_exclusions)):
            raise ValueError("Linked products require explicit price and exclusion patterns")
        unavailable = contract.get("price_unavailable_patterns", [])
        if (not isinstance(unavailable, list)
                or any(not isinstance(p, str) or not p.strip() or re.compile(p).search("")
                       for p in unavailable)):
            raise ValueError("Invalid explicit price-unavailability patterns")
        patterns = [*patterns, *price_patterns, *price_exclusions, *unavailable]
    elif any(k in contract for k in ("price_patterns", "price_exclusion_patterns", "price_unavailable_patterns")):
        raise ValueError("Price patterns apply only to linked products")
    if contract["schema"] == "page_identity" and not anchors:
        raise ValueError("Page identity contracts require stable text anchors")
    try:
        for pattern in patterns:
            compiled = re.compile(pattern)
            if pattern == contract.get('entry_identity_pattern') and compiled.groups != 1:
                raise ValueError('Named-link identity pattern must have one capture group')
    except re.error as error:
        raise ValueError("Invalid stable acceptance regex") from error


def evaluate_stable_task(content: str, contract: dict, base_url: str = "", eligible: bool = True, links=None, *, anchor_text: str | None = None, anchors_required: bool = True, allow_closed_title_cards: bool = False) -> dict:
    """Validate a version-1 schema/count contract, without fetching or snapshots.

    Required keys: version, schema, minimum_records. Optional maximum_records
    imposes a finite exact scope when equal to the minimum. page_patterns is a
    list of required regexes for stable page/section identity (not current items).
    stable_text_anchors contains explicit {id, alternatives} groups; every group
    must match unless the caller supplies a separate body or composite contract
    and selects diagnostic anchors. Record and page-pattern gates remain required.
    named_link additionally requires hosts and entry_path, using the existing
    same-site detail-link parser. Raw URL identity is not factual verification.
    anchor_text may reuse the caller's normalized visible HTML prose;
    document-title metadata cannot supply page text or structural records.
    """
    validate_acceptance_contract(contract)
    schema = contract["schema"]
    count_policy = contract.get("record_count_policy")
    minimum, maximum = contract["minimum_records"], contract.get("maximum_records")
    patterns = contract.get("page_patterns", [])
    visible = _visible(content, schema)
    # HTML normalization can also decode entities in Markdown. Preserve its
    # explicit link boundaries before matching atomic page labels.
    anchor_visible = _visible(anchor_text, schema, normalized_html=True) if anchor_text is not None else None
    if anchor_visible is None:
        html_candidate = content.lstrip()
        first_line, newline, remainder = html_candidate.partition('\n')
        # Legacy callers combine a metadata title and HTML. Recognize the
        # following document, not a guessed title-length limit. Keep metadata
        # as text rather than interpreting angle brackets in a title as HTML.
        if (newline and re.match(r'\s*(?:<!doctype\s+html\b|<(?:html|body|main|div|section|article)\b)',
                                  remainder, re.I)
                and not re.match(r'(?:<!doctype\s+html\b|<(?:html|body|main|div|section|article)\b)',first_line,re.I)):
            html_candidate = remainder.lstrip()
        if re.match(r'(?:<!doctype\s+html\b|<(?:html|body|main|div|section|article)\b)',
                    html_candidate, re.I):
            from .dynamic_lists import visible_html_text
            anchor_visible = visible_html_text(html_candidate)
    exclusions = dict(contract.get('text_exclusions', {}))
    if contract.get('content_scope') == 'target_content':
        exclusions['html_selectors'] = ['nav', 'footer', *exclusions.get('html_selectors', [])]
    if exclusions:
        if exclusions.get('html_selectors') and re.search(r'<[a-z][^>]*>', content, re.I):
            from bs4 import BeautifulSoup
            from .dynamic_lists import visible_html_text
            soup = BeautifulSoup(content, 'html.parser')
            for selector in exclusions['html_selectors']:
                for element in soup.select(selector):
                    element.decompose()
            anchor_visible = visible_html_text(str(soup))
        else:
            anchor_visible = anchor_visible if anchor_visible is not None else visible
        for pattern in exclusions.get('text_patterns', []):
            anchor_visible = re.sub(pattern, ' ', anchor_visible, flags=re.I | re.M)
    text_surface = anchor_visible if anchor_visible is not None else visible
    page_results = [{"pattern": p, "pass": bool(re.search(p, text_surface, re.I | re.M))} for p in patterns]
    content_errors = (["page_identity_mismatch"] if not all(r["pass"] for r in page_results) else [])
    from .reference_quality import blocked_page_hits
    blocked_page = bool(blocked_page_hits(text_surface))
    errors = []
    if schema in {"named_link", "linked_product"}:
        link_diagnostics = []
        try:
            if schema == "linked_product":
                from .dynamic_lists import extract_linked_products
                entries = extract_linked_products(
                    content, contract, base_url, diagnostics=link_diagnostics,
                )
            else:
                entries = extract_named_entries(
                    content, contract, base_url, links, link_diagnostics,
                )
        except ValueError:
            # Malformed returned URLs are output validation failures, not
            # malformed contracts and not exceptions that abort a whole run.
            entries = []
            errors.append("invalid_link_output")
        errors.extend(error for error in link_diagnostics if error not in errors)
        excluded_names = ({_key(value) for value in contract.get("record_name_exclusions", [])}
                          if schema == "named_link" else set())
        excluded_entries = [entry for entry in entries if _key(entry["title"]) in excluded_names]
        entries = [entry for entry in entries if _key(entry["title"]) not in excluded_names]
        source_pair_hashes = set(contract.get("source_record_pair_hashes", []))
        source_mismatches = []
        if source_pair_hashes:
            matched_entries = []
            for entry in entries:
                pair_hash = named_entry_source_pair_hash(
                    entry["title"], entry["url"], contract)
                if pair_hash in source_pair_hashes:
                    matched_entries.append(entry)
                else:
                    source_mismatches.append({"title": entry["title"], "url": entry["url"],
                                              "pair_sha256": pair_hash})
            entries = matched_entries
            if source_mismatches:
                errors.append("source_record_mismatch")
        records = []
        for entry in entries:
            parts = urlsplit(entry["url"])
            if (parts.scheme not in {"http", "https"} or not parts.hostname
                    or parts.username or parts.password):
                continue
            records.append({"fields": entry, "valid": True, "errors": []})
    else:
        excluded_entries = []
        source_mismatches = []
        if schema == 'table_row':
            from .structured_tables import parse_table_records
            records = parse_table_records(content, visible, contract)
        else:
            records = (
                []
                if schema in {"body_text", "page_identity"}
                else _parse_records(
                    visible,
                    schema,
                    allow_closed_title_cards=allow_closed_title_cards,
                )
            )
    anchors = []
    non_record_visible = None
    if schema == 'named_link' and any(a.get('match_scope') == 'non_record_lines'
                                      for a in contract.get('stable_text_anchors', [])):
        non_record_visible = _non_record_visible(content, contract, records, base_url)
    registered_words = tuple(value for anchor in contract.get("stable_text_anchors", [])
                             for value in anchor["alternatives"]
                             if re.fullmatch(r"[A-Za-z]+", value))
    for anchor in contract.get("stable_text_anchors", []):
        matched = _anchor_matches(anchor, records, visible, schema,
                                  non_record_visible, anchor_visible, registered_words)
        anchors.append({**anchor, "matched": matched, "pass": bool(matched)})
    anchor_errors = ["missing_stable_anchor:" + r["id"] for r in anchors if not r["pass"]]
    if anchors_required:
        content_errors += anchor_errors
    complete = [r for r in records if r["valid"]]
    identity_fields = contract["record_identity_fields"] if schema == "table_row" else UNIQUE_FIELDS[schema]
    compact_fields = {c['name'] for c in contract.get('table_columns', [])
                      if c.get('compact_whitespace')}
    def identity_key(record, field):
        value = _key(record['fields'][field])
        return re.sub(r'\s+', '', value) if field in compact_fields else value
    unique = {tuple(identity_key(r, field) for field in identity_fields) for r in complete}
    record_warnings = []
    field_checks = []
    for field, count in contract.get('field_minimum_records', {}).items():
        identities = {tuple(_key(r['fields'][key]) for key in identity_fields)
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
        grouped = {}
        for record in complete:
            identity = tuple(identity_key(record, field) for field in identity_fields)
            grouped.setdefault(identity, set()).add(tuple(
                (field, _key(value)) for field, value in sorted(record['fields'].items())))
        if (contract.get('duplicate_policy') == 'deduplicate_identical'
                and all(len(values) == 1 for values in grouped.values())):
            record_warnings.append('identical_records_counted_once')
        else:
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
            "stable_anchors_required": anchors_required,
            "stable_anchor_errors": anchor_errors, "stable_anchor_results": anchors, "stable_page_pattern_results": page_results,
            "stable_anchor_hits": sum(r["pass"] for r in anchors),
            "stable_anchor_total": len(anchors),
            "stable_anchor_coverage": sum(r["pass"] for r in anchors) / len(anchors) if anchors else None,
            "stable_text_scope": "declared_text_groups" if anchors else "not_annotated",
            "stable_record_count": len(records), "stable_complete_record_count": len(complete),
            "stable_unique_record_count": len(unique), "stable_minimum_records": minimum,
            "stable_record_list_type": count_policy.get("list_type") if count_policy else None,
            "stable_source_observed_record_count": count_policy.get("source_observed_count") if count_policy else None,
            "stable_required_record_ratio": count_policy.get("required_ratio") if count_policy else None,
            "stable_record_coverage": (len(unique) / count_policy["source_observed_count"]
                                       if count_policy else None),
            "stable_maximum_records": maximum, "stable_task_errors": errors, "stable_records": records,
            "stable_excluded_record_names": [entry["title"] for entry in excluded_entries],
            "stable_record_warnings": record_warnings,
            "stable_source_matched_record_count": (len(records)
                                                     if contract.get("source_record_pair_hashes")
                                                     else None),
            "stable_source_mismatched_record_count": len(source_mismatches),
            "stable_source_mismatched_records": source_mismatches[:20],
            "factual_correctness": None,
            "stable_verification_scope": "Mechanical field/group/count checks; source fidelity and factual values are not verified"}
