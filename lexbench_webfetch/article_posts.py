"""Post ownership checks for fixed initial coverage contracts."""
import re
import unicodedata
from urllib.parse import urljoin, urlsplit

import soupsieve

from .body_coverage import tokens
from .dynamic_lists import named_entry_identity, visible_html_text
from .article_text import (article_html, is_article_html, article_markdown, TIMESTAMP_LINE,
                           mask_markdown_code, author_key, label_key, validate_labels, declared_labels)
from .reference_quality import markdown_links, visible_markdown_links


def validate_post_contract(contract):
    """Raise ValueError for malformed frozen post extraction contracts."""
    try:
        allowed = {'version', 'hosts', 'entry_path', 'html_record_selector',
                   'html_permalink_selector', 'html_author_selector', 'html_body_selector',
                   'html_quote_selector', 'quote_markers',
                   'allow_empty_outer_with_quotes', 'minimum_posts', 'ignored_labels', 'unavailable_notices', 'non_author_labels'}
        if not isinstance(contract, dict) or set(contract) - allowed:
            raise ValueError('unknown frozen post contract fields')
        if type(contract['version']) is not int or contract['version'] != 2 or not isinstance(contract['hosts'], list) or not contract['hosts']:
            raise ValueError('invalid version or hosts')
        if any(not isinstance(h, str) or not h or '/' in h for h in contract['hosts']):
            raise ValueError('invalid hosts')
        re.compile(contract['entry_path'])
        for key in ('html_record_selector', 'html_permalink_selector', 'html_author_selector', 'html_body_selector'):
            if not isinstance(contract[key], str) or not contract[key].strip():
                raise ValueError('empty selector')
            soupsieve.compile(contract[key])
        soupsieve.compile(contract.get('html_quote_selector', 'blockquote'))
        if type(contract.get('minimum_posts')) is not int or contract['minimum_posts'] < 1:
            raise ValueError('invalid fixed coverage bound')
        if type(contract.get('allow_empty_outer_with_quotes', False)) is not bool:
            raise ValueError('invalid empty outer opt-in')
        validate_labels(contract, ('ignored_labels', 'unavailable_notices', 'non_author_labels'))
        markers = contract.get('quote_markers', [])
        if not isinstance(markers, list) or any(not isinstance(x, str) or not x.strip() for x in markers):
            raise ValueError('invalid quote markers')
    except (KeyError, TypeError, re.error, soupsieve.SelectorSyntaxError) as error:
        raise ValueError('invalid post contract') from error


def post_identity(value, contract, base):
    destination = urljoin(base, value)
    p = urlsplit(destination)
    p.port  # Force validation; hostname alone accepts malformed port text.
    if (p.scheme not in {'http', 'https'} or p.hostname not in contract['hosts']
            or p.username or p.password or not re.fullmatch(contract['entry_path'], p.path.rstrip('/'))):
        raise ValueError('invalid post URL')
    return named_entry_identity(destination, contract)


def _owned_body_text(node, contract):
    surface = article_html(str(node))
    # Field-node ownership alone does not exclude a nested card's text.
    for nested in list(surface.select(contract['html_record_selector'])):
        if nested.parent is not None:
            nested.decompose()
    return visible_html_text(str(surface))


def _html_records(content, contract):
    soup, rows = article_html(content), []
    selector = contract.get('html_quote_selector', 'blockquote')
    for card in soup.select(contract['html_record_selector']):
        def owned(node):
            return node.find_parent(lambda x: soupsieve.match(contract['html_record_selector'], x)) is card
        quotes = [x for x in card.select(selector) if owned(x)
                  and not x.find_parent(lambda p: p is not card and soupsieve.match(selector, p))]
        def outer(node):
            return owned(node) and not any(node is q or q in node.parents for q in quotes)
        fields = [[x for x in card.select(contract[key]) if outer(x)] for key in
                  ('html_permalink_selector', 'html_author_selector', 'html_body_selector')]
        optional_body = contract.get('allow_empty_outer_with_quotes', False)
        if (any(len(x) != 1 for x in fields[:2])
                or len(fields[2]) != 1 and not (optional_body and not fields[2])):
            rows.append({})
            continue
        link, author = [x[0] for x in fields[:2]]
        body = fields[2][0] if fields[2] else None
        quote_text = [_owned_body_text(x, contract) for x in quotes]
        quote_authors = [[a.get_text(' ', strip=True)
                          for a in x.select(contract['html_author_selector'])
                          if owned(a) and a.find_parent(
                              lambda p: soupsieve.match(selector, p)) is x]
                         for x in quotes]
        quote_bodies = []
        for quote in quotes:
            # Prefer an explicit quote body field. If the site's quote uses
            # a different shape, exclude attribution and interface fields
            # before accepting its remaining visible text as prose.
            bodies = [node for node in quote.select(contract['html_body_selector'])
                      if owned(node) and node.find_parent(
                          lambda parent: soupsieve.match(selector, parent)) is quote]
            copy = article_html(str(bodies[0] if len(bodies) == 1 else quote))
            metadata = (contract['html_author_selector'] + ', '
                        + contract['html_permalink_selector']
                        + ', time, button, [role="button"]')
            for field in list(copy.select(metadata)):
                if field.parent is not None:
                    field.decompose()
            quote_bodies.append(_owned_body_text(copy, contract))
            quote.extract()
        rows.append({'url': link.get('href', ''), 'author': author.get_text(' ', strip=True),
                     'post': _owned_body_text(body, contract) if body is not None else '', 'quote': quote_text,
                     'quote_bodies_after_author': quote_bodies,
                     'quote_authors': quote_authors})
    return rows


