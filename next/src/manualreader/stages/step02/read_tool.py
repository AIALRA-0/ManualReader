import json

TOOL = {'name': 'read', 'description': 'Read original file text by its exact listed inclusive location. '
        'The files, ranges and available image pages are listed in the planning input. '
        'Use requests to batch nearby portions and return original evidence, not summaries.',
        'inputSchema': {'type': 'object', 'required': ['requests'], 'properties': {'requests': {
            'type': 'array', 'items': {'type': 'object', 'required': ['file_id', 'start', 'end'],
                'properties': {'file_id': {'type': 'string'}, 'start': {'type': 'integer'},
                    'end': {'type': 'integer'}, 'visual': {'type': 'boolean'},
                    'char_offset': {'type': 'integer'}}, 'additionalProperties': False}}}, 'additionalProperties': False}}

def read(store, arguments, trace):
    req = arguments.get('requests')
    if not isinstance(req, list) or not 1 <= len(req) <= 8:
        raise ValueError('每次可读取 1 至 8 个原件范围')
    blocks, remaining = [], 24000
    for x in req:
        fid, start, end = x.get('file_id'), x.get('start'), x.get('end')
        data = store.get(fid)
        offset = x.get('char_offset', 0)
        status = 'read_from_original'
        if (not data or type(start) is not int or type(end) is not int or end < start
                or start not in data['rows'] or end not in data['rows']
                or type(offset) is not int or offset < 0 or end - start > 299):
            blocks.append({'type': 'text', 'text': 'access_blocked: 这个范围不在原件读取白名单中'})
            status = 'access_blocked'
            used = []
        else:
            result = []
            used = []
            for n in range(start, end + 1):
                item = data['rows'].get(n)
                if item is None:
                    continue
                item = dict(item)
                t = item['text']
                if t and not remaining:
                    break
                item['text'] = t[offset:offset + remaining]
                remaining -= len(item['text'])
                result.append(item)
                used.append(n)
            blocks.append({'type': 'text', 'text': json.dumps(
                {'file_id': fid, 'type': data['type'], 'rows': result}, ensure_ascii=False)})
            if x.get('visual'):
                image = data.get('images', {}).get(str(start)) if start == end else None
                if image:
                    blocks.append({'type': 'image', 'data': image, 'mimeType': 'image/png'})
                else:
                    blocks.append({'type': 'text', 'text': '该页没有随本轮暂存的图像；可以使用实际页文本'})
                    status = 'visual_not_staged'
        with trace.open('a', encoding='utf8') as stream:
            stream.write(json.dumps({'file_id': fid, 'start': start, 'end': end, 'actual_positions': used,
                                     'content_sha256': (data or {}).get('content_sha256'),
                                     'file_revision': (data or {}).get('file_revision'),
                                     'status': status, 'via': 'model_tool'}, ensure_ascii=False) + '\n')
    return {'content': blocks}
