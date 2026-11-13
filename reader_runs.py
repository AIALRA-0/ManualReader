"""记录手动触发的一次写作，保存实际阶段时间；不启动模型，也不改阅读进度。"""
from pathlib import Path
import datetime,json,sqlite3,time,uuid
from contextlib import contextmanager

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def seconds(a,b):return round(max(0,datetime.datetime.fromisoformat(b.replace('Z','+00:00')).timestamp()-datetime.datetime.fromisoformat(a.replace('Z','+00:00')).timestamp()),3) if a and b else None

def estimated_cost(group):
    if not group.get('usage'):return None
    total=0
    for request in group.get('requests') or [group['usage']]:
        model=request.get('model') or group.get('model');tier=request.get('configured_service_tier') or group.get('configured_service_tier')
        if model not in ('gpt-6.1-terra','gpt-6.1-sol') or tier not in ('default','priority','fast'):return None
        unit=(1 if model=='gpt-6.1-terra' else 2)*(2 if tier in ('priority','fast') else 1)
        incoming=request.get('input_tokens',0);cache=request.get('cached_input_tokens',0);writes=request.get('cache_write_input_tokens',0);long=incoming>272000
        total+=(max(0,incoming-cache-writes)+cache*.05+writes*1.25)*unit*(2 if long else 1)/1e6+request.get('output_tokens',0)*unit*5*(1.5 if long else 1)/1e6
    return round(total,8)

