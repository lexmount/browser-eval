"""Field contracts for independently observed, finite dynamic practice pages."""
import re


def entry_facts(case_id, entries):
    fields = (['quote', 'author', 'tags'] if case_id in {'dynamic-quotes-api','dynamic-delayed'}
              else ['name','price'] if case_id=='dynamic-products-api'
              else ['price','title','description','reviews'] if case_id=='dynamic-catalog' else None)
    if not fields:raise ValueError('Unknown dynamic case')
    facts=[]
    for i,entry in enumerate(entries,1):
        values=[line.strip() for line in entry.splitlines() if line.strip()]
        if len(values)!=len(fields):raise ValueError(f'Incomplete or unexpected fields: {case_id} entry {i}')
        for field,value in zip(fields,values):
            if field=='author':value=value.removeprefix('by ')
            if field=='tags':value=value.removeprefix('Tags: ')
            alternatives=[value]
            # Numeric count + its label can be adjacent in Markdown extraction.
            # Keep the whole number/unit, never remove arbitrary word boundaries.
            if field=='reviews' and re.fullmatch(r'\d+\s+reviews?',value):
                alternatives.append(re.sub(r'\s+','',value))
            facts.append({'id':f'entry-{i}-{field}','alternatives':alternatives})
    return facts
