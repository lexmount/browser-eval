"""Configured row ownership and field types for changing demo tables."""
from datetime import datetime
from decimal import Decimal
import re


def validate_table(contract):
    columns = contract.get('table_columns')
    identity = contract.get('record_identity_fields')
    scope = contract.get('table_scope')
    if not isinstance(columns, list) or not columns:
        raise ValueError('Table columns must be declared')
    names = []
    for col in columns:
        if (not isinstance(col, dict) or set(col) - {'name', 'label', 'type', 'minimum', 'maximum', 'pattern', 'allow_empty', 'compact_whitespace', 'selector', 'own_text'}
                or not isinstance(col.get('name'), str) or not col['name']
                or not isinstance(col.get('label'), str) or not col['label']
                or col.get('type') not in {'text', 'token', 'integer', 'number', 'currency_usd', 'date_dmy', 'date_ordinal_en'}):
            raise ValueError('Invalid table column')
        if 'selector' in col:
            import soupsieve
            if not isinstance(col['selector'], str) or not col['selector'].strip():
                raise ValueError('Invalid record field selector')
            soupsieve.compile(col['selector'])
        if 'own_text' in col and (type(col['own_text']) is not bool or 'selector' not in col):
            raise ValueError('Own text requires an explicit field selector')
        if 'compact_whitespace' in col and (type(col['compact_whitespace']) is not bool
                                            or col['type'] != 'text'):
            raise ValueError('Whitespace equivalence requires a text column')
        if 'allow_empty' in col and type(col['allow_empty']) is not bool:
            raise ValueError('Invalid empty-cell policy')
        if col['type'] == 'token' and not col.get('pattern'):
            raise ValueError('Token columns require an explicit format pattern')
        if 'pattern' in col:
            if not isinstance(col['pattern'], str) or not col['pattern'] or re.compile(col['pattern']).search(''):
                raise ValueError('Invalid table field pattern')
        for bound in ('minimum', 'maximum'):
            if bound in col and (col['type'] not in {'integer', 'number'}
                                  or type(col[bound]) not in {int, float}):
                raise ValueError('Invalid table column bound')
        if col.get('minimum', float('-inf')) > col.get('maximum', float('inf')):
            raise ValueError('Inverted table bounds')
        names.append(col['name'])
    if len(names) != len(set(names)) or not isinstance(identity, list) or not identity or not set(identity) <= set(names):
        raise ValueError('Invalid table identity')
    if any('selector' in c for c in columns) and not all('selector' in c for c in columns):
        raise ValueError('Owned record fields must all declare selectors')
    required = {'row_selector', 'cell_selector', 'start_pattern', 'end_pattern', 'plain_row_pattern'}
    optional = {'root_selector', 'root_index', 'exclude_selector', 'plain_multiline_row_pattern',
                'reference_rows', 'reference_kind', 'ignored_column_labels',
                'plain_text_ignored_patterns', 'markdown_table_limit'}
    if (not isinstance(scope, dict) or not required <= set(scope) <= required | optional
            or any(not isinstance(scope[k], str) or not scope[k] for k in required)):
        raise ValueError('Invalid table scope')
    if 'plain_text_ignored_patterns' in scope:
        patterns = scope['plain_text_ignored_patterns']
        if (not isinstance(patterns, list) or not patterns or len(patterns) > 10
                or any(not isinstance(p, str) or not p or re.compile(p).search('')
                       for p in patterns)):
            raise ValueError('Invalid table plain-text auxiliary patterns')
    if 'markdown_table_limit' in scope and (type(scope['markdown_table_limit']) is not int
                                            or scope['markdown_table_limit'] < 1):
        raise ValueError('Invalid Markdown table limit')
    import soupsieve
    soupsieve.compile(scope['row_selector']); soupsieve.compile(scope['cell_selector'])
    if 'exclude_selector' in scope:
        if not isinstance(scope['exclude_selector'], str) or not scope['exclude_selector']:
            raise ValueError('Invalid table auxiliary-content selector')
        soupsieve.compile(scope['exclude_selector'])
    if 'ignored_column_labels' in scope:
        ignored = scope['ignored_column_labels']
        if not isinstance(ignored, list) or any(not isinstance(v, str) or not v.strip() for v in ignored):
            raise ValueError('Invalid ignored table columns')
        if {v.casefold() for v in ignored} & {c['label'].casefold() for c in columns}:
            raise ValueError('Required columns cannot be ignored')
    if ('root_selector' in scope) != ('root_index' in scope):
        raise ValueError('Table root selector and index must be paired')
    if 'root_selector' in scope:
        if (not isinstance(scope['root_selector'], str) or not scope['root_selector']
                or type(scope['root_index']) is not int or scope['root_index'] < 0):
            raise ValueError('Invalid table root selection')
        soupsieve.compile(scope['root_selector'])
    for key in ('start_pattern', 'end_pattern', 'plain_row_pattern'):
        if re.compile(scope[key]).search(''):
            raise ValueError('Empty table boundary')
    if set(re.compile(scope['plain_row_pattern']).groupindex) != set(names):
        raise ValueError('Plain row fields differ from columns')
    if 'plain_multiline_row_pattern' in scope:
        pattern = scope['plain_multiline_row_pattern']
        if (not isinstance(pattern, str) or not pattern
                or re.compile(pattern).search('')
                or set(re.compile(pattern).groupindex) != set(names)):
            raise ValueError('Invalid multiline row fields')
    if 'reference_rows' in scope or 'reference_kind' in scope:
        rows = scope.get('reference_rows')
        if scope.get('reference_kind') != 'fixed_demo' or not isinstance(rows, list) or not rows:
            raise ValueError('Exact row references require a declared fixed demo')
        if any(not isinstance(row, dict) or set(row) != set(names)
               or not all(_valid(row[c['name']], c) for c in columns) for row in rows):
            raise ValueError('Invalid fixed-demo reference rows')
        keys = [tuple(_canonical(row[c['name']], c) for c in columns) for row in rows]
        if len(set(keys)) != len(keys):
            raise ValueError('Duplicate fixed-demo reference rows')