class Runs:
    def __init__(self,folder):self.path=Path(folder)/'writer-runs.sqlite3'
    @contextmanager
    def db(self):
        conn=sqlite3.connect(self.path,timeout=15)
        try:
            with conn:
                conn.execute('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,value TEXT NOT NULL)');yield conn
        finally:conn.close()
    def create(self,material_id,packet):
        identity='run-'+uuid.uuid4().hex[:20];stamp=now();row={'id':identity,'material_id':material_id,'created_at':stamp,'state':'prepared','writer':packet['writer'],'question_ids':[q['id'] for q in packet['questions']],'packet':packet,'events':[{'type':'prepared','at':stamp}],'groups':{str(i+1):{'id':str(i+1),'question_ids':ids,'state':'prepared'} for i,ids in enumerate(packet['groups'])}}
        if packet.get('writing_version'):row['writing_version']=packet['writing_version']
        if not row['groups']:row.update(state='completed',completed_at=stamp)
        with self.db() as conn:conn.execute('INSERT INTO runs VALUES(?,?)',(identity,json.dumps(row,ensure_ascii=False)))
        return row
    def get(self,identity):
        if not self.path.exists():raise ValueError('没有找到这次写作记录')
        with self.db() as conn:row=conn.execute('SELECT value FROM runs WHERE id=?',(identity,)).fetchone()
        if not row:raise ValueError('这次写作不属于当前项目')
        return json.loads(row[0])
    def update(self,identity,data):
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE');found=conn.execute('SELECT value FROM runs WHERE id=?',(identity,)).fetchone()
            if not found:raise ValueError('这次写作不属于当前项目')
            row=json.loads(found[0]);group=None;stamp=now()
            if data.get('group_id') is not None:
                group=row['groups'].get(str(data['group_id']))
                if group is None:raise ValueError('写作组不属于这次任务')
            kind=data.get('type')
            if kind not in ('prepared','dispatch','writer_started','output_ready','writeback_started','answers_saved','group_saved','completed','usage','observation','coordinator','term_repair','caller'):raise ValueError('没有这种阶段记录')
            event={'type':kind,'at':stamp}
            if group is not None:
                event['group_id']=group['id']
                if kind!='usage':group['state']=kind
                key={'dispatch':'dispatched_at','writer_started':'started_at','output_ready':'output_ready_at','writeback_started':'writeback_started_at','group_saved':'saved_at'}.get(kind)
                if key:group.setdefault(key,stamp)
                if kind=='answers_saved':
                    accepted=data.get('question_ids',[])
                    if not accepted or not set(accepted)<=set(group['question_ids']):
                        raise ValueError('保存回执需要包含本组实际写入的问题编号')
                    group['saved_question_ids']=list(dict.fromkeys(group.get('saved_question_ids',[])+accepted))
                    group.setdefault('first_saved_at',stamp)
                    if set(group['saved_question_ids'])==set(group['question_ids']):
                        group.setdefault('saved_at',stamp)
                        group['state']='group_saved'
            if kind=='usage':
                usage=data.get('usage');target=group if group is not None else row
                if not isinstance(usage,dict):raise ValueError('用量需要使用实际运行记录中的对象')
                target['usage']={k:v for k,v in usage.items() if k in ('input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens','model_calls') and isinstance(v,int) and not isinstance(v,bool) and v>=0}
                target['usage_source']=str(data.get('source','client_runtime'))[:80]
                for key in ('model','effort','configured_service_tier','writing_version','analysis_version'):
                    if data.get(key):target[key]=str(data[key])[:100]
                stages=data.get('stages')
                if isinstance(stages,dict):
                    measured={}
                    for name in ('analysis','writing','supplement'):
                        stage=stages.get(name)
                        if not isinstance(stage,dict) or not isinstance(stage.get('usage'),dict):continue
                        entry={'usage':{k:v for k,v in stage['usage'].items() if k in target['usage'] and type(v) is int and v>=0}}
                        if type(stage.get('seconds')) in (int,float) and 0<=stage['seconds']<86400:entry['seconds']=stage['seconds']
                        if type(stage.get('http_requests')) is int and stage['http_requests']>=0:entry['http_requests']=stage['http_requests']
                        if isinstance(stage.get('session_id'),str):entry['session_id']=stage['session_id'][:100]
                        measured[name]=entry
                    if measured:target['stages']=measured
                if isinstance(data.get('usage_complete'),bool):target['usage_complete']=data['usage_complete']
                if isinstance(data.get('image_tools'),list):target['image_tools']=data['image_tools']
                if isinstance(data.get('requests'),list):target['requests']=[{k:v for k,v in item.items() if k in target['usage'] and isinstance(v,int) and v>=0} for item in data['requests'] if isinstance(item,dict)]
                if isinstance(data.get('tool_usage'),dict) and isinstance(data['tool_usage'].get('web_search_calls'),int) and data['tool_usage']['web_search_calls']>=0:target['tool_usage']=data['tool_usage']
                if isinstance(data.get('research_requests'),list):
                    calls=[entry for entry in data['research_requests'] if isinstance(entry,dict) and entry.get('path')=='/alpha/search']
                    target['research']={'requests':len(calls),
                                        'seconds':round(sum(r['seconds'] for r in calls if type(r.get('seconds')) in (float,int) and 0<=r['seconds']<86400),3),
                                        'model_roundtrips':data.get('research_model_calls',0) if type(data.get('research_model_calls')) is int else 0}
                for key in ('model_started_at','model_completed_at','session_id'):
                    if data.get(key):
                        value=str(data[key])[:100]
                        if key!='session_id':datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
                        target[key]=value
            if kind=='observation':row['observation']=data.get('observation',{})
            if kind=='coordinator':
                value=data.get('coordinator')
                if not isinstance(value,dict) or value.get('kind')!='local_script' or any(value.get('usage',{}).values()):raise ValueError('脚本调度记录需要明确零模型调用，写作者用量另记')
                row['coordinator']=value
            if kind=='term_repair':row['term_repair']=data.get('repair',{})
            if kind=='caller':
                caller=data.get('caller',{})
                if not isinstance(caller,dict) or caller.get('scope')!='entire_codex_turn':raise ValueError('Codex用量需要从当前用户请求开始统计整轮对话')
                row['caller']={**row.get('caller',{}),**caller}
            if kind=='completed':row.update(state='completed',completed_at=stamp)
            elif kind not in ('usage','coordinator','term_repair','caller'):row['state']='running'
            if all(g.get('saved_at') for g in row['groups'].values()) and row['groups']:row.update(state='completed',completed_at=max(g['saved_at'] for g in row['groups'].values()))
            row['events'].append(event);conn.execute('UPDATE runs SET value=? WHERE id=?',(json.dumps(row,ensure_ascii=False),identity));return row
    def request(self,identity,method,path,milliseconds,bytes_count):
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE');found=conn.execute('SELECT value FROM runs WHERE id=?',(identity,)).fetchone()
            if not found:return
            row=json.loads(found[0]);row.setdefault('requests',[]).append({'at':now(),'method':method,'path':path,'milliseconds':round(milliseconds,3),'response_bytes':bytes_count});conn.execute('UPDATE runs SET value=? WHERE id=?',(json.dumps(row,ensure_ascii=False),identity))
    def list(self,limit=20):
        if not self.path.exists():return []
        with self.db() as conn:return [self.public(json.loads(r[0])) for r in conn.execute('SELECT value FROM runs ORDER BY rowid DESC LIMIT ?',(limit,))]
    @staticmethod
    def public(row):
        groups=[]
        for group in row['groups'].values():
            group=dict(group);group['writer_seconds']=seconds(group.get('model_started_at') or group.get('started_at') or group.get('dispatched_at'),group.get('model_completed_at') or group.get('output_ready_at'));group['save_seconds']=seconds(group.get('writeback_started_at'),group.get('saved_at'));group['estimated_token_cost_usd']=estimated_cost(group);groups.append(group)
        writer_used=[g['usage'] for g in groups if g.get('usage')];repair=row.get('term_repair',{});used=writer_used+([repair['usage']] if repair.get('usage') else []);usage={k:sum(u.get(k,0) for u in used) for k in set().union(*(u.keys() for u in used))} if used else None
        first=min((g.get('model_started_at') or g.get('dispatched_at') or g.get('started_at') for g in groups if g.get('model_started_at') or g.get('dispatched_at') or g.get('started_at')),default=None)
        cost=sum(g['estimated_token_cost_usd'] for g in groups) if groups and all(g['estimated_token_cost_usd'] is not None for g in groups) else None
        if repair.get('usage'):
            extra=estimated_cost(repair);cost=cost+extra if cost is not None and extra is not None else None
        metered=groups+([repair] if repair.get('usage') else []);tools_known=bool(metered) and all(isinstance(g.get('tool_usage',{}).get('web_search_calls'),int) for g in metered)
        searches=sum(g['tool_usage']['web_search_calls'] for g in metered) if tools_known else None
        tool_cost=0 if searches==0 and not any(g.get('image_tools')for g in metered)else None
        caller=row.get('caller',{});caller_complete=caller.get('state')=='complete' and bool(caller.get('usage'));caller_cost=estimated_cost(caller) if caller_complete else None
        complete_usage={k:(usage or {}).get(k,0)+caller['usage'].get(k,0) for k in set(usage or {})|set(caller.get('usage',{}))} if caller_complete and usage is not None else None
        complete_seconds=max(caller.get('total_seconds',0),seconds(caller.get('started_at'),row.get('completed_at')) or 0) if caller_complete and row['state']=='completed' else None
        if complete_seconds is not None:complete_seconds=max(complete_seconds,caller.get('launch_to_final_seconds',0))
        complete_cost=round(cost+caller_cost,8) if caller_complete and cost is not None and caller_cost is not None else None
        caller_searches=caller.get('tool_usage',{}).get('web_search_calls');complete_searches=searches+caller_searches if caller_complete and searches is not None and isinstance(caller_searches,int) else None
        complete_total=complete_cost if complete_cost is not None and complete_searches==0 and tool_cost==0 and not caller.get('image_tools')else None
        public_caller={k:v for k,v in caller.items() if k!='requests'}
        return {**{k:v for k,v in row.items() if k not in ('packet','groups','caller')},**({'caller':public_caller} if caller else {}),'caller_details_url':'/api/runs/'+row['id']+'/caller','groups':groups,'question_count':len(row['question_ids']),'total_seconds':row.get('coordinator',{}).get('total_seconds',seconds(row['created_at'],row.get('completed_at'))),'prepare_to_dispatch_seconds':seconds(row['created_at'],first),'usage':usage,'usage_scope':'writers_and_local_script_only','complete_usage':complete_usage,'complete_seconds':complete_seconds,'complete_estimated_token_cost_usd':complete_cost,'complete_web_search_calls':complete_searches,'complete_estimated_total_cost_usd':complete_total,'caller_status':caller.get('state','not_collected'),'usage_groups':len(writer_used),'estimated_token_cost_usd':round(cost,8) if cost is not None else None,'web_search_calls':searches,'estimated_external_tool_cost_usd':tool_cost,'estimated_total_cost_usd':round(cost+tool_cost,8) if cost is not None and tool_cost is not None else None,'cost_basis':'按已返回的逐请求回执折算文本API费用；usage_complete为false时只代表已知下限。检索和图片未返回账单时不推断工具费用。complete字段包含Codex整轮；缺失主对话时留空，不用脚本的0代替','actual_charge':None,'billing_status':'客户端没有返回实际账单','timing_origin':'模型时间与用量来自客户端运行记录；Codex整轮覆盖用户触发到最终汇报，同轮其他工作也会计入；并行时间不相加'}
