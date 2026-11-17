"""Read-only project projection and the round ledger for the standalone planner."""
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from .stages.step01.lifecycle import Step01, schema_defaults
from .stages.step01.paths import relative
from .stages.step01.scan import inside
from .stages.step02.card import digest
from .stages.step02.original import inventory, read_region

MODES = ('NORMAL','INTRO','FINALE','ANSWER')
EMPTY = {'title': None,'bridges':[],'recaps':[],'forecast':None}


def read(path):
    return json.loads(Path(path).read_bytes())


def first_valid_range(material, selected):
    answer={}
    for fid, row in material.items():
        if fid in selected:
            answer[fid]=row
    return answer


class Project:
    def __init__(self, data, identity):
        if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,90}',identity):
            raise ValueError('项目编号无效')
        self.data=Path(data).resolve()
        self.root=inside(self.data/'projects',identity)
        self.projection=inside(self.root,'project.json')
        self.index=inside(self.root,'source-index.json')
        self.identity=identity
        self.db=self.data/'ledgers'/(identity+'.sqlite3')
        self.db.parent.mkdir(parents=True,exist_ok=True)

    def _project(self):
        p=read(self.projection)
        if p.get('project_id')!=self.identity:
            raise ValueError('正式项目身份与已导入目录不一致')
        return p

    def config(self):
        p=self._project()
        return {'root':str(inside(self.root,'originals')),
                'sources':{fid:relative(f['relative_path']) for fid,f in p['files'].items()},
                'material_roots':['']}

    def tx(self, action, *, write=True):
        with closing(sqlite3.connect(self.db,timeout=25)) as db, db:
            if write:
                db.execute('BEGIN IMMEDIATE')
                db.execute('CREATE TABLE IF NOT EXISTS rounds (id INTEGER PRIMARY KEY, state TEXT NOT NULL)')
            try:
                item=db.execute('SELECT state FROM rounds WHERE id=1').fetchone()
            except sqlite3.OperationalError as exc:
                if write or 'no such table' not in str(exc):
                    raise
                item=None
            s=json.loads(item[0]) if item else schema_defaults()
            outcome=action(s, {'file_coverage':self._project()['progress_input']['file_coverage']})
            if write:
                wire=json.dumps(s,ensure_ascii=False,separators=(',',':'))
                if item is None or wire!=item[0]:
                    db.execute('INSERT INTO rounds VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET state=excluded.state',(wire,))
            return outcome

    def manual_counts(self, _):
        progress=self._project()['progress_input']
        return len(progress['completed_chapter_ids']),progress['total_formal_chapters']

    def lifecycle(self, request):
        step=Step01(self.identity,self.config,self.tx,self.manual_counts)
        action=request.get('action')
        rid=request.get('round_id')
        if action=='prepare':
            return step.prepare(request.get('mode'),rid)
        if action=='observe':
            return step.observe(rid)
        if action=='select':
            ids=request.get('file_ids')
            if isinstance(ids,list) and all(isinstance(x,str) for x in ids) and len(set(ids))!=len(ids):
                raise ValueError('本轮实际使用的文件编号重复')
            return step.select(rid,ids)
        if action=='reconcile':
            return step.reconcile(rid)
        raise ValueError('文件清单只接受准备、监测、绑定和提交前核对')

    def locate_round(self,rid):
        if not isinstance(rid,str):
            raise ValueError('STEP-02 需要本轮 M1 编号')
        item=self.tx(lambda s,_:s['rounds'].get(rid),write=False)
        if not item:
            raise ValueError('本项目没有该轮次，请从 M1 开始')
        return item

    def context(self, request):
        m1=self.locate_round(request.get('round_id'))['m1']
        return build_context(self._project(),m1['mode'],m1['manifest'],self.root,self.index,request)

    def original(self, request):
        row=self.locate_round(request.get('round_id'))['m1']
        file_id=request.get('file_id')
        f=next((r for r in row['manifest']['files'] if r['file_id']==file_id and r['status']=='active'
                and not r['identity_pending']),None)
        if f is None:
            raise ValueError('access_blocked: 原件不在本轮有效文件清单')
        root=inside(self.root,'originals')
        action=request.get('action')
        if action=='catalog':
            return inventory(root,f)
        if action=='read':
            return read_region(root,f,request.get('start'),request.get('end'),request.get('visual') is True,
                               request.get('char_offset',0))
        if action=='index':
            return index_for(read(self.index),f)
        raise ValueError('原件接口只接受目录、读取或来源索引请求')


