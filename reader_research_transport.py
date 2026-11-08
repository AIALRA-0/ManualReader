"""搜索先取得简短候选；打开来源、看图和查找具体证据仍保留调用者请求的范围。"""
import copy,json

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
    for item in commands.get('open',[]):
        if isinstance(item,dict)and 'num_lines'in item:
            item.pop('num_lines');removed+=1
    if removed:changes['unsupported_num_lines_removed']=removed
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
