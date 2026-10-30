"""让写作者一次读取输入并记录实际会话；用量只从该会话日志取得。"""
from pathlib import Path
import argparse,datetime,json,os

def conversation_usage(identity,turn_id=None,cursor=None,session_file=None,at=None):
    """从任务开始读到最终汇报；只提取用量，不保留或上传对话正文。"""
    roots=[Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'sessions']
    path=Path(session_file) if session_file else next((p for root in roots if root.exists() for p in root.rglob('*'+identity+'.jsonl')),None)
    if not path:return None
    cursor=cursor or {'offset':0,'tier':None,'turn':None}
    if path.stat().st_size<cursor['offset']:return None
    with path.open('rb') as log:
        log.seek(cursor['offset'])
        while True:
            offset=log.tell();line=log.readline()
            if not line:break
            try:row=json.loads(line)
            except ValueError:
                if not line.endswith(b'\n'):log.seek(offset);break
                continue
            value=row.get('payload',{});kind=value.get('type')
            if kind=='thread_settings_applied' and 'service_tier' in value.get('thread_settings',{}):cursor['tier']=value['thread_settings']['service_tier']
            if kind=='task_started':
                if (turn_id and value.get('turn_id')!=turn_id) or (at and datetime.datetime.fromisoformat(row['timestamp'].replace('Z','+00:00'))>datetime.datetime.fromisoformat(at.replace('Z','+00:00'))):
                    cursor['selected']=False
                else:
                    cursor['selected']=True;cursor['turn']={'turn_id':value.get('turn_id'),'started_at':row['timestamp'],'state':'pending','requests':[],'fallback':[],'seen':[],'configured_service_tier':cursor['tier']}
            current=cursor.get('turn')
            if not current or not cursor.get('selected'):continue
            if row.get('type')=='turn_context':
                for key in ('model','effort'):current[key]=value.get(key)
                if value.get('service_tier'):current['configured_service_tier']=value['service_tier']
            if kind=='thread_settings_applied':current['configured_service_tier']=cursor['tier']
            if row.get('type')=='token_usage_record':
                current['requests'].append({**{k:value.get('usage',{}).get(k,0) for k in ('input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens')},'at':row['timestamp'],'model':current.get('model'),'configured_service_tier':current.get('configured_service_tier')})
            if kind=='token_count' and value.get('info'):
                info=value['info'];key=json.dumps(info.get('total_token_usage',{}),sort_keys=True)
                if key not in current['seen']:current['seen'].append(key);current['fallback'].append(info.get('last_token_usage',{}))
            if kind=='task_complete':current.update(state='complete',completed_at=row['timestamp'])
            if kind in ('turn_aborted','task_aborted'):current.update(state='interrupted',completed_at=row['timestamp'])
        cursor['offset']=log.tell()
    current=cursor.get('turn')
    if not current:return {'report':None,'cursor':cursor}
    requests=current['requests'] or current['fallback'];usage={k:sum(r.get(k,0) for r in requests) for k in ('input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens')};usage['model_calls']=len(requests)
    report={k:v for k,v in current.items() if k not in ('fallback','seen','requests')};report.update(session_id=identity,requests=requests,usage=usage,source='codex_entire_turn_log',scope='entire_codex_turn',scope_note='从用户触发这一轮到Codex最终汇报结束，包含读取、启动、等待和汇报；同轮其他开发或测试也原样计入，不按内容猜测拆账')
    if current.get('completed_at'):report['total_seconds']=round((datetime.datetime.fromisoformat(current['completed_at'].replace('Z','+00:00'))-datetime.datetime.fromisoformat(current['started_at'].replace('Z','+00:00'))).total_seconds(),3)
    return {'report':report,'cursor':cursor}

def external_tool_usage(events):
    calls={}
    for line in events:
        try:row=json.loads(line)
        except ValueError:continue
        item=row.get('item',{})
        if row.get('type')=='item.completed' and item.get('type')=='web_search':calls[item.get('id')]=item.get('action',{}).get('type','other')
    return {'web_search_calls':len(calls),'actions':{kind:list(calls.values()).count(kind) for kind in set(calls.values())}}

def runtime_usage(meta,coordinator=None):
    identity=meta.get('session_id')
    if not identity or identity==coordinator:return None
    roots=[Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'sessions']
    log=next((p for root in roots if root.exists() for p in root.rglob('*'+identity+'.jsonl')),None)
    if not log:return None
    rows=[]
    for line in log.read_text('utf8',errors='replace').splitlines():
        try:rows.append(json.loads(line))
        except ValueError:continue
    starts=[i for i,row in enumerate(rows) if row.get('payload',{}).get('type')=='task_started']
    if not starts:return None
    selected=rows[starts[-1]:];done=next((row for row in reversed(selected) if row.get('payload',{}).get('type')=='task_complete'),None)
    if not done:return None
    counts=[];seen=set()
    for row in selected:
        info=row.get('payload',{}).get('info')
        if row.get('payload',{}).get('type')!='token_count' or not info:continue
        key=json.dumps(info.get('total_token_usage',{}),sort_keys=True)
        if key not in seen:seen.add(key);counts.append(info.get('last_token_usage',{}))
    if not counts:return None
    usage={key:sum(row.get(key,0) for row in counts) for key in ('input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens')};usage['model_calls']=len(counts)
    context=next((row['payload'] for row in selected if row.get('type')=='turn_context'),{})
    settings=[row['payload']['thread_settings'] for row in rows if row.get('payload',{}).get('type')=='thread_settings_applied']
    tier=(settings[-1].get('service_tier') if settings else None) or context.get('service_tier') or meta.get('configured_service_tier')
    return {'usage':usage,'requests':counts,'source':'codex_session_log','session_id':identity,'model':context.get('model'),'effort':context.get('effort'),'configured_service_tier':tier,'model_started_at':selected[0]['timestamp'],'model_completed_at':done['timestamp']}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['input','finish']);parser.add_argument('--out',required=True);parser.add_argument('--group',required=True,choices=['1','2']);args=parser.parse_args();folder=Path(args.out)
    if args.action=='finish':
        draft=folder/('result-'+args.group+'.draft.json');raw=draft.read_text('utf8');result=json.loads(raw);manifest=json.loads((folder/'run.json').read_text('utf8'));group=next(g for g in manifest['groups'] if g['id']==args.group)
        answers=result.get('answers',[])
        if len(answers)!=len(group['question_ids']) or {a.get('question_id') for a in answers}!=set(group['question_ids']):raise SystemExit('回答编号存在缺项或重复，请只修正本组结果文件')
        if not isinstance(result.get('terms'),list) or not isinstance(result.get('quizzes'),list):raise SystemExit('术语和练习必须使用列表')
        target=folder/('result-'+args.group+'.json');draft.replace(target);print(json.dumps({'file':str(target),'answers':len(answers),'terms':len(result['terms']),'quizzes':len(result['quizzes'])}));raise SystemExit(0)
    marker=folder/('worker-'+args.group+'.json')
    if not marker.exists():marker.write_text(json.dumps({'session_id':os.environ.get('CODEX_THREAD_ID') or os.environ.get('CODEX_SESSION_ID'),'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}),'utf8')
    print((folder/('prompt-'+args.group+'.txt')).read_text('utf8'))
