"""Composite acceptance contracts for independently scoped page records.

The existing stable-task contract deliberately describes one homogeneous
record set.  Some pages contain several lists which must each meet their own
threshold, or rows whose extra fields must belong to the same record.  This
module adds that composition without teaching the provider or report code
about individual sites.  It is pure scoring code and performs no I/O.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

from .dynamic_lists import extract_named_entries
from .reference_quality import clean_reference_text, markdown_links, visible_markdown_links


def _key(value: str) -> str:
    value = unicodedata.normalize("NFKC", unquote(value)).casefold()
    return re.sub(r"\s+", " ", value).strip(" *#>`")


def _scope(content: str, group: dict) -> str:
    """Return one declared section, failing closed when a boundary is absent."""
    start = group.get("start_pattern")
    end = group.get("end_pattern")
    begin = 0

    def boundary(pattern: str, value: str):
        matches = list(re.finditer(pattern, value, re.I | re.M))
        return next(
            (
                match
                for match in matches
                if re.match(r"\s*#{1,6}\s+", value[value.rfind("\n", 0, match.start()) + 1 :])
            ),
            matches[0] if matches else None,
        )

    if start:
        match = boundary(start, content)
        if not match:
            return ""
        begin = match.start()
    finish = len(content)
    if end:
        match = boundary(end, content[begin:])
        if not match:
            return ""
        finish = begin + match.start()
    return content[begin:finish]


def _validate_group(group: dict) -> None:
    allowed = {
        "id",
        "kind",
        "minimum_records",
        "hosts",
        "entry_path",
        "entry_identity_pattern",
        "identity_query_keys",
        "link_base_url",
        "record_name_exclusions",
        "start_pattern",
        "end_pattern",
        "required_fields",
        "required_columns",
        "identity_column",
        "required_literals",
        "fixed_values",
        "link_column",
    }
    if (
        not isinstance(group, dict)
        or set(group) - allowed
        or not isinstance(group.get("id"), str)
        or not group["id"]
        or group.get("kind")
        not in {
            "named_link",
            "name_description",
            "table",
            "linked_table",
            "literals",
            "field_report",
        }
    ):
        raise ValueError("Invalid composite record group")
    for key in ("start_pattern", "end_pattern"):
        if key in group:
            if not isinstance(group[key], str) or not group[key]:
                raise ValueError("Invalid composite scope pattern")
            re.compile(group[key])
    kind = group["kind"]
    minimum = group.get("minimum_records")
    if kind == "literals":
        literals = group.get("required_literals")
        if (
            minimum is not None
            or not isinstance(literals, list)
            or not literals
            or any(not isinstance(value, str) or not value for value in literals)
        ):
            raise ValueError("Invalid composite literal group")
        return
    if kind == "field_report":
        fixed = group.get("fixed_values", {})
        if (
            minimum is not None
            or not fields_are_valid(group.get("required_fields", []))
            or not isinstance(fixed, dict)
            or any(
                not isinstance(key, str) or not key or not isinstance(value, str) or not value
                for key, value in fixed.items()
            )
        ):
            raise ValueError("Invalid composite field report")
        return
    if type(minimum) is not int or minimum < 1:
        raise ValueError("Composite record groups require a positive minimum")
    if kind in {"named_link", "linked_table"}:
        hosts = group.get("hosts")
        if (
            not isinstance(hosts, list)
            or not hosts
            or any(not isinstance(host, str) or not host for host in hosts)
            or not isinstance(group.get("entry_path"), str)
            or not group["entry_path"]
        ):
            raise ValueError("Composite named links require hosts and entry_path")
        re.compile(group["entry_path"])
        if pattern := group.get("entry_identity_pattern"):
            if re.compile(pattern).groups != 1:
                raise ValueError("Composite link identity must capture once")
    if kind in {"table", "linked_table"}:
        columns = group.get("required_columns")
        if (
            not isinstance(columns, list)
            or not columns
            or any(
                not isinstance(row, dict)
                or set(row) != {"id", "aliases"}
                or not isinstance(row["id"], str)
                or not row["id"]
                or not isinstance(row["aliases"], list)
                or not row["aliases"]
                or any(not isinstance(value, str) or not value for value in row["aliases"])
                for row in columns
            )
            or group.get("identity_column") not in {row["id"] for row in columns}
        ):
            raise ValueError("Invalid composite table columns")
        if kind == "linked_table" and group.get("link_column") not in {
            row["id"] for row in columns
        }:
            raise ValueError("Invalid composite table link column")
    fields = group.get("required_fields", [])
    if not fields_are_valid(fields):
        raise ValueError("Invalid composite required fields")


def fields_are_valid(fields: object) -> bool:
    allowed_field_keys = {"id", "kind", "aliases", "minimum_chars", "pattern"}
    allowed_field_kinds = {"text", "integer", "date_or_time", "price", "area", "version"}
    if not isinstance(fields, list) or any(
        not isinstance(field, dict)
        or set(field) - allowed_field_keys
        or not isinstance(field.get("id"), str)
        or not field["id"]
        or field.get("kind") not in allowed_field_kinds
        or (
            "aliases" in field
            and (
                not isinstance(field["aliases"], list)
                or not field["aliases"]
                or any(not isinstance(value, str) or not value for value in field["aliases"])
            )
        )
        or (
            "minimum_chars" in field
            and (
                field["kind"] != "text"
                or type(field["minimum_chars"]) is not int
                or field["minimum_chars"] < 1
            )
        )
        or ("pattern" in field and (not isinstance(field["pattern"], str) or not field["pattern"]))
        for field in fields
    ):
        return False
    if len({field["id"] for field in fields}) != len(fields):
        return False
    for field in fields:
        if "pattern" in field:
            re.compile(field["pattern"])
    return True


def validate_composite_acceptance(contract: dict) -> None:
    if (
        not isinstance(contract, dict)
        or contract.get("version") != 1
        or set(contract) != {"version", "groups"}
        or not isinstance(contract.get("groups"), list)
        or not contract["groups"]
    ):
        raise ValueError("Unsupported composite acceptance contract")
    ids = set()
    for group in contract["groups"]:
        _validate_group(group)
        if group["id"] in ids:
            raise ValueError("Duplicate composite group id")
        ids.add(group["id"])


def _markdown_contexts(content: str) -> list[tuple[object, str]]:
    links = markdown_links(content)
    contexts = []
    for link in links:
        line_start = content.rfind("\n", 0, link.start) + 1
        line_end = content.find("\n", link.end)
        line_end = len(content) if line_end < 0 else line_end
        line = content[line_start:line_end]
        if "|" in line:
            context = line
        else:
            block_end = content.find("\n\n", link.end)
            block_end = len(content) if block_end < 0 else block_end
            context = content[line_start:block_end]
            # A list item owns its indented continuation lines, not the next item.
            before = content[line_start : link.start]
            if re.fullmatch(r"\s*(?:[-*+] |\d+[.)] )[^\n]*", before):
                following = re.search(r"(?m)^\s*(?:[-*+] |\d+[.)] )", content[line_end:block_end])
                if following:
                    context = content[line_start : line_end + following.start()]
        contexts.append((link, clean_reference_text(context)))
    return contexts


def _link_identity(url: str, group: dict) -> str | None:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or "*" not in group["hosts"]
        and parts.hostname not in group["hosts"]
    ):
        return None
    path = parts.path.rstrip("/")
    if not re.fullmatch(group["entry_path"], path):
        return None
    identity = f"{parts.hostname}::{path}"
    if pattern := group.get("entry_identity_pattern"):
        match = re.search(pattern, path)
        if not match:
            return None
        identity = f"{parts.hostname}::{match[1]}"
    keys = group.get("identity_query_keys", [])
    if keys:
        values = {key: [] for key in keys}
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key in values:
                values[key].append(value)
        if any(len(values[key]) != 1 or not values[key][0].strip() for key in keys):
            return None
        identity += "?" + "&".join(f"{key}={values[key][0]}" for key in sorted(keys))
    return identity


def _named_link_group(content: str, group: dict, base_url: str, links: list | None) -> dict:
    standard = {
        "version": 1,
        "schema": "named_link",
        "minimum_records": group["minimum_records"],
        "hosts": group["hosts"],
        "entry_path": group["entry_path"],
    }
    for key in (
        "entry_identity_pattern",
        "identity_query_keys",
        "link_base_url",
        "record_name_exclusions",
    ):
        if key in group:
            standard[key] = group[key]
    if "*" in group["hosts"]:
        entries = []
        for link, _ in _markdown_contexts(content):
            destination = urljoin(group.get("link_base_url", base_url), link.destination)
            if _link_identity(destination, group) is not None and not link.is_image:
                label = visible_markdown_links(link.label).strip()
                if label:
                    entries.append({"url": destination, "title": label})
    else:
        entries = extract_named_entries(content, standard, base_url, links)
    exclusions = {_key(value) for value in group.get("record_name_exclusions", [])}
    entries = [entry for entry in entries if _key(entry["title"]) not in exclusions]
    fields = group.get("required_fields", [])
    if fields:
        # A card commonly places its title link, body paragraphs and date on
        # separate Markdown blocks.  Own everything from the first qualifying
        # link for one identity up to the first qualifying link for another
        # identity.  Repeated title/date links to the same article stay inside
        # that record and cannot inflate the count.
        allowed_rows = {_link_identity(entry["url"], group): entry for entry in entries}
        preferred_titles = {}
        for link in markdown_links(content):
            destination = urljoin(group.get("link_base_url", base_url), link.destination)
            identity = _link_identity(destination, group)
            if identity not in allowed_rows or link.is_image:
                continue
            label = visible_markdown_links(link.label).strip()
            field_like = bool(
                _PRICE.search(label)
                or _DATE_OR_TIME.search(label)
                or re.fullmatch(r"(?i)(?:today|tomorrow|\d[\d,.]*|more|details?)", label)
                or re.search(r"[A-Za-z][A-Za-z .'-]+,\s*[A-Z]{2}\b$", label)
            )
            if label and not field_like and _key(label) not in exclusions:
                preferred_titles.setdefault(identity, label)
        candidates = []
        for link in markdown_links(content):
            destination = urljoin(group.get("link_base_url", base_url), link.destination)
            identity = _link_identity(destination, group)
            if identity is None or link.is_image:
                continue
            entry = allowed_rows.get(identity)
            if entry is None:
                continue
            title = preferred_titles.get(identity, entry["title"])
            if not title or _key(title) in exclusions:
                continue
            candidates.append((identity, entry["url"], title, link.start))
        owned = {}
        # Consecutive links to the same identity belong to one card (for
        # example, a title link followed by a linked date).  A later duplicate
        # after another identity starts a new card and must not enlarge the
        # intervening record's field context.
        ordered = []
        for candidate in sorted(candidates, key=lambda row: row[3]):
            if not ordered or ordered[-1][0] != candidate[0]:
                ordered.append(candidate)
        for index, (identity, destination, label, start) in enumerate(ordered):
            line_start = content.rfind("\n", 0, start) + 1
            finish = ordered[index + 1][3] if index + 1 < len(ordered) else len(content)
            next_line = content.rfind("\n", 0, finish) + 1 if finish < len(content) else finish
            context = content[line_start:next_line]
            if _required_fields(context, label, field_specs=fields):
                owned[identity] = {"url": destination, "title": label, "context": context}
        allowed = {_link_identity(entry["url"], group) for entry in entries}
        records = [row for identity, row in owned.items() if identity in allowed]
    else:
        records = entries
    return {
        "count": len(records),
        "records": records,
        "minimum": group["minimum_records"],
        "pass": len(records) >= group["minimum_records"],
    }


_AMOUNT = r"(?:\d{1,3}(?:[,.\s]\d{3})+|\d+)(?:[.,]\d+)?"
_DATE_OR_TIME = re.compile(
    r"(?:\b(?:19|20)\d{2}[-/.]\d{1,2}(?:[-/.]\d{1,2})?\b|"
    r"\b\d{1,2}[-/.]\d{1,2}[-/.](?:\d{2}|\d{4})\b|"
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2}(?:,\s*(?:19|20)\d{2})?\b|"
    r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)(?:\s+(?:19|20)\d{2})?\b|"
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?\b|"
    r"\b\d{1,2}:\d{2}(?:\s*[ap]m)?\b|"
    r"\b(?:an?|\d+)\s+(?:minutes?|hours?|days?)\s+ago\b)",
    re.I,
)
_PRICE = re.compile(rf"(?:[$£€¥]\s*{_AMOUNT}|(?:USD|EUR|GBP|CNY)\s*{_AMOUNT})", re.I)
_AREA = re.compile(
    rf"\b{_AMOUNT}\s*(?:sq\.?\s*ft|sqft|ft²|m²|square\s+(?:feet|met(?:er|re)s))\b", re.I
)
_VERSION = re.compile(
    r"\b(?:v(?:ersion)?\s*)?\d+(?:\.\d+){1,3}(?:[-+._a-z0-9]*)\b|\bcompatib(?:le|ility)\b", re.I
)


def _context_lines(context: str, title: str) -> list[str]:
    title_key = _key(title)
    lines = []
    for raw in clean_reference_text(context).splitlines():
        if re.match(r"^\s*#{1,6}\s+", raw):
            continue
        line = visible_markdown_links(raw)
        line = re.sub(r"(?<!\w)([*_]{1,3})(?=\S)(.+?\S)\1(?!\w)", r"\2", line)
        line = line.strip(" \t#>*|-_")
        if not line or _key(line) == title_key or re.fullmatch(r"https?://\S+", line):
            continue
        # Some native Markdown serializers concatenate venue and city text
        # nodes without a separator (for example ``ArenaSeattle, WA``).  The
        # lowercase-to-uppercase boundary still proves two visible fields.
        split = re.search(r"(?<=[a-z])(?=[A-Z][A-Za-z .'-]+,\s*[A-Z]{2}\b)", line)
        if split:
            lines.extend((line[: split.start()], line[split.start() :]))
            continue
        lines.append(line)
    return lines


def _field_value(
    lines: list[str], spec: dict, used: set[int] | None = None
) -> tuple[int, str] | None:
    aliases = spec.get("aliases", [])
    candidates = []
    if aliases:
        for index, line in enumerate(lines):
            if used and index in used:
                continue
            for alias in aliases:
                match = re.match(rf"(?i)^\s*{re.escape(alias)}\s*[:：-]?\s*(.+)$", line)
                if match and match[1].strip():
                    candidates.append((index, match[1].strip()))
                elif (
                    _key(line) == _key(alias)
                    and index + 1 < len(lines)
                    and (not used or index + 1 not in used)
                ):
                    candidates.append((index + 1, lines[index + 1].strip()))
    else:
        candidates = [
            (index, line) for index, line in enumerate(lines) if not used or index not in used
        ]
    pattern = spec.get("pattern")
    kind = spec["kind"]
    for index, value in candidates:
        if pattern and not re.search(pattern, value, re.I | re.M):
            continue
        if kind == "integer" and re.search(r"\b\d[\d,.]*\b", value):
            return index, value
        if kind == "date_or_time" and _DATE_OR_TIME.search(value):
            return index, value
        if kind == "price" and _PRICE.search(value):
            return index, value
        if kind == "area" and _AREA.search(value):
            return index, value
        if kind == "version" and _VERSION.search(value):
            return index, value
        if kind == "text":
            minimum = spec.get("minimum_chars", 2)
            visible = re.sub(r"\s+", " ", value).strip()
            if len(visible) >= minimum and any(character.isalpha() for character in visible):
                return index, value
    return None


def _required_fields(context: str, title: str, *, field_specs: list[dict]) -> bool:
    """Require every declared field inside the record block which owns the link."""
    lines = _context_lines(context, title)
    used = set()
    for spec in field_specs:
        result = _field_value(lines, spec, used)
        if result is None:
            return False
        used.add(result[0])
    return True


def _name_description_group(content: str, group: dict, base_url: str) -> dict:
    from .stable_tasks import evaluate_stable_task

    result = evaluate_stable_task(
        content,
        {
            "version": 1,
            "schema": "name_description",
            "minimum_records": group["minimum_records"],
        },
        base_url,
    )
    return {
        "count": result["stable_unique_record_count"],
        "records": result["stable_records"],
        "minimum": group["minimum_records"],
        "pass": result["stable_structure_pass"],
    }


def _table_group(content: str, group: dict) -> dict:
    lines = [line.strip() for line in clean_reference_text(content).splitlines() if line.strip()]
    required = group["required_columns"]
    records = []
    for index in range(len(lines) - 1):
        delimiter = "|" if "|" in lines[index] else "\t" if "\t" in lines[index] else None
        if not delimiter:
            continue
        headers = [_key(value) for value in lines[index].strip("|").split(delimiter)]
        separator = lines[index + 1].strip("|").split(delimiter)
        if delimiter == "|" and not all(
            re.fullmatch(r"\s*:?-{3,}:?\s*", value) for value in separator
        ):
            continue
        mapping = {}
        for spec in required:
            hits = [
                i
                for i, header in enumerate(headers)
                if header in {_key(value) for value in spec["aliases"]}
            ]
            if len(hits) != 1:
                break
            mapping[spec["id"]] = hits[0]
        else:
            row_index = index + 2 if delimiter == "|" else index + 1
            while row_index < len(lines) and delimiter in lines[row_index]:
                values = [value.strip() for value in lines[row_index].strip("|").split(delimiter)]
                if len(values) == len(headers):
                    row = {field: values[column] for field, column in mapping.items()}
                    if all(value and value not in {"-", "—"} for value in row.values()):
                        records.append(row)
                row_index += 1
            break
    unique = {_key(row[group["identity_column"]]): row for row in records}
    return {
        "count": len(unique),
        "records": list(unique.values()),
        "minimum": group["minimum_records"],
        "pass": len(unique) >= group["minimum_records"],
    }


def _linked_table_group(content: str, group: dict, base_url: str) -> dict:
    """Read named links and all required fields from the same Markdown row."""
    lines = [line.strip() for line in clean_reference_text(content).splitlines() if line.strip()]
    required = group["required_columns"]
    records = {}
    for index in range(len(lines) - 1):
        if "|" not in lines[index]:
            continue
        headers = [_key(value) for value in lines[index].strip("|").split("|")]
        separator = lines[index + 1].strip("|").split("|")
        if not all(re.fullmatch(r"\s*:?-{3,}:?\s*", value) for value in separator):
            continue
        mapping = {}
        for spec in required:
            hits = [
                position
                for position, header in enumerate(headers)
                if header in {_key(value) for value in spec["aliases"]}
            ]
            if len(hits) != 1:
                break
            mapping[spec["id"]] = hits[0]
        else:
            row_index = index + 2
            while row_index < len(lines) and "|" in lines[row_index]:
                values = [value.strip() for value in lines[row_index].strip("|").split("|")]
                if len(values) == len(headers):
                    row = {field: values[column] for field, column in mapping.items()}
                    cell = row[group["link_column"]]
                    candidates = [link for link in markdown_links(cell) if not link.is_image]
                    if len(candidates) == 1 and all(
                        visible_markdown_links(value).strip() not in {"", "-", "—"}
                        for value in row.values()
                    ):
                        link = candidates[0]
                        destination = urljoin(
                            group.get("link_base_url", base_url), link.destination
                        )
                        identity = _link_identity(destination, group)
                        label = visible_markdown_links(link.label).strip()
                        if identity is not None and label:
                            records[identity] = {"url": destination, "title": label, "fields": row}
                row_index += 1
            break
    values = list(records.values())
    return {
        "count": len(values),
        "records": values,
        "minimum": group["minimum_records"],
        "pass": len(values) >= group["minimum_records"],
    }


def _field_report_group(content: str, group: dict) -> dict:
    lines = _context_lines(content, "")
    fields = {}
    used = set()
    for spec in group["required_fields"]:
        value = _field_value(lines, spec, used)
        if value is not None:
            fields[spec["id"]] = value[1]
            used.add(value[0])
    fixed = {
        key: {"value": value, "pass": key in fields and _key(fields[key]) == _key(value)}
        for key, value in group.get("fixed_values", {}).items()
    }
    passed = len(fields) == len(group["required_fields"]) and all(
        result["pass"] for result in fixed.values()
    )
    return {
        "count": None,
        "minimum": None,
        "records": [{"fields": fields}],
        "fixed_value_results": fixed,
        "pass": passed,
    }


def evaluate_composite_acceptance(
    content: str, contract: dict, *, base_url: str, links: list | None = None, eligible: bool = True
) -> dict:
    validate_composite_acceptance(contract)
    results = []
    for group in contract["groups"]:
        scoped = _scope(content, group)
        if group["kind"] == "named_link":
            result = _named_link_group(
                scoped, group, base_url, links if scoped == content else None
            )
        elif group["kind"] == "linked_table":
            result = _linked_table_group(scoped, group, base_url)
        elif group["kind"] == "name_description":
            result = _name_description_group(scoped, group, base_url)
        elif group["kind"] == "table":
            result = _table_group(scoped, group)
        elif group["kind"] == "field_report":
            result = _field_report_group(scoped, group)
        else:
            folded = _key(scoped)
            literals = [
                {"value": value, "pass": _key(value) in folded}
                for value in group["required_literals"]
            ]
            result = {
                "count": None,
                "minimum": None,
                "records": [],
                "literal_results": literals,
                "pass": all(row["pass"] for row in literals),
            }
        results.append({"id": group["id"], "kind": group["kind"], **result})
    errors = ["composite_group_failed:" + result["id"] for result in results if not result["pass"]]
    if not eligible:
        errors.append("request_ineligible")
    return {
        "composite_acceptance_pass": not errors,
        "composite_group_results": results,
        "composite_acceptance_errors": errors,
    }
