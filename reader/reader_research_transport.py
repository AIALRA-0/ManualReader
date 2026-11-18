"""搜索先取得简短候选；打开来源、看图和查找具体证据仍保留调用者请求的范围。"""
import copy,json,re

from reader_web_sources import HEADER, SOURCE, pieces


WEB_CALL = re.compile(r'\btools\s*\.\s*(?:web__run|web\.run)\s*\(')
SOURCE_LINE = re.compile(r'(?m)^L(\d+(?:@P\d+)?):[ \t]*([^\r\n]*)(?:\r?\n|$)')
DISCOVERY = re.compile(r'\bsearch_query["\']?\s*:')
EVIDENCE_CALL = re.compile(r'\b(?:open|find|click|screenshot|image_query|literature_search_query)["\']?\s*:')
SEARCH_REFERENCE = re.compile(r'\bturn\w*search\d+\b')


def _join_character_output(text):
    # functions.exec can format each character as a separate output item if its caller loops over a string.
    heading = re.match(r'(?:Warning: truncated output[^\n]*\n)?Total output lines: [\d,]+\r?\n\r?\n', text)
    if not heading:
        return text
    payload = text[heading.end():]
    if payload.count('\n')<200:
        return text
    parts = re.split(r'(…[\d,]+ tokens truncated…)', payload)
    decoded = []
    for i, part in enumerate(parts):
        if i%2:
            decoded.append(part)
            continue
        if i and part.startswith('\n'):
            part = part[1:]
        if any(char!='\n' for char in part[1::2]):
            return text
        decoded.append(part[::2])
    body = ''.join(decoded)
    if not re.search(r'https?://|Source:\s*(?:open|find|click)\(', body):
        return text
    warning = 'The tool output was truncated. Reopen the missing lines if needed.\n' if len(parts)>1 or heading[0].startswith('Warning:') else ''
    return warning + body


def _deduplicate_source_lines(text, seen):
    blocks = pieces(text)
    for i, block in enumerate(blocks):
        header = HEADER.search(block)
        if not header or not SOURCE.search(block):
            continue
        url = header[2]
        omitted = []

        def retain(line):
            number, body = line.groups()
            key = url, number, body
            if len(body)<24:
                return line[0]
            if key in seen:
                omitted.append(number)
                return ''
            seen.add(key)
            return line[0]

        compact = SOURCE_LINE.sub(retain, block)
        if omitted:
            blocks[i] = compact.rstrip() + '\nRepeated source lines already present above: ' + ', '.join(omitted) + '\n\n'
    return ''.join(blocks)


def _discovery_snippets(text):
    """搜索只用于挑来源。保留原题名、地址、引用编号和摘要首行。"""
    headers = list(HEADER.finditer(text))
    if not headers or SOURCE.search(text):
        return text
    chunks = [text[:headers[0].start()]]
    for index, header in enumerate(headers):
        end = headers[index+1].start() if index+1<len(headers) else len(text)
        snippet = text[header.end():end].lstrip('\r\n \t').splitlines()
        if not snippet or not SEARCH_REFERENCE.search(snippet[0]):
            return text
        chunks.append(header[0].strip()+'\n'+snippet[0][:600]+'\n\n')
    compact = ''.join(chunks).rstrip()+'\n'
    return compact if len(compact)<len(text) else text


def compact_history(items):
    """Keep the first exact copy of every source line and keep each tool call and citation handle."""
    calls = {item['call_id']:str(item.get('input','')) for item in items
             if item.get('type')=='custom_tool_call' and item.get('call_id') and WEB_CALL.search(str(item.get('input','')))}
    seen = set()
    rows = []
    for item in items:
        output = item.get('output')
        if item.get('type')!='custom_tool_call_output' or item.get('call_id') not in calls:
            rows.append(item)
            continue
        request = calls[item['call_id']]
        discovery = DISCOVERY.search(request) and not EVIDENCE_CALL.search(request)
        def normalize(text):
            decoded = _join_character_output(text)
            return _discovery_snippets(decoded) if discovery else _deduplicate_source_lines(decoded, seen)
        if isinstance(output,str):
            updated = normalize(output)
        elif isinstance(output,list):
            updated = [{**part,'text':normalize(part['text'])} if isinstance(part,dict) and isinstance(part.get('text'),str)
                       else part for part in output]
        else:
            updated = output
        rows.append({**item,'output':updated} if updated!=output else item)
    return rows

def prepare(path,raw,enabled=True):
    if not enabled or path.split('?',1)[0]!='/alpha/search':return raw,{}
    try:value=json.loads(raw)
    except (ValueError,TypeError):return raw,{}
    commands=value.get('commands')
    if not isinstance(commands,dict):return raw,{}
    result=copy.deepcopy(value);commands=result['commands'];changes={}
    # 只有发现候选来源的纯搜索采用短摘要，不能缩短同时请求的原文或图像。
    operations=set(commands)-{'response_length'}
    if operations=={'search_query'}and commands.get('response_length')in ('medium','long'):
        desired='medium'if len(commands.get('search_query',[]))>3 else'short'
        if commands['response_length']!=desired:
            changes['discovery_response_length']={'requested':commands['response_length'],'sent':desired}
            commands['response_length']=desired
    removed=0
    aliases=0
    for item in commands.get('open',[]):
        if not isinstance(item,dict):
            continue
        if type(item.get('line')) is int:
            item.setdefault('lineno',item.pop('line'))
            aliases+=1
        if 'num_lines' in item:
            item.pop('num_lines');removed+=1
    if removed:changes['unsupported_num_lines_removed']=removed
    if aliases:changes['open_line_aliases_normalized']=aliases
    return (json.dumps(result,ensure_ascii=False).encode()if changes else raw),changes

def events(chunks):
    """按完整的 SSE 行解析，连接结束时也读取没有换行符的最后一条回执。"""
    pending=b''
    for chunk in chunks:
        if not chunk:continue
        pending+=chunk
        while b'\n'in pending:
            line,pending=pending.split(b'\n',1)
            value=event(line)
            if value is not None:yield value
    value=event(pending)
    if value is not None:yield value

def event(line):
    if not line.startswith(b'data:'):return None
    try:return json.loads(line[5:].strip())
    except (ValueError,UnicodeDecodeError):return None
