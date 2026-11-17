"""Version-pinned views of project originals for the planning Agent."""
from base64 import b64encode
from collections import OrderedDict
from io import BytesIO
from hashlib import sha256
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from threading import RLock
from zipfile import ZipFile

from reader_step01_scan import CHUNK_BYTES, inside, signature

MAX_FILE = 64 * 1024 * 1024
LIMIT = 24000
_CACHE = OrderedDict()
_LOCK = RLock()


def pinned_bytes(root, file):
    if file['status'] != 'active' or file['identity_pending']:
        raise ValueError('access_blocked: 本轮尚不能读取这份原件')
    original = inside(Path(root).resolve(strict=True), file['relative_path'])
    import os
    info = original.lstat()
    if info.st_size > MAX_FILE:
        raise ValueError('access_blocked: 原件超过当前单文件读取上限')
    value = BytesIO()
    with original.open('rb') as stream:
        first = os.fstat(stream.fileno())
        if signature(first)[:-1] != signature(info)[:-1]:
            raise ValueError('access_blocked: 打开原件时文件发生变化')
        while content := stream.read(CHUNK_BYTES):
            value.write(content)
            if value.tell() > MAX_FILE:
                raise ValueError('access_blocked: 原件超过当前单文件读取上限')
        if signature(first) != signature(os.fstat(stream.fileno())):
            raise ValueError('access_blocked: 读取原件时文件发生变化')
    if (signature(info) != signature(inside(Path(root).resolve(), file['relative_path']).lstat())
            or value.tell() != file['size_bytes'] or sha256(value.getbuffer()).hexdigest() != file['content_sha256']):
        raise ValueError('access_blocked: 原件已不是 M1 记录的版本')
    return value.getvalue()


def seconds(value):
    fields = str(value).replace(',', '.').split(':')
    return sum(float(x) * 60 ** i for i, x in enumerate(reversed(fields)))


def view(raw, suffix):
    if suffix == '.pdf':
        import fitz
        with fitz.open(stream=raw, filetype='pdf') as doc:
            return 'pdf_page', [{'n': i + 1, 'text': page.get_text(sort=True)}
                                for i, page in enumerate(doc)]
    if suffix in ('.html', '.htm'):
        # The existing HTML walker defines domN. Run it on a staged local file without network.
        from source_parser import _html
        with TemporaryDirectory(prefix='reader-plan-dom-') as temp:
            folder = Path(temp)
            url = folder / 'original.html'
            url.write_bytes(raw)
            data = _html(url, 'step02-original', folder / 'assets')
            rows = {}
            for p in data['passages']:
                loc = p.get('source_location', {})
                atom_ids = loc.get('source_unit_ids') or p.get('source_ids') or []
                for sid in atom_ids:
                    if re.fullmatch(r'dom\d+', str(sid)):
                        n = int(sid[3:])
                        rows[n] = {'n': n, 'text': p.get('raw_text') or p.get('en') or p.get('alt') or '',
                                   'kind': p.get('kind')}
            if rows:
                return 'html_dom_unit', list(rows.values())
        # HTML compatibility for documents whose stored parser has no DOM locator metadata.
        from bs4 import BeautifulSoup
        page = BeautifulSoup(raw, 'html.parser')
        for item in page.find_all(['script', 'style', 'noscript', 'head', 'svg']):
            item.decompose()
        return 'html_text_line', [{'n': i, 'text': t} for i, t in enumerate(
            (line.strip() for line in page.get_text('\n').splitlines() if line.strip()), 1)]
    if suffix == '.json':
        text = json.loads(raw)
        rows = text.get('body') if isinstance(text, dict) else text
        if isinstance(rows, list) and rows and all(isinstance(x, dict) and 'content' in x and 'sid' in x for x in rows):
            return 'subtitle_sid', [{'n': int(x['sid']), 'text': str(x['content']),
                                     'time': [float(x['from']), float(x['to'])]} for x in rows]
    if suffix in ('.srt', '.vtt'):
        text = raw.decode('utf-8-sig')
        matches = list(re.finditer(r'(?m)^((?:\d+:)?\d{2}:\d{2}[,.]\d{3})\s*-->\s*((?:\d+:)?\d{2}:\d{2}[,.]\d{3})[^\n]*\n((?:[^\r\n][^\r\n]*(?:\r?\n|$))+)', text))
        if matches:
            return 'subtitle_sid', [{'n': i, 'text': m[3].strip(), 'time': [seconds(m[1]), seconds(m[2])]}
                                    for i, m in enumerate(matches, 1)]
    if suffix in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'):
        from PIL import Image
        with Image.open(BytesIO(raw)) as pic:
            return 'image', [{'n': 1, 'text': '', 'size': [pic.width, pic.height]}]
    if suffix in ('.docx', '.pptx', '.xlsx'):
        from xml.etree import ElementTree as ET
        with ZipFile(BytesIO(raw)) as z:
            if suffix == '.docx':
                part = 'word/document.xml'
                if z.getinfo(part).file_size > MAX_FILE:
                    raise ValueError('access_blocked: Word 正文超过读取上限')
                root = ET.fromstring(z.read(part))
                rows = [''.join(t.text or '' for t in p.iter() if t.tag.endswith('}t'))
                        for p in root.iter() if p.tag.endswith('}p')]
                return 'document_paragraph', [{'n': i, 'text': t} for i, t in enumerate(rows, 1)]
            pattern = r'ppt/slides/slide(\d+)\.xml' if suffix == '.pptx' else r'xl/worksheets/sheet(\d+)\.xml'
            rows = []
            for name in z.namelist():
                if (m := re.fullmatch(pattern, name)) and z.getinfo(name).file_size <= MAX_FILE:
                    root = ET.fromstring(z.read(name))
                    rows.append({'n': int(m[1]), 'text': '\n'.join(x.text or '' for x in root.iter() if x.text and x.text.strip())})
            return 'slide' if suffix == '.pptx' else 'sheet', sorted(rows, key=lambda r: r['n'])
    if suffix == '.zip':
        with ZipFile(BytesIO(raw)) as z:
            return 'archive_member', [{'n': i, 'text': f.filename, 'size': f.file_size}
                                      for i, f in enumerate((f for f in z.infolist() if not f.is_dir()), 1)]
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as error:
        raise ValueError('access_blocked: 暂无此原件格式的只读视图') from error
    if '\x00' in text:
        raise ValueError('access_blocked: 暂无此原件格式的只读视图')
    return 'text_line', [{'n': i, 'text': t} for i, t in enumerate(text.splitlines(), 1)]


