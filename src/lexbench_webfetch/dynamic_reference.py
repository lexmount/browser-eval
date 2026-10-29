"""Shared field layouts for owned record matching."""


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