def _post_headers(lines, contract, url):
    """Identify owned permalink headings before parsing their body ranges."""
    headers = []
    header_level = None
    for index, line in enumerate(lines):
        heading = re.match(r'^\s*(#{1,6})\s+', line)
        links = [link for link in markdown_links(line) if not link.is_image]
        if not links or line.lstrip().startswith('>'):
            if heading and header_level is not None and len(heading[1]) <= header_level:
                break
            continue
        if header_level is None and heading:
            header_level = len(heading[1])
        if len(links) > 1:
            first, last = links[0], links[-1]
            prefix, suffix = line[:first.start].strip(), line[last.end:].strip()
            if (not re.match(r'^(?:#{1,6}|[-*+]|\d+[.)])(?:\s|$)', prefix)
                    or suffix.strip(' |—–-:*_')):
                continue
            author = visible_markdown_links(first.label)
            destination = 'about:invalid-post-heading'
            # A linked author profile may precede exactly one owning post
            # permalink. Other explicit linked headings still end the prior
            # record, but cannot supply a valid record of their own.
            if (len(links) == 2 and re.fullmatch(r'(?:#{1,6}|[-*+]|\d+[.)])', prefix)
                    and not line[first.end:last.start].strip(' |·—–-:*_')):
                try:
                    profile = urlsplit(urljoin(url, first.destination))
                    profile.port
                    if (profile.scheme in {'http', 'https'}
                            and profile.hostname in contract['hosts']
                            and not profile.username and not profile.password
                            and not re.fullmatch(contract['entry_path'], profile.path.rstrip('/'))):
                        post_identity(last.destination, contract, url)
                        destination = last.destination
                except ValueError:
                    pass
            headers.append((index, destination, visible_markdown_links(line), author))
            continue
        link = links[0]
        prefix, suffix = line[:link.start].strip(), line[link.end:].strip()
        explicit_header = bool(re.match(r'^(?:#{1,6}|[-*+]|\d+[.)])(?:\s|$)', prefix))
        if suffix.strip(' |—–-:*_') or (prefix and not explicit_header):
            continue
        try:
            post_identity(link.destination, contract, url)
        except ValueError:
            # An explicit linked post heading still ends the previous post,
            # even when its own URL is invalid. Ordinary inline links do not.
            if not explicit_header:
                continue
        header = visible_markdown_links(line)
        author = re.sub(r'^(?:#{1,6}|[-*+]|\d+[.)])(?:\s+|$)', '', prefix).strip()
        if not author:
            author = visible_markdown_links(link.label)
        if not prefix and index and lines[index-1].strip() and not lines[index-1].startswith('>'):
            author = visible_markdown_links(lines[index-1])
            header = author+'\n'+header
        headers.append((index, link.destination, header, author))
    return headers


