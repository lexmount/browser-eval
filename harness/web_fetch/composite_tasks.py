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

from .dynamic_lists import _canonical_entry_path, extract_named_entries
from .reference_quality import clean_reference_text, markdown_links, visible_markdown_links


def _key(value: str) -> str:
    value = unicodedata.normalize("NFKC", unquote(value)).casefold()
    return re.sub(r"\s+", " ", value).strip(" *#>`")


def _column_scope(content: str, group: dict) -> str | None:
    """Own a declared named list by its table column, not the adjacent column."""
    start, end = group.get("start_pattern"), group.get("end_pattern")
    if group["kind"] != "named_link" or not start or not end:
        return None
    from .structured_tables import _pipe_cells
    lines = content.splitlines()
    def label(cell):
        return re.sub(r"^#{1,6}\s+", "", visible_markdown_links(cell).strip()).strip(" *")
    for index, line in enumerate(lines):
        delimiter = "\t" if "\t" in line else "|" if "|" in line else None
        if not delimiter:
            continue
        cells = _pipe_cells(line) if delimiter == "|" else line.split("\t")
        if len(cells) < 2:
            continue
        starts = [i for i, cell in enumerate(cells) if re.fullmatch(start, label(cell), re.I)]
        if not starts:
            continue
        if len(starts) != 1:
            return ""
        column = starts[0]
        sibling_end = any(i != column and re.fullmatch(end, label(cell), re.I)
                          for i, cell in enumerate(cells))
        if not sibling_end and not re.search(end, "\n".join(lines[index + 1:]), re.I | re.M):
            return ""
        cursor = index + 1
        if delimiter == "|":
            if cursor == len(lines):
                return ""
            divider = _pipe_cells(lines[cursor])
            if len(divider) != len(cells) or not all(re.fullmatch(r"\s*:?-{3,}:?\s*", value) for value in divider):
                continue
            cursor += 1
        owned = [cells[column]]
        while cursor < len(lines) and lines[cursor].strip():
            values = _pipe_cells(lines[cursor]) if delimiter == "|" else lines[cursor].split("\t")
            if len(values) != len(cells):
                break
            owned.append(values[column])
            cursor += 1
        return "\n".join(owned)
    return None


def _scope(content: str, group: dict) -> str:
    """Return one declared section, failing closed when a boundary is absent."""
    column = _column_scope(content, group)
    if column is not None:
        return column
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
        "record_line_pattern",
        "start_pattern",
        "end_pattern",
        "required_fields",
        "required_columns",
        "identity_column",
        "required_literals",
        "fixed_values",
        "link_column",
        "combined_columns",
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
    for key in ("start_pattern", "end_pattern", "record_line_pattern"):
        if key in group:
            if not isinstance(group[key], str) or not group[key]:
                raise ValueError("Invalid composite scope pattern")
            re.compile(group[key])
    kind = group["kind"]
    if "combined_columns" in group and kind != "table":
        raise ValueError("Combined columns require a table group")
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
            or any(not isinstance(row, dict) or "aliases" not in row for row in columns)
            or not fields_are_valid(columns)
            or group.get("identity_column") not in {row["id"] for row in columns}
        ):
            raise ValueError("Invalid composite table columns")
        if kind == "linked_table" and group.get("link_column") not in {
            row["id"] for row in columns
        }:
            raise ValueError("Invalid composite table link column")
        combinations = group.get("combined_columns", [])
        if not isinstance(combinations, list) or (combinations and kind != "table"):
            raise ValueError("Invalid combined table columns")
        for combined in combinations:
            if (not isinstance(combined, dict) or set(combined) != {"aliases", "pattern"}
                    or not isinstance(combined["aliases"], list) or not combined["aliases"]
                    or any(not isinstance(a, str) or not a.strip() for a in combined["aliases"])
                    or not isinstance(combined["pattern"], str) or not combined["pattern"]):
                raise ValueError("Invalid combined table columns")
            pattern = re.compile(combined["pattern"])
            if (len(pattern.groupindex) < 2 or pattern.groups != len(pattern.groupindex)
                    or not set(pattern.groupindex) <= {row["id"] for row in columns}):
                raise ValueError("Combined columns must capture declared fields by name")
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
    path = _canonical_entry_path(parts.path).rstrip("/")
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