def index_for(index, file):
    saved=index.get(file['file_id'])
    if not saved:
        return {'verified':False,'reason':'no_parsed_source_index'}
    if saved.get('sha256')!=file['content_sha256']:
        return {'verified':False,'reason':'parsed_original_version_differs'}
    rows=saved.get('rows',[])
    ids=[r['id'] for r in rows if r.get('id')]
    if not rows or len(ids)!=len(rows) or len(set(ids))!=len(ids):
        return {'verified':False,'reason':'source_index_empty_or_duplicate'}
    return {'verified':True,'rows':rows}


def joined_ranges(rows):
    out=[]
    for row in sorted(rows,key=lambda a:(a['type'],a['start'],a['end'])):
        prior=out[-1] if out else {}
        if prior.get('type')==row['type'] and row['start']<=prior['end']+1:
            prior['end']=max(prior['end'],row['end'])
        else:
            out.append(dict(row))
    return out


def build_context(p,mode,manifest,root,source_index,request):
    if mode not in MODES:
        raise ValueError('未知规划模式')
    directory=p['directory']
    node_id=p['selection']
    current=next((n for n in directory if n['node_id']==node_id),None)
    prior=None
    active={row['file_id']:row for row in manifest['files'] if row['status']=='active' and not row['identity_pending']}
    # An old illustration has no membership in the originals tree imported by the adapter.
    diagnostics=[]
    fixed={'custom_instruction':request.get('custom_instruction') or None}
    if fixed['custom_instruction'] is not None and (not isinstance(fixed['custom_instruction'],str)
            or len(fixed['custom_instruction'])>24000):
        raise ValueError('补充要求需要是用户原话，长度不得超过 24000 字符')
    preview=None
    fixture=None
    hints={}
    saved=[]
    allowed=None
    ranges=None
    questions=p['questions']
    if mode=='NORMAL':
        if current is None or request.get('node_id') not in (None,node_id):
            raise ValueError('当前章节未选定或已经变化')
        fixed['chapter']=current
        i=directory.index(current)
        prior=directory[i-1]['node_id'] if i else None
        hints=p['scope_hints'].get(node_id,{})
    elif mode=='INTRO':
        intro=next((n for n in directory if n.get('title') in ('导读','0. 导读','宏观导读','第 0 章 导读','第 0 章：导读')),None)
        fixed['chapter']=intro or {'node_id':None,'parent_node_id':None,'number':'0','title':'导读'}
        if intro is None:
            preview={'identity':'preview-intro-'+digest(p['project_id'])[:16],'persisted':False}
    elif mode=='FINALE':
        if request.get('explicit_finale') is not True:
            raise ValueError('只有用户明确选择才能启动收尾；双完成只显示选择')
        index=read(source_index)
        identity_map={}
        for fid,row in active.items():
            verified=index_for(index,row)
            for item in verified.get('rows',[]):
                if item['id'] in identity_map:
                    raise ValueError('来源索引存在跨文件重复编号')
                identity_map[item['id']]=(fid,item)
        source_ranges={}
        for e in p['saved_explanations']:
            ids=e['source_ids']
            if not e['version'] or not ids or any(i not in identity_map for i in ids):
                diagnostics.append({'explanation_id':e['explanation_id'],'reason':'saved_sources_unavailable'})
                continue
            blocks=e.get('blocks') or []
            if e.get('fixture_based'):
                ref=inside(root,relative(e['markdown_path']))
                raw=ref.read_bytes()
                if sha256(raw).hexdigest()!=e['body_sha256']:
                    raise ValueError('测试首稿哈希与已登记记录不一致')
                blocks=[raw.decode('utf-8-sig')]
                fixture=p.get('fixture')
            if not any(b.strip() for b in blocks):
                continue
            saved.append({'chapter':e['chapter'],'blocks':blocks,'fixture_based':e.get('fixture_based',False)})
            prior=e['node_id']
            for sid in ids:
                fid,s=identity_map[sid]
                if s.get('position'):
                    source_ranges.setdefault(fid,[]).append(s['position'])
                if s.get('time'):
                    source_ranges.setdefault(fid,[]).append(s['time'])
        if not saved or not source_ranges:
            raise ValueError('no_saved_chapter: '+(p.get('fixture_blocked') or '没有能追溯原件的已保存讲解'))
        ranges={fid:joined_ranges(value) for fid,value in source_ranges.items()}
        hints=ranges
        allowed=sorted(ranges)
        finale=next((n for n in directory if n['title'] in ('最后一章','收尾','全篇回顾与收尾')),None)
        fixed['chapter']=finale or {'node_id':None,'parent_node_id':None,'number':None,'title':'阶段性收束'}
        if not finale:
            preview={'identity':'preview-finale-'+digest(p['project_id'])[:16],'persisted':False}
    else:
        qid=request.get('question_id')
        q=questions.get(qid) if isinstance(qid,str) else None
        if not q:
            raise ValueError('没有找到真实有效的 Reader 问题')
        ctx={'选中的原文或讲解':q.get('selected_text','')}
        parents=[]
        next_id=q.get('parent_question_id')
        visited={qid}
        while next_id and next_id in questions and next_id not in visited:
            visited.add(next_id)
            parent=questions[next_id]
            item={'question':parent['text'],'answer':parent.get('answer') or ''}
            if parent.get('explicit_requirement'):
                item['用户原始要求']=parent['explicit_requirement']
            parents.append(item)
            next_id=parent.get('parent_question_id')
        if parents:
            ctx['父问题及相关回答']=parents
        if request.get('revise_existing_answer') is True and q.get('answer'):
            ctx['这条问题已有的回答']=q['answer']
        if q.get('resource'):
            ctx['选中资源']=q['resource']
        hints=q.get('scope_hints',{})
        fixed.update(question={k:q.get(k) for k in ('id','text','version','status','learning_node_id')},context=ctx)
    if mode!='ANSWER':
        fixed['project']={'id':p['project_id'],'name':p['name'],'root_path':str(root/'originals')}
        fixed['versions']={**p['versions'],'file_manifest_revision':manifest['manifest_version']}
        fixed['progress']={k:{f:manifest['progress'][k][f] for f in fields} for k,fields in (
            ('files',('coverage_pct','status')),('lessons',('completed','total','completion_pct')),
            ('overall',('coverage_pct','status','calculation')))}
        fixed['previous']=p['previous'].get(prior,EMPTY)
    approved=[] if mode in ('ANSWER','INTRO') else [x for x in p['approved_styles'].get(prior,[])
        if x.get('evidence_question_ids') and all(q in questions for q in x['evidence_question_ids'])]
    used={i for x in approved for i in x['evidence_question_ids']}
    pending=[{'ref':f'qref-{i+1}','question_id':qid,'text':questions[qid]['text']} for i,qid in
             enumerate(() if mode in ('ANSWER','INTRO') else p['recent_questions'].get(prior,[]))
             if qid in questions and qid not in used]
    return {'mode':mode,'fixed':fixed,'files':list(active.values()),'approved_styles':approved,
            'unprocessed_questions':pending,'directory':directory if mode in ('NORMAL','INTRO','FINALE') else [],
            'scope_hints':hints,'added_rounds':{fid:1 for fid in active},'saved_explanations':saved,
            'allowed_selection':allowed,'allowed_ranges':ranges,'preview':preview,'fixture':fixture,
            'diagnostics':diagnostics,'state_fingerprint':digest(p)}