def _markdown_records(content, contract, url):
    # Fenced source snippets cannot introduce post identities or supply post prose.
    # Comments keep code as owned text; posts deliberately require unfenced prose.
    content, _ = mask_markdown_code(content)
    # HTML visibility projection drops empty lines. Preserve Markdown paragraph
    # boundaries across that projection so a blockquote cannot absorb a later
    # unquoted paragraph. Choose a marker absent from the answer itself.
    paragraph_marker = '\ue000post-paragraph-break\ue001'
    while paragraph_marker in content:
        paragraph_marker += '_'
    content = re.sub(r'\n[ \t]*\n', '\n'+paragraph_marker+'\n', content)
    content = visible_html_text(content, preserve_source_whitespace=True, preserve_links=True)
    content = content.replace(paragraph_marker, '')
    lines = content.splitlines()
    headers = _post_headers(lines, contract, url)
    rows = []
    markers = {x.casefold().rstrip(':：') for x in contract.get('quote_markers', [])}
    for n, (start, destination, header, author) in enumerate(headers):
        end = headers[n+1][0] if n+1 < len(headers) else len(lines)
        post, quotes, current = [], [], None
        quote_mode, paragraph_break = None, False
        header_heading = re.match(r'^\s*(#{1,6})\s+', lines[start])
        for line in lines[start+1:end]:
            heading = re.match(r'^\s*(#{1,6})\s+', line)
            if heading and header_heading and len(heading[1]) <= len(header_heading[1]):
                break
            if line.strip().strip('*_').rstrip(':：').casefold() in markers:
                if current is not None:
                    quotes.append('\n'.join(current))
                current = []
                quote_mode, paragraph_break = 'marker', False
            elif line.lstrip().startswith('>'):
                if current is None:
                    current = []
                    quote_mode = 'blockquote'
                paragraph_break = False
                current.append(re.sub(r'^\s*>\s?', '', line))
            elif current is not None:
                if quote_mode == 'blockquote' and paragraph_break and line.strip():
                    # Lazy continuation belongs to the current paragraph; a
                    # new unquoted paragraph belongs to the owning outer post.
                    quotes.append('\n'.join(current))
                    current, quote_mode = None, None
                    post.append(line)
                else:
                    current.append(line)
                    if not line.strip():
                        paragraph_break = True
            else:
                post.append(line)
        if current is not None:
            quotes.append('\n'.join(current))
        rows.append({'url': destination, 'author_header': header, 'author': author,
                     'whole_post': visible_markdown_links('\n'.join(lines[start+1:end])),
                     'post': visible_markdown_links('\n'.join(post)),
                     'quote': [visible_markdown_links(x) for x in quotes],
                     'quote_authors': [[visible_markdown_links(next(
                         (line.strip() for line in x.splitlines() if line.strip()), ''))]
                                       for x in quotes],
                     'quote_bodies_after_author': [visible_markdown_links('\n'.join(
                         x.lstrip().splitlines()[1:])) for x in quotes]})
    return rows


def _initial_surface(content):
    """Apply shared closed-disclosure visibility to mixed representations."""
    return str(article_html(content)) if re.search(r'<details\b', content, re.I) else content


def _has_post_prose(text, contract):
    """Reject metadata-only serialized fields, without filtering prose mentions.

    This narrow whole-line filter cannot distinguish date-only prose from a
    serializer timestamp; it does not authenticate or compare dynamic text.
    """
    controls = declared_labels(contract, "ignored_labels")
    unavailable = declared_labels(contract, "unavailable_notices")
    for line in text.splitlines():
        line = unicodedata.normalize('NFKC', visible_markdown_links(line)).strip()
        if not line or label_key(line) in unavailable:
            continue
        if TIMESTAMP_LINE.fullmatch(line):
            continue
        labels = [label_key(part)
                  for part in re.split(r'\s*[|·•]\s*', line)]
        if all(label_key(re.sub(r'(?:^\d+\s*|\s*\d+$)', '', label)) in controls
               for label in labels):
            continue
        return True
    return False


def _valid_post_author(value, contract):
    if author_key(value) is None:
        return False
    label = label_key(value)
    return (label not in declared_labels(contract, 'non_author_labels')
            and label not in declared_labels(contract, 'ignored_labels')
            and label not in declared_labels(contract, 'unavailable_notices')
            and not TIMESTAMP_LINE.fullmatch(label))


def _verify_fixed_posts(content, contract, url):
    content = _initial_surface(content)
    if is_article_html(content):
        native = article_html(content).select(contract['html_record_selector'])
        rows = (_html_records(content, contract) if native
                else _markdown_records(article_markdown(content), contract, url))
    else:
        rows = _markdown_records(content, contract, url)
    seen, checks = set(), []
    for row in rows:
        check = {'url': row.get('url'), 'pass': False}
        try:
            key = post_identity(row['url'], contract, url)
            author_pass = _valid_post_author(row.get('author', ''), contract)
            body_pass = _has_post_prose(row.get('post', ''), contract)
            attributed_quote = any(
                _has_post_prose(text, contract) and len(authors) == 1 and _valid_post_author(authors[0], contract)
                for text, authors in zip(row.get('quote_bodies_after_author', []),
                                         row.get('quote_authors', [])))
            check.update(author_pass=author_pass, body_pass=body_pass,
                         attributed_quote_pass=attributed_quote)
            if key in seen:
                check['reason'] = 'duplicate'
            elif not author_pass:
                check['reason'] = 'missing_author'
            elif not body_pass and not (contract.get('allow_empty_outer_with_quotes', False)
                                       and attributed_quote):
                check['reason'] = 'missing_body'
            else:
                check['pass'] = True
                seen.add(key)
        except (KeyError, ValueError):
            check['reason'] = 'invalid_record'
        checks.append(check)
    confirmed = sum(row['pass'] for row in checks)
    return {'article_posts_mode': 'fixed_coverage',
            'article_posts_pass': confirmed >= contract['minimum_posts'],
            'article_post_results': checks, 'article_post_count': len(rows),
            'article_post_confirmed_count': confirmed,
            'article_post_minimum_count': contract['minimum_posts']}


def verify_article_posts(content, contract, *, url):
    """Check fixed visible-post coverage without a runtime reference."""
    validate_post_contract(contract)
    return _verify_fixed_posts(content, contract, url)
