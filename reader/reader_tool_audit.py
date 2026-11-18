"""按真实联网参数统计动作；一批搜索和打开来源可以发生在同一次调用中。"""
import re

_TOKENS=re.compile(r'''//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|[A-Za-z_$][\w$]*|[{}\[\]():,.]''')
_FIELDS={'search_query':'search','image_query':'image_search','open':'open_page','find':'find_in_page','click':'click','screenshot':'screenshot'}

def _object(tokens,start):
    depth=0
    for end in range(start,len(tokens)):
        if tokens[end]=='{':depth+=1
        elif tokens[end]=='}':
            depth-=1
            if depth==0:return tokens[start:end+1]
    return None

def _array_count(tokens,start):
    stack=[];count=0;occupied=False
    for token in tokens[start:]:
        if token in ('[','{','('):
            if len(stack)==1:occupied=True
            stack.append(token)
        elif token in (']','}',')'):
            if not stack:return None
            stack.pop()
            if not stack:return count+int(occupied)
        elif len(stack)==1:
            if token==',':
                if occupied:count+=1;occupied=False
            else:occupied=True
    return None

def _actions(tokens):
    actions={};depth=0;known=True
    for i,token in enumerate(tokens):
        if token=='{':depth+=1
        elif token=='}':depth-=1
        if depth!=1 or i+2>=len(tokens) or tokens[i+1]!=':':continue
        key=token.strip('\"\'')
        if key not in _FIELDS:continue
        count=_array_count(tokens,i+2) if tokens[i+2]=='[' else None
        if count is None:known=False
        elif count:actions[_FIELDS[key]]=count
    return actions,known

def argument_usage(rows):
    calls=[];unparsed=0
    for row in rows:
        payload=row.get('payload',{})
        if payload.get('type')=='custom_tool_call':
            code=str(payload.get('input',''))
            tokens=[m[0] for m in _TOKENS.finditer(code) if not m[0].startswith(('//','/*'))]
            for i,name in enumerate(tokens):
                if name!='web__run' or tokens[i+1:i+2]!=['(']:continue
                literal=_object(tokens,i+2) if tokens[i+2:i+3]==['{'] else None
                if literal is None:unparsed+=1;continue
                actions,known=_actions(literal)
                if not known:unparsed+=1
                calls.append(actions)
        elif payload.get('type')=='function_call' and payload.get('name') in ('web__run','web.run'):
            import json
            try:
                value=json.loads(payload['arguments']);calls.append({_FIELDS[k]:len(v) for k,v in value.items() if k in _FIELDS and isinstance(v,list)})
            except (KeyError,TypeError,ValueError):unparsed+=1
    if not calls and not unparsed:return None
    actions={};batches={}
    for call in calls:
        for key,count in call.items():actions[key]=actions.get(key,0)+count;batches[key]=batches.get(key,0)+1
    return {'web_search_calls':len(calls)+unparsed,'actions':actions,'action_batches':batches,'actions_complete':not unparsed,'unparsed_calls':unparsed,'source':'codex_session_tool_arguments'}
