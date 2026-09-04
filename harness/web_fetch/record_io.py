"""Read append-only measurement records without hiding completed-line corruption."""
import json


def read_completed_records(path):
    """Only an unterminated final JSON fragment may be concurrently flushing."""
    lines=path.read_text().splitlines(keepends=True)
    records=[]
    for index,line in enumerate(lines):
        try:records.append(json.loads(line))
        except json.JSONDecodeError:
            if index != len(lines)-1 or line.endswith(('\n','\r')):
                raise
    return records