def original(root, file):
    path = Path(file['relative_path'])
    # Recheck the content hash on every new read; cache only the view of those exact bytes.
    raw = pinned_bytes(root, file)
    key = file['content_sha256'], path.suffix.lower()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached:
            _CACHE.move_to_end(key)
    if cached:
        return raw, cached
    kind, rows = view(raw, key[1])
    if not rows:
        raise ValueError('access_blocked: 文件没有可定位的内容')
    numbers = [row['n'] for row in rows]
    if len(set(numbers)) != len(numbers) or any(type(n) is not int or n < 1 for n in numbers):
        raise ValueError('access_blocked: 原件位置重复或无效')
    with _LOCK:
        _CACHE[key] = (kind, rows)
        while len(_CACHE) > 12:
            _CACHE.popitem(last=False)
    return raw, (kind, rows)


def inventory(root, file):
    _, (kind, rows) = original(root, file)
    return {'file_id': file['file_id'], 'type': kind, 'positions': sorted(r['n'] for r in rows),
            'time_bounds': [min(x['time'][0] for x in rows), max(x['time'][1] for x in rows)]
            if kind == 'subtitle_sid' and all('time' in x for x in rows) else None,
            'headings': [r for r in rows if r.get('kind') == 'heading'][:100],
            'has_page_image': kind in ('pdf_page', 'image')}


def page_image(raw, kind, page):
    if kind == 'pdf_page':
        import fitz
        with fitz.open(stream=raw, filetype='pdf') as doc:
            return doc[page - 1].get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False).tobytes('png')
    if kind == 'image':
        from PIL import Image
        with Image.open(BytesIO(raw)) as picture:
            picture.thumbnail((1400, 1400))
            out = BytesIO()
            picture.convert('RGB').save(out, format='PNG')
            return out.getvalue()
    raise ValueError('这个原件位置没有可以直接读取的页面图像')


def read_region(root, file, start, end, visual=False, char_offset=0):
    raw, (kind, rows) = original(root, file)
    if type(start) is not int or type(end) is not int or not 1 <= start <= end or end - start > 299:
        raise ValueError('请指定有效的原件起止位置，每次最多读取 300 个位置')
    if type(char_offset) is not int or char_offset < 0 or char_offset and start != end:
        raise ValueError('字符续读只适用于一个有效的原件位置')
    valid = {r['n'] for r in rows}
    if start not in valid or end not in valid:
        raise ValueError('原件没有请求的起点或终点')
    supplied, size, more = [], 0, None
    for row in sorted((row for row in rows if start <= row['n'] <= end), key=lambda row: row['n']):
        n = len(row['text'])
        if supplied and size + n > LIMIT:
            more = row['n']
            break
        # A long single unit remains accessible using character pagination.
        block = row['text'][char_offset:char_offset+LIMIT]
        supplied.append({**row, 'text': block, 'text_chars': n, 'char_offset': char_offset})
        size += len(block)
    result = {'file_id': file['file_id'], 'type': kind, 'start': start, 'end': end, 'rows': supplied,
              'next_start': more, 'content_sha256': file['content_sha256'], 'file_revision': file['file_revision']}
    if visual:
        if start != end:
            raise ValueError('每次只读取一张原件页面图像')
        result['image'] = b64encode(page_image(raw, kind, start)).decode()
    return result