def _html_table_record_contexts(content: str, group: dict, base_url: str, allowed: set):
    """Retain native row/cell boundaries lost by a text-only projection.

    One linked cell owns its row's fields. Multiple linked cells are separate
    records; extra links nested in one cell do not create additional records.
    """
    from .dynamic_lists import _scoped_html, visible_html_text
    soup = _scoped_html(content)
    contexts, table_ids = {}, set()
    for row in soup.select('tr'):
        owners = []
        for cell in row.select('td,th'):
            if cell.find_parent('tr') is not row:
                continue
            identities = []
            for link in cell.select('a[href]'):
                if link.find_parent(['td', 'th']) is not cell:
                    continue
                identity = _link_identity(urljoin(base_url, link['href']), group)
                if identity in allowed:
                    table_ids.add(identity)
                    if identity not in identities:
                        identities.append(identity)
            if identities:
                owners.append((cell, identities[0]))
        for cell, identity in owners:
            owner = row if len(owners) == 1 else cell
            context = visible_html_text(str(owner), preserve_links=True)
            contexts.setdefault(identity, []).append(context)
    return table_ids, contexts


def _card_section_context(content: str, begin: int, end: int, title_start: int,
                          identity: str, group: dict, base_url: str) -> str:
    """Stop an implicit card at a new peer section; keep owned subheadings."""
    context = content[begin:end]
    first_line = content[begin:].split("\n", 1)[0]
    own_heading = re.match(r"[ \t]{0,3}(#{1,6})[ \t]+", first_line)
    level = len(own_heading[1]) if own_heading else 6
    links = markdown_links(content)
    fence = None
    offset = 0
    for line in context.splitlines(keepends=True):
        marker = re.match(r"[ \t]{0,3}(`{3,}|~{3,})", line)
        if marker:
            value = marker[1]
            if fence is None:
                fence = value
            elif (value[0] == fence[0] and len(value) >= len(fence)
                  and not line[marker.end():].strip()):
                fence = None
        elif fence is None:
            heading = re.match(r"[ \t]{0,3}(#{1,6})[ \t]+", line)
            absolute = begin + offset
            owned_heading = heading and any(
                absolute <= link.start < absolute + len(line)
                and _link_identity(urljoin(group.get("link_base_url", base_url), link.destination), group) == identity
                for link in links if not link.is_image)
            if owned_heading:
                level = min(level, len(heading[1]))
            if (heading and begin + offset > title_start
                    and len(heading[1]) <= level
                    and not owned_heading
                    and not any(link.start <= absolute < link.end for link in links)):
                return context[:offset]
        offset += len(line)
    return context


