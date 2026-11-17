from hashlib import sha256
import re


FENCE = re.compile(r'^\s*(`{3,}|~{3,})')


def anchors(markdown):
    """Return Python character offsets into the original UTF-8 draft."""
    rows = []
    start = pos = 0
    closing = None

    def append(end):
        text = markdown[start:end].rstrip('\r\n')
        if text.strip():
            digest = sha256(text.encode()).hexdigest()
            rows.append({'anchor_id':'tmp-'+sha256(f'{start}:{digest}'.encode()).hexdigest()[:16],
                         'start':start,'end':start+len(text),'content_sha256':digest,'text':text})

    for line in markdown.splitlines(keepends=True):
        marker = FENCE.match(line)
        stripped = line.strip()
        if not stripped and not closing:
            append(pos)
            start = pos+len(line)
        elif closing:
            if stripped.startswith(closing) and (closing[0] in '`~' or stripped==closing):
                closing = None
        elif marker:
            closing = marker[1]
        elif stripped in ('$$',r'\['):
            closing = '$$' if stripped=='$$' else r'\]'
        pos += len(line)
    append(pos)
    return rows
