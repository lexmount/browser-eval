"""Deterministic reference preparation; original source bytes remain evidence."""
import re


def clean_reference_text(text: str) -> str:
    # Explicit list, not all Unicode format characters: ZWJ/ZWNJ can carry meaning.
    return text.translate({0x200B:None, 0xFEFF:None, 0x00AD:None})


def visible_markdown_links(text: str) -> str:
    """Keep labels and adjacent link boundaries, consuming balanced destinations.

    Browser Markdown serializers sometimes wrap a generated destination or its
    title onto the next line.  The visible label is still page text, so layout
    newlines inside a bounded, balanced destination must not turn it into a
    false negative.
    """
    result=[];start=0;previous_label=''
    for match in re.finditer(r'!?\[([^\]\n]*)\]\(',text):
        if match.start()<start:continue
        end=match.end();depth=1
        while end<len(text) and depth and end-match.end() <= 2048:
            char=text[end]
            if char=='\\':end+=2;continue
            if char=='(':depth+=1
            elif char==')':depth-=1
            end+=1
        if depth:continue
        # Distinct adjacent link labels retain a token boundary. Some native
        # serializers also drop a real DOM text boundary immediately before a
        # path link or a multi-word link label (``out[/r/x]`` / ``or[Stack
        # Overflow]``). Restore only those unambiguous boundaries; ordinary
        # links embedded in a word (``pre[fix]`` / ``[read]able``) stay joined.
        gap = text[start:match.start()]
        if match.start()==start and previous_label and match[1]:
            # A numeric value may span links: never turn -10, 10.5 or 100
            # into a separately matchable positive 10 by adding whitespace.
            numeric = lambda char: char.isdigit() or char in '+-.−'
            gap='' if numeric(previous_label[-1]) and numeric(match[1][0]) else ' '
        else:
            separated = lambda label: label.startswith(('/', '#')) or any(char.isspace() for char in label)
            if (previous_label and gap and not gap[0].isspace()
                    and separated(previous_label) and gap[0].isalnum()):
                gap=' '+gap
            if (gap and match[1] and not gap[-1].isspace()
                    and separated(match[1]) and (gap[-1].isalnum() or gap[-1] in ',.;:!?')):
                gap+=' '
        result.extend([gap,match[1]])
        previous_label=match[1]
        start=end
    result.append(text[start:])
    return ''.join(result)


def audit_reference(text, facts, *, normalize):
    issues=[]
    if not text.strip(): issues.append('empty_reference')
    if '\ufffd' in text: issues.append('replacement_character')
    if re.search(r'此处是\{\{icon_[a-z_]+\}\}icon',text): issues.append('unresolved_renderer_placeholder')
    if re.search(r'[\x00-\x08\x0b\x0e-\x1f\x7f]',text): issues.append('unexpected_control_character')
    if clean_reference_text(text)!=text: issues.append('layout_characters')
    seen=set();folded=normalize(text)
    for fact in facts:
        alternatives={normalize(a) for a in fact.get('alternatives',[])}-{''}
        if not alternatives:issues.append('empty_fact:'+fact['id'])
        elif not any(a in folded for a in alternatives):issues.append('fact_absent_from_reference:'+fact['id'])
        key=tuple(sorted(alternatives))
        if key in seen:issues.append('duplicate_fact:'+fact['id'])
        seen.add(key)
    return issues