def _named_link_group(content: str, group: dict, base_url: str, links: list | None, *, native_html: str | None = None) -> dict:
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

        def is_record_start(link) -> bool:
            pattern = group.get("record_line_pattern")
            if not pattern:
                return True
            line_start = content.rfind("\n", 0, link.start) + 1
            line_end = content.find("\n", link.end)
            line_end = len(content) if line_end < 0 else line_end
            line = content[line_start:line_end].lstrip()
            date = _DATE_OR_TIME.match(line)
            if date and re.match(r"[ \t]*#{1,6}[ \t]+", line[date.end():]):
                line = line[date.end():].lstrip()
            if re.fullmatch(r"(?P<mark>\*\*|__).+?(?P=mark)", link.label):
                raw_link = content[link.start:link.end]
                if line.strip() == raw_link:
                    return any(re.search(pattern, "#" * level + " " + line, re.I)
                               for level in range(1, 7))
            return bool(re.search(pattern, line, re.I))

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
            if identity not in allowed_rows or link.is_image or not is_record_start(link):
                continue
            visible_label = link.label
            for media in reversed(markdown_links(visible_label)):
                if media.is_image:
                    visible_label = visible_label[:media.start] + " " + visible_label[media.end:]
            label = visible_markdown_links(visible_label).strip()
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
            if identity is None or link.is_image or (identity not in preferred_titles and not is_record_start(link)):
                continue
            entry = allowed_rows.get(identity)
            if entry is None:
                continue
            title = preferred_titles.get(identity, entry["title"])
            if not title or _key(title) in exclusions:
                continue
            candidates.append((identity, entry["url"], title, link.start, link.end))
        owned = {}
        # Consecutive links to the same identity belong to one card (for
        # example, a title link followed by a linked date).  A later duplicate
        # after another identity starts a new card and must not enlarge the
        # intervening record's field context.
        ordered = []
        for candidate in sorted(candidates, key=lambda row: row[3]):
            if not ordered or ordered[-1][0] != candidate[0]:
                ordered.append(candidate)
            else:
                ordered[-1] = (*ordered[-1][:4], max(ordered[-1][4], candidate[4]))
        for index, (identity, destination, label, start, owned_end) in enumerate(ordered):
            line_start = content.rfind("\n", 0, start) + 1
            finish = ordered[index + 1][3] if index + 1 < len(ordered) else len(content)
            next_line = content.rfind("\n", 0, finish) + 1 if finish < len(content) else finish
            begin = start if index and ordered[index - 1][4] > line_start else line_start
            context = _card_section_context(content, begin, max(next_line, owned_end), start, identity, group, base_url)
            if _required_fields(context, label, field_specs=fields):
                owned[identity] = {"url": destination, "title": label, "context": context}
        if native_html:
            table_ids, table_contexts = _html_table_record_contexts(
                native_html, group, base_url, set(allowed_rows),
            )
            for identity in table_ids:
                entry = allowed_rows[identity]
                label = preferred_titles.get(identity, entry["title"])
                context = next((value for value in table_contexts.get(identity, [])
                                if _required_fields(value, label, field_specs=fields)), None)
                if context is None:
                    owned.pop(identity, None)
                else:
                    owned[identity] = {"url": entry["url"], "title": label,
                                       "context": context, "context_basis": "native_table_owner"}
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
    r"Dec(?:ember)?)\.?\s+\d{1,2}(?:,\s*(?:19|20)\d{2})?\b|"
    r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?(?:\s+(?:19|20)\d{2})?\b|"
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


def _value_matches_spec(value: str, spec: dict, *, whole_cell: bool = False) -> bool:
    """Apply the same declared field type rules to card and table values."""
    visible = re.sub(r"\s+", " ", visible_markdown_links(value)).strip()
    if not visible or visible in {"-", "—"}:
        return False
    if pattern := spec.get("pattern"):
        if not re.search(pattern, visible, re.I | re.M):
            return False
    kind = spec["kind"]
    if kind == "integer":
        pattern = r"\d[\d,.]*"
        return bool(
            re.fullmatch(pattern, visible) if whole_cell else re.search(rf"\b{pattern}\b", visible)
        )
    if kind == "date_or_time":
        return bool(_DATE_OR_TIME.search(visible))
    if kind == "price":
        return bool(_PRICE.search(visible))
    if kind == "area":
        return bool(_AREA.search(visible))
    if kind == "version":
        return bool(_VERSION.search(visible))
    minimum = spec.get("minimum_chars", 2)
    return len(visible) >= minimum and any(character.isalpha() for character in visible)


