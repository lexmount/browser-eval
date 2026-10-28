"""HTML resource URL syntax shared by projections and scoring."""
import re


def srcset_urls(value: str) -> list[str]:
    """Read URL tokens without treating internal commas as separators.

    HTML srcset URLs end at ASCII whitespace. Trailing commas delimit a
    descriptorless candidate; commas inside a URL (including data URLs) do not.
    Descriptors are consumed to the next comma outside parentheses.
    """
    urls = []
    position = 0
    while position < len(value):
        while position < len(value) and value[position] in ' \t\n\r\f,':
            position += 1
        match = re.match(r'[^ \t\n\r\f]+', value[position:])
        if match is None:
            break
        token = match[0]
        position += len(token)
        urls.append(token.rstrip(','))
        if token.endswith(','):
            continue
        depth = 0
        while position < len(value):
            character = value[position]
            position += 1
            if character == '(':
                depth += 1
            elif character == ')' and depth:
                depth -= 1
            elif character == ',' and not depth:
                break
    return urls