def _valid(value, col):
    if not isinstance(value, str):
        return False
    if not value.strip():
        return col.get('allow_empty', False)
    value = value.strip()
    if col.get('pattern') and not re.fullmatch(col['pattern'], value):
        return False
    kind = col['type']
    if kind == 'currency_usd':
        return bool(re.fullmatch(r'\$\s*(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?', value))
    if kind == 'token':
        return True  # The mandatory format pattern was checked above; retain leading zeros.
    if kind == 'text':
        return bool(any(c.isalpha() for c in value) and value.casefold() not in {'null', 'undefined', 'unknown', 'n/a'})
    if kind == 'date_dmy':
        try:
            return bool(re.fullmatch(r'\d{2}/\d{2}/\d{4}', value) and datetime.strptime(value, '%d/%m/%Y'))
        except ValueError:
            return False
    if kind == 'date_ordinal_en':
        match = re.fullmatch(r'(\d{1,2})(st|nd|rd|th) ([A-Za-z]{3}) (\d{2}|\d{4})', value)
        if not match:
            return False
        day = int(match[1])
        suffix = 'th' if 10 <= day % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th')
        if match[2] != suffix:
            return False
        try:
            datetime.strptime(f'{day} {match[3]} {match[4]}', '%d %b %y' if len(match[4]) == 2 else '%d %b %Y')
            return True
        except ValueError:
            return False
    if not re.fullmatch(r'-?\d+' if kind == 'integer' else r'-?\d+(?:\.\d+)?', value):
        return False
    number = float(value)
    return col.get('minimum', float('-inf')) <= number <= col.get('maximum', float('inf'))


def _canonical(value, column):
    if column.get('compact_whitespace'):
        return re.sub(r'\s+', '', value).casefold()
    if column['type'] == 'currency_usd' and _valid(value, column):
        return str(Decimal(value.strip()[1:].replace(',', '').strip()).normalize())
    return ' '.join(value.split()).casefold()


def _without_example_fences(text):
    """Code samples in prose are not rendered data; a whole fenced answer is."""
    lines = text.splitlines(keepends=True)
    spans = []
    opened = None
    for index, line in enumerate(lines):
        if opened is None:
            match = re.match(r'^[ \t]{0,3}(`{3,}|~{3,})[^\n]*$', line.rstrip('\r\n'))
            if match:
                opened = (index, match[1][0], len(match[1]))
        elif re.fullmatch(r'[ \t]{0,3}' + re.escape(opened[1]) + '{' + str(opened[2]) + r',}[ \t]*', line.rstrip('\r\n')):
            spans.append((opened[0], index))
            opened = None
    if opened:
        spans.append((opened[0], len(lines) - 1))
    if len(spans) == 1:
        start, end = spans[0]
        if not ''.join(lines[:start] + lines[end + 1:]).strip() and opened is None:
            return ''.join(lines[start + 1:end])
    for start, end in reversed(spans):
        lines[start:end + 1] = ['\n']
    return ''.join(lines)