def serialized_link_card_fields(link):
    """Recover explicit inline card spans; URLs only corroborate visible text."""
    label = link.label
    for image in reversed(markdown_links(label)):
        if image.is_image:
            label = label[:image.start] + " " + label[image.end:]
    label = visible_markdown_links(label).strip()
    explicit = re.fullmatch(
        r"(?P<name>.+),\s+(?P<city>[^,\n]+,\s*[A-Z]{2})\s+"
        r"(?P<venue>.+?)(?P<date>\d{1,2}/\d{1,2}/\d{2,4},\s*\d{1,2}:\d{2}\s*[AP]M)(?:.*)", label)
    if explicit:
        name = re.sub(r"^(?:Find Tickets|Event Info)\s*", "", explicit['name'])
        return name, [explicit['date'], explicit['venue'], explicit['city']]
    times = list(re.finditer(r"\d{1,2}:\d{2}\s*[AP]M\b", label, re.I))
    if not times:
        return None
    end = times[-1].end()
    tail = label[end:].strip()
    compact = lambda value: re.sub(r"[^a-z0-9]", "", value.casefold())
    slugs = [compact(unquote(segment)) for segment in urlsplit(link.destination).path.split('/')]
    cities = [match for match in re.finditer(
        r"(?<=[a-z\s])(?=[A-Z][A-Za-z .'-]+,\s*[A-Z]{2}$)", tail)
        if any(compact(tail[match.start():].rsplit(',', 1)[0]) in slug for slug in slugs)]
    if not cities:
        return None
    city = cities[0]
    name_venue, location = tail[:city.start()].strip(), tail[city.start():]
    city_key = compact(location.rsplit(',', 1)[0])
    matches = []
    for segment in urlsplit(link.destination).path.split('/'):
        slug = compact(unquote(segment))
        if city_key not in slug:
            continue
        # The URL cannot supply a missing name, date, place or city. It only
        # distinguishes two values already present in the same link label.
        slug = slug.split(city_key, 1)[0]
        for boundary in re.finditer(r"(?<=\w)(?=\s|[^\w\s])", name_venue):
            name = name_venue[:boundary.start()].strip()
            venue = name_venue[boundary.start():].strip()
            if (len(re.findall(r"\w+", name)) >= 2 and len(compact(name)) >= 8
                    and compact(name) == slug and len(re.findall(r"[A-Za-z]+", venue)) >= 2):
                matches.append((len(compact(name)), name, venue))
    if not matches:
        return None
    _, name, venue = max(matches)
    return name, [label[:end], venue, location]


def _context_lines(context: str, title: str) -> list[str]:
    title_key = _key(title)
    for card in markdown_links(context):
        if card.is_image:
            continue
        recovered = serialized_link_card_fields(card)
        if recovered and title_key in {_key(recovered[0]), _key(visible_markdown_links(card.label))}:
            return recovered[1]
    for card in reversed(markdown_links(context)):
        if (card.is_image or "\n" not in card.label
                 ):
            continue
        label = card.label
        for image in reversed(markdown_links(label)):
            if image.is_image:
                label = label[:image.start] + " " + label[image.end:]
        if _key(visible_markdown_links(label)) != title_key:
            continue
        header = re.match(r"[ \t]*(?P<mark>\*\*|__)(?P<title>[^\n]+?)(?P=mark)(?:\\)*[ \t]*\n", label)
        if not header:
            # Do not leave a multiline Markdown destination in a field line:
            # dates in the URL are not a delivered visible date field.
            context = context[:card.start] + visible_markdown_links(label) + context[card.end:]
            continue
        name = re.sub(r"\\([_*!\[\]()`])", r"\1", header["title"]).strip()
        identity_key = lambda value: re.sub(r"[\W_]", "", _key(value))
        identities = {identity_key(name), identity_key(name.rsplit("/", 1)[-1]),
                      identity_key(unquote(urlsplit(card.destination).path.rsplit("/", 1)[-1]))}
        parts = []
        author_pending = False
        for raw in label[header.end():].splitlines():
            value = re.sub(r"\\+$", "", raw).strip()
            value = re.sub(r"\\([_*!\[\]()`])", r"\1", value)
            if not value or value == "/" or identity_key(value) in identities:
                continue
            if value.casefold() == "by":
                author_pending = True
                continue
            if author_pending:
                value = "Author: " + value
                author_pending = False
            parts.append(value)
        context = context[:card.start] + "\n".join(parts) + context[card.end:]
    lines = []
    for raw in clean_reference_text(context).splitlines():
        for link in reversed(markdown_links(raw)):
            label = link.label
            for image in reversed(markdown_links(label)):
                if image.is_image:
                    label = label[:image.start] + " " + label[image.end:]
            if link.is_image or not label.strip():
                raw = raw[:link.start] + " " + raw[link.end:]
            elif label != link.label:
                native_link = raw[link.start:link.end].replace(link.label, label, 1)
                raw = raw[:link.start] + native_link + raw[link.end:]
        if re.match(r"^\s*#{1,6}\s+", raw):
            continue
        # The linked title identifies the record; it cannot provide a
        # missing source or summary beside a date on the same native line.
        if title_key:
            for link in reversed(markdown_links(raw)):
                if not link.is_image and _key(visible_markdown_links(link.label)) == title_key:
                    raw = raw[:link.start] + " " + raw[link.end:]
        line = visible_markdown_links(raw)
        # A closed bold numeric span followed by its unit exposes two
        # adjacent visible nodes. Preserve their boundary for typed fields.
        # Escaped or unclosed markers are literal text, not this format.
        line = re.sub(rf"(?<![\\\w*])\*\*({_AMOUNT})\*\*(?=[A-Za-z])",
                      r"\1 ", line)
        line = re.sub(r"(?<![\w*_])([*_]{1,3})([^\s*_](?:[^*_\n]*?[^\s*_])?)\1(?![\w*_])", r"\2", line)
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
        # Browser innerText may join adjacent inline field/status nodes.
        # Preserve the visible lexical boundary, not a new record/field value.
        lines.append(re.sub(r"(?<=[a-z])(?=\d|[A-Z])", " ", line))
    return lines


