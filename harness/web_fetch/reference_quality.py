"""Deterministic reference preparation; original source bytes remain evidence."""
import re


def clean_reference_text(text: str) -> str:
    # Explicit list, not all Unicode format characters: ZWJ/ZWNJ can carry meaning.
    return text.translate({0x200B:None, 0xFEFF:None, 0x00AD:None})


def visible_markdown_links(text: str) -> str:
    """Keep labels and adjacent link boundaries, consuming balanced destinations."""
    result=[];start=0;previous_label=''
    for match in re.finditer(r'!?\[([^\]\n]*)\]\(',text):
        if match.start()<start:continue
        end=match.end();depth=1
        while end<len(text) and depth:
            char=text[end]
            if char=='\\':end+=2;continue
            if char=='\n':break
            if char=='(':depth+=1
            elif char==')':depth-=1
            end+=1
        if depth:continue
        # Distinct adjacent link labels retain a token boundary. Plain text
        # (including a link inside a word) is never split or re-segmented.
        gap = text[start:match.start()]
        if match.start()==start and previous_label and match[1]:
            gap=' '
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
