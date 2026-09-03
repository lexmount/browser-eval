"""Field contracts for independently observed, finite dynamic practice pages."""
import re


def entry_field_orders(fields):
    """Supported visible record layouts, independent of provider and case IDs.

    These are tuples, not arbitrary permutations or substring windows. Shipping
    badges precede product names in DOM extraction and follow prices in captures.
    Unknown schemas fail closed in the entry matcher.
    """
    fields = frozenset(fields)
    layouts = [
        ('quote', 'author', 'tags'),
        ('name', 'price'),
        ('price', 'title', 'description', 'reviews'),
        ('title', 'price', 'description', 'reviews'),
        ('date', 'text'),
        ('name', 'description'),
        ('method', 'path', 'summary'),
        ('family',),
    ]
    if {'name', 'price'} <= fields <= {'name', 'price', 'financing', 'shipping'}:
        body = tuple(f for f in ('name', 'price', 'financing') if f in fields)
        return [body + ('shipping',), ('shipping',) + body] if 'shipping' in fields else [body]
    return [layout for layout in layouts if frozenset(layout) == fields]


def entry_facts(case_id, entries):
    fields = (['quote', 'author', 'tags'] if case_id in {'dynamic-quotes-api','dynamic-delayed'}
              else ['name','price'] if case_id=='dynamic-products-api'
              else ['price','title','description','reviews'] if case_id=='dynamic-catalog'
              else ['date','text'] if case_id in {'dynamic-reviews-graphql','dynamic-product-reviews'}
              else ['name','description'] if case_id=='dynamic-angular-phones'
              else ['method','path','summary'] if case_id=='dynamic-swagger'
              else ['family'] if case_id=='dynamic-iframe'
              else ['name','price','financing','shipping'] if case_id=='dynamic-react-cart' else None)
    if not fields:raise ValueError('Unknown dynamic case')
    facts=[]
    for i,entry in enumerate(entries,1):
        values=[line.strip() for line in entry.splitlines() if line.strip()]
        entry_fields = fields
        if case_id == 'dynamic-react-cart':
            # Shipping and financing are optional visible fields on these cards.
            entry_fields = ['name', 'price']
            if len(values)>2 and values[2].startswith('or '):entry_fields.append('financing')
            if values[-1]=='Free shipping':entry_fields.append('shipping')
        if len(values)!=len(entry_fields):raise ValueError(f'Incomplete or unexpected fields: {case_id} entry {i}')
        for field,value in zip(entry_fields,values):
            if field=='author':value=value.removeprefix('by ')
            if field=='tags':value=value.removeprefix('Tags: ')
            alternatives=[value]
            # Numeric count + its label can be adjacent in Markdown extraction.
            # Keep the whole number/unit, never remove arbitrary word boundaries.
            if field=='reviews' and re.fullmatch(r'\d+\s+reviews?',value):
                alternatives.append(re.sub(r'\s+','',value))
            facts.append({'id':f'entry-{i}-{field}','alternatives':alternatives})
    return facts