def _field_token_pattern(spec: dict):
    """Typed card fields own a distinct value span, not an entire layout line."""
    if spec.get('aliases'):
        return None
    if spec['kind'] == 'integer' and spec.get('pattern'):
        # A number elsewhere in the card cannot prove a numeric labeled field.
        return re.compile(rf"\b{_AMOUNT}\s*(?:{spec['pattern']})|(?:{spec['pattern']})", re.I)
    return {'price': _PRICE, 'area': _AREA, 'date_or_time': _DATE_OR_TIME,
            'version': _VERSION}.get(spec['kind'])


def _text_role_value(value: str, spec: dict) -> str | None:
    """Explicit metadata belongs to its declared role, not another text field."""
    roles = {"title", "name", "author", "creator", "owner", "source", "publisher",
             "description", "summary", "date", "time", "version", "downloads",
             "views", "likes", "count", "price", "rating", "size"}
    match = re.fullmatch(r"([A-Za-z][A-Za-z ]*)[ \t]*[:：][ \t]*(.*)", value)
    role = _key(match[1]) if match else None
    payload = match[2].strip() if match else value
    if re.fullmatch(r"[^\n]+?\s*/\s*by\s+[^\n]+", value, re.I):
        role = "author"
        payload = re.split(r"\s*/\s*by\s+", value, maxsplit=1, flags=re.I)[1]
    if role in roles:
        accepted = {_key(spec["id"].replace("_", " ")),
                    *(_key(alias) for alias in spec.get("aliases", []))}
        return payload if role in accepted and payload else None
    return value


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
    for index, value in candidates:
        if spec["kind"] == "text":
            value = _text_role_value(value, spec)
            if value is None:
                continue
        if token_pattern := _field_token_pattern(spec):
            for match in token_pattern.finditer(value):
                if _value_matches_spec(match[0], spec):
                    return index, match[0]
            continue
        if _value_matches_spec(value, spec):
            return index, value
    return None


def _labeled_field_lines(lines, field_specs):
    """Keep explicit label/value spans separate within one native card line."""
    aliases = sorted({alias for spec in field_specs for alias in spec.get("aliases", [])},
                     key=len, reverse=True)
    if not aliases:
        return lines
    label = r"(?:" + "|".join(re.escape(alias) for alias in aliases) + r")[ \t]*[:：]"
    labels = re.compile(r"(?<!\w)" + label, re.I)
    result = []
    for line in lines:
        # Only a line that starts with a declared labeled field can contain
        # more inline fields. A label mentioned inside prose is not metadata.
        if re.match(label, line, re.I):
            starts = [match.start() for match in labels.finditer(line)]
            ends = starts[1:] + [len(line)]
            result.extend(line[start:end].strip() for start, end in zip(starts, ends))
        else:
            result.append(line)
    return result