def parse_table_records(content, visible, contract):
    columns, scope = contract['table_columns'], contract['table_scope']
    names = [c['name'] for c in columns]
    candidates = []
    document_pattern = r'\s*(?:<!doctype\s+html[^>]*>\s*)?<(?:html|body|main|div|table|section|article|ul|ol)\b'
    if not re.match(document_pattern, content, re.I):
        content = _without_example_fences(content)
        visible = _without_example_fences(visible)
    explicit_html = bool(re.match(document_pattern, content, re.I))
    root = None
    if re.search(r'<(?:html|body|main|div|table|section|article|ul|ol)\b', content, re.I):
        from .dynamic_lists import _scoped_html
        soup = _scoped_html(content)
        root = soup
        if scope.get('root_selector'):
            roots = soup.select(scope['root_selector'])
            if len(roots) <= scope['root_index']:
                if explicit_html:
                    return []
                root = None
            else:
                root = roots[scope['root_index']]
        elif not explicit_html and not soup.select(scope['row_selector']):
            root = None
        if root is not None and not explicit_html and not root.select(scope['row_selector']):
            # Documentation may mention an inline <table> tag. That mention
            # has no rows and must not hide the actual Markdown table.
            root = None
    if root is not None:
        if scope.get('exclude_selector'):
            for auxiliary in root.select(scope['exclude_selector']):
                auxiliary.decompose()
        for row in root.select(scope['row_selector']):
            if all('selector' in c for c in columns):
                fields = {}
                for col in columns:
                    nodes = [row] if col['selector'] == ':scope' else row.select(col['selector'])
                    if len(nodes) != 1:
                        continue
                    node = nodes[0]
                    fields[col['name']] = (' '.join(node.find_all(string=True, recursive=False)).strip()
                                           if col.get('own_text') else node.get_text(' ', strip=True))
                candidates.append(fields)
                continue
            cells = [row] if scope['cell_selector'] == ':scope' else row.select(scope['cell_selector'])
            # Cell boundaries are stronger than guessed text positions.
            if len(cells) == len(columns):
                candidates.append(dict(zip(names, (c.get_text(' ', strip=True) for c in cells))))
        if not explicit_html and not candidates:
            root = None
    if root is None:
        # Inline layout breaks do not create new Markdown rows/cells. Preserve
        # a space so split numeric tokens cannot become a different valid value.
        visible = re.sub(r'<br\s*/?>', ' ', visible, flags=re.I)
        # Only source-verified, case-declared auxiliary text is removed.
        # This also normalizes headers before locating the target table.
        for pattern in scope.get('plain_text_ignored_patterns', []):
            visible = re.sub(pattern, '', visible, flags=re.M)
        start = re.search(scope['start_pattern'], visible, re.M)
        if not start:
            return []
        text = visible[start.end():]
        end = re.search(scope['end_pattern'], text, re.M)
        if end:
            text = text[:end.start()]
        if scope.get('plain_multiline_row_pattern'):
            # Only a declared, bounded row shape may join lines. Never flatten
            # the document and borrow missing values from the following row.
            def capture(match):
                candidates.append(match.groupdict())
                return '\n' * match.group().count('\n')
            text = re.sub(scope['plain_multiline_row_pattern'], capture, text)
        lines = text.splitlines()
        aliases = {c['label'].casefold(): c['name'] for c in columns}
        aliases.update({n.casefold(): n for n in names})
        occupied = set()
        table_count = 0
        for i in range(len(lines) - 1):
            if i in occupied or '|' not in lines[i]:
                continue
            labels = [s.strip().casefold() for s in _pipe_cells(lines[i])]
            ignored = {v.casefold() for v in scope.get('ignored_column_labels', [])}
            selected = [j for j, label in enumerate(labels) if label not in ignored]
            headings = [aliases.get(labels[j]) for j in selected]
            separators = _pipe_cells(lines[i+1])
            if (set(headings) != set(names) or len(headings) != len(names)
                    or len(separators) != len(labels)
                    or not all(re.fullmatch(r'\s*:?-{3,}:?\s*', s) for s in separators)):
                continue
            j = i + 2
            while j < len(lines) and '|' in lines[j]:
                values = [v.strip() for v in _pipe_cells(lines[j])]
                if len(values) == len(labels):
                    candidates.append(dict(zip(headings, (values[j] for j in selected))))
                occupied.add(j); j += 1
            table_count += 1
            if table_count == scope.get('markdown_table_limit'):
                # Later examples with the same headers are separate tables.
                # Exclude their lines from the plain-row fallback as well.
                occupied.update(range(j, len(lines)))
                break
        pattern = re.compile(scope['plain_row_pattern'])
        for i, line in enumerate(lines):
            if i not in occupied and (match := pattern.fullmatch(line.strip())):
                candidates.append(match.groupdict())
    expected = {tuple(_canonical(row[c['name']], c) for c in columns)
                for row in scope.get('reference_rows', [])}
    records = []
    for fields in candidates:
        errors = ['invalid_field:'+c['name'] for c in columns if not _valid(fields.get(c['name']), c)]
        if not errors and expected and tuple(_canonical(fields[c['name']], c) for c in columns) not in expected:
            errors.append('fixed_demo_field_values_mismatch')
        records.append({'fields': fields, 'valid': not errors, 'errors': errors})
    return records


def _pipe_cells(line):
    # Remove only the table's outer delimiters; adjacent pipes are empty cells.
    return line.strip().removeprefix('|').removesuffix('|').split('|')
