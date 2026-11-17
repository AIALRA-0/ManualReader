"""Find physical file ranges that can be tied to directly explained sources."""

from base64 import b64decode
from binascii import Error as Base64Error
from collections import defaultdict
from hashlib import sha256
import json
import re
from struct import unpack_from
from unicodedata import normalize
from zipfile import BadZipFile
from xml.etree.ElementTree import ParseError

from ..step03.documents import checked_zip, nodes, tag, xml


def unique_span(blob, value):
    start = blob.find(value)
    if start != -1 and blob.find(value, start + 1) == -1:
        return start, start + len(value)
    return None


def embedded_image_ranges(blob):
    ranges = defaultdict(list)
    expression = rb"""<img\b[^>]*?\s+src\s*=\s*(["'])data:image/[a-z0-9.+-]+;base64,([A-Za-z0-9+/=]+)\1"""
    for match in re.finditer(expression, blob, re.IGNORECASE):
        try:
            checksum = sha256(b64decode(match[2], validate=True)).hexdigest()
        except Base64Error:
            continue
        ranges[checksum].append(match.span(2))
    return {key: spans[0] for key, spans in ranges.items() if len(spans) == 1}


def text_ranges(blob, suffix, rows):
    images = embedded_image_ranges(blob) if suffix in ('.htm', '.html') else {}
    ranges = {}
    for row in rows:
        text = row['en'].strip()
        candidates = [text]
        if suffix == '.json':
            candidates.extend(line.strip() for line in text.splitlines())
            candidates.extend(json.dumps(line, ensure_ascii=False)[1:-1] for line in tuple(candidates))
        spans = {span for item in candidates if len(raw := item.encode('utf-8')) >= 24
                 and (span := unique_span(blob, raw))}
        if row.get('kind') == 'image' and row.get('asset_sha') in images:
            spans.add(images[row['asset_sha']])
        if spans:
            ranges[row['id']] = spans
    return ranges


def letters(text):
    return re.sub(r'\s+', '', normalize('NFC', text))


def positioned_rows(rows, kind):
    positions = defaultdict(list)
    for row in rows:
        point = row.get('position') or {}
        if point.get('type') != kind:
            continue
        start, end = point.get('start'), point.get('end')
        if type(start) is int and type(end) is int and start == end and start > 0:
            positions[start].append(row)
    return positions


def word_main_part(blob, rows):
    # A compressed XML member has no separate original byte range for a paragraph.
    attached = positioned_rows(rows, 'document_paragraph')
    try:
        archive, _ = checked_zip(blob)
        with archive:
            member = archive.getinfo('word/document.xml')
            root = xml(archive, member.filename)
            if any(tag(node) in ('drawing', 'pict', 'altChunk', 'object', 'subDoc') for node in root.iter()):
                return {}
            sources = []
            for rank, paragraph in enumerate(nodes(root, 'p'), 1):
                text = letters(''.join(node.text or '' for node in nodes(paragraph, 't')))
                if not text:
                    continue
                matches = [row['id'] for row in attached.get(rank, []) if text == letters(row['en'])]
                if not matches:
                    return {}
                sources.extend(matches)
            offset = member.header_offset
            if (not sources or member.flag_bits & 1 or offset < 0
                    or offset + 30 > len(blob) or blob[offset:offset + 4] != b'PK\x03\x04'):
                return {}
            name_size, extra_size = unpack_from('<HH', blob, offset + 26)
            start = offset + 30 + name_size + extra_size
            end = start + member.compress_size
            return {source_id: {(start, end)} for source_id in sources} if start < end <= len(blob) else {}
    except (ValueError, KeyError, OSError, BadZipFile, ParseError, RuntimeError):
        return {}


def pdf_content_streams(blob, rows):
    import fitz

    by_page = positioned_rows(rows, 'pdf_page')
    covered_pages = {}
    owners = defaultdict(set)
    spans = defaultdict(set)
    try:
        with fitz.open(stream=blob, filetype='pdf') as pdf:
            if pdf.is_encrypted or pdf.is_repaired:
                return {}
            for page in pdf:
                number = page.number + 1
                for xref in page.get_contents():
                    owners[xref].add(number)
                if number not in by_page or page.get_image_info() or page.get_drawings() or page.get_xobjects():
                    # A shared stream can also contain objects that the text layer does not represent.
                    continue
                blocks = [letters(item[4]) for item in page.get_text('blocks') if item[6] == 0]
                matched = defaultdict(set)
                for block in filter(None, blocks):
                    for row in by_page[number]:
                        if block in letters(row['en']):
                            matched[block].add(row['id'])
                if blocks and all(matched[block] for block in blocks if block):
                    covered_pages[number] = set().union(*matched.values())
            for xref, pages in owners.items():
                if not pages <= covered_pages.keys():
                    continue
                raw = pdf.xref_stream_raw(xref)
                if not raw or not (span := unique_span(blob, raw)):
                    continue
                start, end = span
                # A matching byte string also has to be delimited as a stream in this PDF.
                if not blob[max(0, start - 8):start].endswith((b'stream\n', b'stream\r', b'stream\r\n')):
                    continue
                if not blob[end:end + 14].lstrip(b'\r\n').startswith(b'endstream'):
                    continue
                for number in pages:
                    for source_id in covered_pages[number]:
                        spans[source_id].add(span)
    except (ValueError, RuntimeError, OSError, IndexError):
        return {}
    return spans


def directly_explained_ranges(blob, suffix, rows):
    if suffix == '.pdf':
        return pdf_content_streams(blob, rows)
    if suffix == '.docx':
        return word_main_part(blob, rows)
    if suffix in ('.zip', '.png', '.jpg', '.jpeg', '.webp', '.pptx', '.xlsx'):
        return {}
    return text_ranges(blob, suffix, rows)