def _required_fields(context: str, title: str, *, field_specs: list[dict]) -> bool:
    """Require every declared field inside the record block which owns the link."""
    lines = _labeled_field_lines(_context_lines(context, title), field_specs)
    # A declared text label at the beginning of a native line has its own
    # exact span. An adjacent visible value remains a separate field.
    separated = []
    patterns = [re.compile(spec["pattern"], re.I) for spec in field_specs
                if spec["kind"] == "text" and spec.get("pattern") and not spec.get("aliases")]
    for line in lines:
        for pattern in patterns:
            match = pattern.match(line)
            if match and match.end() and (match.end() == len(line)
                    or not line[match.end()].isalnum()):
                rest = line[match.end():].strip(" |\t")
                if rest:
                    separated.extend((match[0], rest))
                    break
        else:
            separated.append(line)
    lines = separated
    used = set()
    typed_lines = set()
    for spec in sorted(field_specs, key=lambda field: not (field.get("aliases")
                       or field["kind"] == "text" and field.get("pattern"))):
        typed = _field_token_pattern(spec) is not None
        result = _field_value(lines, spec, used if typed else used | typed_lines)
        if result is None:
            return False
        index, value = result
        if typed:
            # Consume this exact span so fields may share a line, never a value.
            lines[index] = lines[index].replace(value, ' ' * len(value), 1)
            typed_lines.add(index)
        else:
            used.add(index)
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
        headers = [_key(visible_markdown_links(value)) for value in lines[index].strip("|").split(delimiter)]
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
            if len(hits) > 1:
                break
            if hits:
                mapping[spec["id"]] = (hits[0], None)
        else:
            for combined in group.get("combined_columns", []):
                pattern = re.compile(combined["pattern"])
                fields = set(pattern.groupindex)
                hits = [i for i, header in enumerate(headers)
                        if header in {_key(a) for a in combined["aliases"]}]
                if len(hits) == 1 and not fields & mapping.keys():
                    mapping.update({field: (hits[0], pattern) for field in fields})
            if set(mapping) != {spec["id"] for spec in required}:
                continue
            row_index = index + 2 if delimiter == "|" else index + 1
            while row_index < len(lines) and delimiter in lines[row_index]:
                values = [value.strip() for value in lines[row_index].strip("|").split(delimiter)]
                if len(values) == len(headers):
                    row = {}
                    for field, (column, pattern) in mapping.items():
                        match = pattern.fullmatch(visible_markdown_links(values[column])) if pattern else None
                        row[field] = (match[field] if match else '') if pattern else values[column]
                    if all(
                        _value_matches_spec(row[spec["id"]], spec, whole_cell=True)
                        for spec in required
                    ):
                        records.append(row)
                row_index += 1
            # Every table in the independently declared section contributes;
            # repeated identities are still counted only once below.
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
        headers = [_key(visible_markdown_links(value)) for value in lines[index].strip("|").split("|")]
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
                        _value_matches_spec(row[spec["id"]], spec, whole_cell=True)
                        for spec in required
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
    lines = [re.sub(r"\s*\|\s*", " ", line).strip()
             for line in _context_lines(content, "")]
    lines = _labeled_field_lines(lines, group["required_fields"])
    fields = {}
    used = set()
    for spec in group["required_fields"]:
        field_spec = dict(spec)
        if spec["kind"] == "text" and "minimum_chars" not in spec:
            field_spec["minimum_chars"] = 1
        value = _field_value(lines, field_spec, used)
        if value is not None:
            fields[spec["id"]] = value[1]
            used.add(value[0])
    fixed = {}
    specs = {spec["id"]: spec for spec in group["required_fields"]}
    for key, expected in group.get("fixed_values", {}).items():
        value = fields.get(key, "")
        spec = specs[key]
        pattern = spec.get("pattern")
        if spec["kind"] == "integer":
            match = re.match(r"\d[\d,.]*(?!\w)", value)
            value = match[0] if match else ""
        elif pattern:
            match = re.match(rf"(?:{pattern})(?!\w)", value, re.I)
            value = match[0] if match else ""
        fixed[key] = {"value": expected, "pass": key in fields and _key(value) == _key(expected)}
    passed = len(fields) == len(group["required_fields"]) and all(
        result["pass"] for result in fixed.values()
    )
    return {"count": None, "minimum": None, "records": [{"fields": fields}],
            "fixed_value_results": fixed, "pass": passed}


def evaluate_composite_acceptance(
    content: str, contract: dict, *, base_url: str, links: list | None = None, eligible: bool = True,
    native_html: str | None = None
) -> dict:
    validate_composite_acceptance(contract)
    results = []
    for group in contract["groups"]:
        scoped = _scope(content, group)
        if group["kind"] == "named_link":
            result = _named_link_group(
                scoped, group, base_url, links if scoped == content else None,
                native_html=native_html,
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
