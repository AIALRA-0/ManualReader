"""分别保存原文文件、译文和讲解，所有提问继续引用稳定的段落与片段编号。"""
from pathlib import Path, PurePosixPath
import base64, copy, difflib, json, re, uuid, threading, os
from source_parser import parse, Document, apply_translations, attach_reading_segments
from reader_learning import learning_payload,group_pdf_figures
from reader_coverage import validate_source_mapping
from reader_reading import annotate_explanations,prepare as prepare_reading
from reader_quality import clean_markdown,lesson_display,explanation_headings


RULES = [
    '每份原文文件有独立编号，译文只更新该文件的原文片段，不能写入讲解',
    '讲解独立保存，可以引用多个原文片段，也可以只属于项目中的一个章节',
    '问题引用实际选中的片段，图片和其他非文本内容使用名称与稳定编号',
    '切换窗口和布局只改变展示，已有问答、笔记与阅读记录不迁移',
    '原文文件和讲解保留历史版本，已有片段编号不能被其他内容复用',
    '主学习目录使用 learning_node_id，原文件目录使用 section_id；两者分别保存，切换文件或定位句子不更换当前学习章节',
    '讲解写入时可提供 learning_node_id，接口会同时登记到该学习章节；原文、译文和问题继续使用同一组真实片段编号',
]


def check_id(value, label='编号'):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,70}', value):
        raise ValueError(label+'需要使用字母、数字、下划线或连字符')
    return value


_WORKSPACE_LOCKS={};_WORKSPACE_LOCKS_GUARD=threading.Lock();_WORKSPACE_SNAPSHOTS={}

class MaterialSnapshot(dict):
    """材料仍按字典读取和编码；辅助缓存只保留它的弱引用。"""

class Workspace:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder/'reading-workspace.json'
        self.files = self.folder/'reading-files'
        with _WORKSPACE_LOCKS_GUARD:self.lock=_WORKSPACE_LOCKS.setdefault(str(self.path.resolve()),threading.RLock())

    def read(self):
        return json.loads(self.path.read_text('utf8')) if self.path.exists() else {'revision':0, 'files':[], 'explanations':[], 'presentation':{}}

    def read_snapshot(self):
        """只读请求复用未变化的文件；需要修改内容时继续调用 read 取得独立对象。"""
        with self.lock:
            if not self.path.exists():return self.read()
            stat=self.path.stat();signature=(stat.st_mtime_ns,stat.st_size,stat.st_ino);key=str(self.path.resolve())
            with _WORKSPACE_LOCKS_GUARD:
                cached=_WORKSPACE_SNAPSHOTS.get(key)
                if cached and cached[0]==signature:return cached[1]
            value=self.read()
            with _WORKSPACE_LOCKS_GUARD:
                limit=1 if os.environ.get('MANUAL_READER_ROLE')=='web'else 2
                if len(_WORKSPACE_SNAPSHOTS)>=limit:_WORKSPACE_SNAPSHOTS.pop(next(iter(_WORKSPACE_SNAPSHOTS)))
                _WORKSPACE_SNAPSHOTS[key]=(signature,value)
            return value

    def revision(self):
        paths=[self.path,self.folder/'reading-framework.json'];return max((p.stat().st_mtime_ns for p in paths if p.exists()),default=0)

    def write(self,value,expected=None):
        with self.lock:return self._write(value,expected)

    def _write(self, value, expected=None):
        if expected is not None and expected != self.read()['revision']:
            raise ValueError('内容已更新，请先读取最新版本再修改')
        self.folder.mkdir(parents=True,exist_ok=True)
        if self.path.exists():
            versions=self.folder/'reading-history';versions.mkdir(exist_ok=True)
            (versions/(str(self.path.stat().st_mtime_ns)+'.json')).write_bytes(self.path.read_bytes())
        value['revision']=self.read()['revision']+1
        temporary=self.path.with_suffix('.next.json')
        temporary.write_text(json.dumps(value,ensure_ascii=False),'utf8');temporary.replace(self.path)
        return value

    def project(self, original):
        value=self.read();result=MaterialSnapshot(copy.deepcopy(original))
        primary={'id':'primary','name':original.get('original_name') or original['title'], 'kind':original.get('kind'),
                 'outline':copy.deepcopy(original['outline']), 'passage_ids':[p['id'] for p in original['passages']],
                 'original':bool(original.get('original_file') or original.get('kind')=='pdf'), 'primary':True}
        for p in result['passages']:
            p.setdefault('content_kind','explanation' if original.get('reader_mode')=='authored' else 'source')
            p.setdefault('file_id','primary')
        files=([primary] if original['passages'] else [])+[{k:v for k,v in row.items() if k!='document'} for row in value['files']]
        for row in value['files']:
            # read 已取得本次独占的工作区对象；直接使用它的正文，不再复制一整份大型代码项目。
            # 辅助快照仍保持只读，project 不从 read_snapshot 取可修改的数据。
            document={key:row['document'][key]for key in ('passages','pages')}
            for p in document['passages']:
                p.update(content_kind='source',file_id=row['id'])
                if p.get('caption_id'):
                    caption=next((q for q in document['passages'] if q['id']==p['caption_id']),{})
                    if caption.get('zh'):p['zh']=caption['zh']
            for page in document['pages'].values():page['file_id']=row['id']
            result['passages'].extend(document['passages']);result['pages'].update(document['pages'])
        for row in value['explanations']:
            for p in copy.deepcopy(row['passages']):
                p.update(content_kind='explanation',explanation_id=row['id'],source_links=row['source_links'],content_version=row['version'])
                result['passages'].append(p)
        # 旧文件曾被重复登记时，只把完全相同的记录显示一次；原始文件与版本记录不改写。
        projected=[];seen={};aliases=[];conflicts=[]
        workspace_passages={p['id'] for row in value['explanations'] for p in row['passages']}
        for p in result['passages']:
            if p.get('content_kind')=='explanation' and p.get('file_id')=='primary' and not p.get('explanation_id') and p['id'] in workspace_passages:
                aliases.append({'id':p['id'],'same_explanation_version':True});continue
            old=seen.get(p['id'])
            keys=('en','raw_text','file_id','refs','kind')
            authored_duplicate=old is not None and old.get('content_kind')==p.get('content_kind')=='explanation' and all(old.get(k)==p.get(k) for k in ('en','raw_text','refs','kind'))
            if old is not None and (all(old.get(k)==p.get(k) for k in keys) or authored_duplicate):
                # 同一原件重复登记时只合并投影，旧记录不删除；已有非空译文仍然保留。
                if not old.get('zh') and p.get('zh'):old['zh']=p['zh']
                elif old.get('zh') and p.get('zh') and old['zh']!=p['zh']:
                    conflicts.append({'id':p['id'],'field':'zh','values':[old['zh'],p['zh']]})
                old_sentences={s['id']:s for s in old.get('sentences',[])}
                for sentence in p.get('sentences',[]):
                    existing=old_sentences.get(sentence['id'])
                    if existing is None:old.setdefault('sentences',[]).append(copy.deepcopy(sentence))
                    elif not existing.get('zh') and sentence.get('zh'):existing['zh']=sentence['zh']
                if authored_duplicate:
                    for key in ('explanation_id','source_links','content_version'):
                        if key in p:old[key]=copy.deepcopy(p[key])
                aliases.append({'id':p['id'],'file_id':p.get('file_id'),'same_content_and_coordinates':True});continue
            if old is not None:conflicts.append({'id':p['id'],'field':'source','reason':'同一编号对应不同原文或坐标，需要核对原件'})
            seen[p['id']]=p;projected.append(p)
        result['passages']=projected;result['duplicate_import_records']=aliases;result['import_conflicts']=conflicts
        self.apply_visual_translations(result)
        for p in result['passages']:
            if p.get('content_kind')=='explanation':
                for key in ('en','zh'):
                    if p.get(key):p[key]=clean_markdown(p[key])
            if p.get('content_kind')=='explanation' and re.fullmatch(r'\s*(?:#{1,6}\s*)?(?:\*\*)?(?:原文|原句|回答)(?:\*\*)?[：:](?:\*\*)?\s*',p.get('zh') or p.get('en') or ''):p['display_omit']=True
        result['reading_files']=files
        result['explanations']=[{k:v for k,v in row.items() if k!='passages'} for row in value['explanations']]
        result['workspace_revision']=value['revision'];result['presentation']=value['presentation']
        from reader_learning_scope import excluded_sources
        excluded,outdated=excluded_sources(result,value);result['learning_excluded_source_ids']=sorted(excluded);result['outdated_learning_exclusions']=outdated
        annotate_explanations(result)
        passages_by_id={p['id']:p for p in result['passages']}
        for p in result['passages']:
            if p.get('content_kind')=='explanation':
                text=p.get('zh') or p.get('en') or ''
                alternatives=[passages_by_id.get(link['passage_id'],{}).get(k,'') for link in p.get('source_links',[]) for k in ('en','zh')]
                p['display_markdown']=lesson_display(text,p.get('source_quote',''),alternatives)
                if not p['display_markdown'].strip():p['display_omit']=True
        headings_file=self.folder/'reading-headings.json'
        if headings_file.exists():
            headings=json.loads(headings_file.read_text('utf8')).get('headings',{})
            for p in result['passages']:
                if p.get('content_kind')=='explanation' and p['id'] in headings and 'reading_heading'not in p:p['reading_heading']=str(headings[p['id']])[:120]
        explanation_headings(result['passages'],result['explanations'])
        __import__('reader_framework_legacy').project(result,value,self.folder)
        result['legacy_math_display_groups']=__import__('reader_blocks').project_legacy_math(result['passages'])
        result['learning']=learning_payload(result,value,{})
        result['association_rules']=RULES
        attach_reading_segments(result)
        result['reading_view']=prepare_reading(result,self.folder,original,value['files'])
        passages_by_id={p['id']:p for p in result['passages']}
        for group in result['reading_view']['groups']:
            for segment in group.get('translation_segments',[]):
                for identity in segment['source_ids']:
                    if identity in passages_by_id:passages_by_id[identity].setdefault('translation_segments',[]).append(segment)
        self.apply_visual_translations(result)
        from reader_alignment import build_alignment
        result['alignment']=build_alignment(result)
        result['framework_version']=__import__('reader_generation').VERSION
        # 进度只读取目录与排除规则，不再为这些小字段重新解析一整份多文件工作区。
        result.reading_metadata={key:value[key]for key in ('revision','learning_outline','learning_exclusions','planning_mode')if key in value}
        return result

    def apply_visual_translations(self,material):
        """只把已核对原图的结果用于展示；原图变化后保留为未核对。"""
        from reader_translation import visual_signature
        from reader_coverage import translation_ready,visual_label_ready
        path=self.folder/'reading-visual-language.json'
        if not path.exists():return
        records=json.loads(path.read_text('utf8')).get('images',[])
        passages={p['id']:p for p in material['passages']}
        for row in records:
            p=passages.get(row.get('image_id'))
            if not p or row.get('original_signature')!=visual_signature(p):continue
            status=row.get('status');labels=row.get('labels',[]);sources=row.get('source_ids',[])
            complete=not row.get('unreadable') and bool(row.get('evidence'))
            if status=='no_text':complete=complete and not labels
            elif status=='text_covered_by_ids':complete=complete and bool(sources) and all(i in passages and translation_ready(passages[i]) for i in sources)
            elif status=='translated':complete=complete and bool(labels) and all(visual_label_ready(x) for x in labels)
            else:complete=False
            p.update(visual_language_verified=True,visual_language_status=status,visual_translation_complete=bool(complete),visual_labels=copy.deepcopy(labels))
            if status=='no_text' and complete:p['source_language']='none'
            # 已核对的中文标签可以先读；原图中仍无法辨认的小字继续计入缺口。
            # 标签没有单独文字框时只定位整图，不伪造句级坐标。
            if status in ('translated','needs_review') and row.get('evidence'):
                valid=[x for x in labels if visual_label_ready(x) and not x.get('preserve')]
                label_text=(row.get('translation_markdown') if complete else '') or '\n\n'.join(x['original']+'：'+x['translation'] for x in valid)
                if label_text and not p.get('zh'):p['zh']=label_text
            if complete:p['visual_translation_source_ids']=list(sources)

    def add_file(self,data):
        return self.import_source(data)

    def import_source(self,data,*,allow_private_urls=False):
        """用户与 Agent 都向当前 Workspace 导入；本机路径不属于远程请求的输入。"""
        from reader_import import SourceImporter
        result=SourceImporter(self,allow_private_urls=allow_private_urls).import_source(data)
        # 导入成功后立即补入同一项目的文件根目录，已打开的文件树通过系统事件收到变化。
        if (self.folder/'project-files.json').exists() and (self.folder/'material.json').exists():
            from reader_project_files import ProjectFiles
            material=json.loads((self.folder/'material.json').read_text('utf8'))
            settings=Path(os.environ.get('MANUAL_READER_SETTINGS',str(self.folder/'reader-settings.json')))
            ProjectFiles(material['id'],self.folder,settings,material).ensure()
        return result

    def normalize_files(self,data):
        value=self.read();wanted=data.get('file_ids');changed=[]
        if wanted is not None and (not isinstance(wanted,list) or any(not isinstance(x,str) for x in wanted)):raise ValueError('请按编号选择原文文件')
        for row in value['files']:
            if row['kind']!='pdf' or (wanted is not None and row['id'] not in wanted):continue
            folder=self.files/row['id'];document=group_pdf_figures(row['document'],folder/row['stored_name'],folder/'assets')
            for p in document['passages']:
                if p.get('src','').startswith('/asset/'):p['src']='/content-files/'+row['id']+'/asset/'+p['src'].split('/')[-1]
            row['document']=document;row['passage_ids']=[p['id'] for p in document['passages']]
            changed.append({'file_id':row['id'],'figures':[p['id'] for p in document['passages'] if p.get('display_generated')]})
        self.write(value,data.get('expected_revision'));return {'files':changed,'original_text_and_ids_preserved':True}

    def translations(self,data):
        identity=data.get('file_id','primary')
        if identity=='primary':return None
        value=self.read();row=next((r for r in value['files'] if r['id']==identity),None)
        if not row:raise ValueError('没有找到译文对应的原文文件')
        row['document']=apply_translations(copy.deepcopy(row['document']),data.get('translations'),data.get('complete',False))
        self.write(value,data.get('expected_revision'));return row['document']['coverage']

    def explain(self,data,material,used_passages=None):
        from reader_quality import lesson_parts,clean_markdown
        data=lesson_parts(data,material)
        if isinstance(data.get('markdown'),str):data={**data,'markdown':clean_markdown(data['markdown'])}
        value=self.read();identity=check_id(data.get('id') or 'explain-'+uuid.uuid4().hex[:12],'讲解编号')
        node_id=data.get('learning_node_id');node=None
        if node_id is not None:
            check_id(node_id,'学习章节编号')
            node=next((n for n in value.get('learning_outline',[]) if n['id']==node_id),None)
            if not node:raise ValueError('学习章节不存在，请先通过 /api/learning/outline 登记目录，再提供 learning_node_id')
        old=next((r for r in value['explanations'] if r['id']==identity),None)
        if old and data.get('expected_version')!=old['version']:raise ValueError('修改讲解时请提供最新的 expected_version')
        title=data.get('title') or (old or {}).get('title')
        if not isinstance(title,str) or not title.strip() or len(title)>300:raise ValueError('讲解标题需要是最多 300 字的文字')
        inherited=next((n.get('effective_source_links',[]) for n in material.get('learning',{}).get('nodes',[]) if n['id']==node_id),[])
        links=data.get('source_links', (old or {}).get('source_links',inherited))
        if not isinstance(links,list) or len(links)>300 or any(not isinstance(link,dict) for link in links):raise ValueError('原文引用需要是对象列表')
        passages={p['id']:p for p in material['passages']};clean_links=[]
        for link in links:
            p=passages.get(link.get('passage_id'))
            if not p or p.get('content_kind')=='explanation':raise ValueError('讲解需要引用真实的原文片段')
            sid=link.get('sentence_id')
            if sid and not any(s['id']==sid for s in p['sentences']):raise ValueError('引用的句子不属于该段原文')
            if link.get('file_id') and link['file_id']!=p.get('file_id','primary'):raise ValueError('引用的文件与原文片段不一致')
            clean_links.append({'file_id':p.get('file_id','primary'),'passage_id':p['id'],'sentence_id':sid})
        section=data.get('section_id',(old or {}).get('section_id'))
        section_ids={s['id'] for f in material.get('reading_files',[]) for s in f['outline']}
        if section and section not in section_ids:raise ValueError('原文目录中没有这个 section_id；讲解目录请使用 learning_node_id')
        blocks=data.get('blocks')
        if blocks is None:
            markdown=data.get('markdown')
            if not isinstance(markdown,str) or not markdown.strip() or len(markdown)>200000:raise ValueError('请提交讲解 Markdown 或 blocks')
            # 按 Markdown 顶层块划分，代码和表格保留为整体；已存在的相同块沿用编号。
            chunks=__import__('reader_blocks').markdown_chunks(markdown)
            chunks=[c for c in chunks if not re.fullmatch(r'\s*(?:#{1,6}\s*)?(?:\*\*)?(?:原文|原句|回答)(?:\*\*)?[：:](?:\*\*)?\s*',c)]
            previous=(old or {}).get('blocks',[]);matcher=difflib.SequenceMatcher(a=[b['markdown'] for b in previous],b=chunks,autojunk=False)
            reused={j+k:previous[i+k]['id'] for i,j,n in matcher.get_matching_blocks() for k in range(n)}
            blocks=[{'id':reused.get(i,'block-'+uuid.uuid4().hex[:12]),'markdown':text} for i,text in enumerate(chunks) if text.strip()]
        if not isinstance(blocks,list) or not blocks or len(blocks)>2000 or any(not isinstance(block,dict) for block in blocks):raise ValueError('讲解需要包含 1 至 2000 个内容片段对象')
        ids=set();doc=Document(identity,title,'agent-authored','explanation');clean=[]
        for block in blocks:
            bid=check_id(block.get('id'),'讲解片段编号');text=block.get('markdown')
            if bid in ids or not isinstance(text,str) or not text.strip() or len(text)>200000:raise ValueError('讲解片段编号不能重复，正文不能为空')
            ids.add(bid);pid=identity+'--'+bid
            p=doc.add(text,'markdown',translatable=False);p.update(id=pid,zh=text,en='',raw_text=text,section_id=section or identity,source_ids=[pid],precision='authored')
            # 公式、代码和表格通过块编号提问，普通文字按句分配提问框。
            restored=__import__('reader_blocks').sentence_pieces(text)
            old_p=next((r for r in (old or {}).get('passages',[]) if r['id']==pid),None)
            previous_sentences=(old_p or {}).get('sentences',[])
            current_sentences=[s for s in previous_sentences if not s.get('historical')]
            matched={j+k:current_sentences[i+k]['id'] for i,j,n in difflib.SequenceMatcher(a=[s['zh'] for s in current_sentences],b=restored,autojunk=False).get_matching_blocks() for k in range(n)}
            p['sentences']=[{'id':matched.get(i,pid+'-s'+(uuid.uuid4().hex[:12] if old_p else str(i+1))),'en':'','zh':s,'refs':[],'precision':'authored'} for i,s in enumerate(restored)]
            kept={s['id'] for s in p['sentences']}
            p['sentences'].extend({**s,'historical':True} for s in previous_sentences if s['id'] not in kept)
            mapping=validate_source_mapping(material,block)
            p.update(mapping)
            if block.get('unit_ids'):p['unit_ids']=copy.deepcopy(block['unit_ids'])
            if block.get('source_quote'):p['source_quote']=block['source_quote']
            if 'reading_heading'in block:
                if block['reading_heading']is not None and not isinstance(block['reading_heading'],str):raise ValueError('阅读标题需要是文字，清除时传空字符串或 null')
                p['reading_heading']=str(block['reading_heading']or'')[:120]
            elif old_p and 'reading_heading'in old_p:p['reading_heading']=old_p['reading_heading']
            for key in ('heading_premise','heading_question','progress_exempt'):
                if key in block:p[key]=copy.deepcopy(block[key])
            clean.append({'id':bid,'markdown':text,**mapping,**({'unit_ids':copy.deepcopy(block['unit_ids'])}if block.get('unit_ids')else{}),**({'reading_heading':p['reading_heading']}if 'reading_heading'in p else{}),**({'source_quote':block['source_quote']} if block.get('source_quote') else {})})
        for block in clean:
            passage=next(p for p in doc.data['passages']if p['id']==identity+'--'+block['id'])
            for key in ('heading_premise','heading_question','progress_exempt'):
                if key in passage:block[key]=copy.deepcopy(passage[key])
        new={'id':identity,'title':title.strip(),'section_id':section,'source_links':clean_links,
             'blocks':clean,'passages':doc.data['passages'],'passage_ids':[p['id'] for p in doc.data['passages']], 'version':(old or {}).get('version',0)+1}
        new.update(validate_source_mapping(material,data))
        for key in ('plan','writing_version','framework_version','narrative','objectives','framing','evidence'):
            if key in data:new[key]=copy.deepcopy(data[key])
        if old and (set(used_passages or []) & set(old['passage_ids']))-set(new['passage_ids']):
            raise ValueError('已提问或已读的片段需要保留，请沿用该 blocks 编号修改')
        first_explanation=not value['explanations']
        value['explanations']=[new if r['id']==identity else r for r in value['explanations']] if old else value['explanations']+[new]
        if first_explanation:
            value['presentation']={**value.get('presentation',{}),'layout':'lesson'if value['files']else'single','columns':[['original','translation'],['explanation']]if value['files']else[['explanation']],'explanation_id':identity}
        if node is not None:node['explanation_ids']=list(dict.fromkeys(node.get('explanation_ids',[])+[identity]))
        self.write(value,data.get('expected_revision'));return {**{k:v for k,v in new.items() if k!='passages'},'learning_node_ids':[n['id'] for n in value.get('learning_outline',[]) if identity in n.get('explanation_ids',[])]}

    def format_explanation(self,data):
        """只修改已有标题字段；正文、片段编号和用户记录不参与这次写入。"""
        with self.lock:
            value=self.read();identity=check_id(data.get('explanation_id'),'讲解编号')
            row=next((e for e in value['explanations']if e['id']==identity),None)
            if not row:raise ValueError('没有找到这份讲解')
            if data.get('expected_version')!=row['version']:raise ValueError('请先读取讲解的最新版本，再修改标题')
            updates=data.get('headings')
            if not isinstance(updates,list)or not updates:raise ValueError('请提交需要修改标题的片段')
            if set(data)-{'explanation_id','expected_version','expected_revision','headings','material_id'}:raise ValueError('排版接口只接受标题修改；正文请使用讲解写回接口')
            blocks={b['id']:b for b in row['blocks']};passages={p['id']:p for p in row['passages']};changed=[];seen=set()
            for item in updates:
                if not isinstance(item,dict)or set(item)!={'block_id','reading_heading'}:raise ValueError('每项只需 block_id 与 reading_heading')
                bid=item['block_id'];heading=item['reading_heading']
                if bid not in blocks or bid in seen:raise ValueError('标题片段不存在或重复')
                if heading is not None and(not isinstance(heading,str)or len(heading)>120):raise ValueError('标题最多 120 字，清除时传空字符串或 null')
                seen.add(bid);heading=heading or'';p=passages[identity+'--'+bid]
                if blocks[bid].get('reading_heading')!=heading or p.get('reading_heading')!=heading:
                    blocks[bid]['reading_heading']=heading;p['reading_heading']=heading;changed.append(bid)
            if changed:row['version']+=1;self.write(value,data.get('expected_revision'))
            return {'id':identity,'version':row['version'],'changed_blocks':changed,'content_changed':False,'assets_required':False}

    def present(self,data,material):
        value=self.read();layout=data.get('layout')
        if layout not in ('parallel','lesson','three','single','custom'):raise ValueError('请选择原译对照、阅读与讲解、三栏、单栏或自定义布局')
        columns=data.get('columns')
        if not isinstance(columns,list) or not 1<=len(columns)<=3:raise ValueError('阅读区需要一至三栏')
        for column in columns:
            if not isinstance(column,list) or not 1<=len(column)<=2 or any(v not in ('original','translation','explanation') for v in column):raise ValueError('每栏可以展示一或两种内容')
        flat=[v for c in columns for v in c]
        if len(flat)!=len(set(flat)):raise ValueError('同一种内容只展示一次，避免多个窗口互相争夺位置')
        file_id=data.get('file_id')
        if file_id and file_id not in {f['id'] for f in material['reading_files']}:raise ValueError('要展示的原文文件不存在')
        explanation_id=data.get('explanation_id')
        if explanation_id and explanation_id not in {e['id'] for e in material['explanations']}:raise ValueError('要展示的讲解不存在')
        ratios=data.get('ratios',[])
        if not isinstance(ratios,list) or any(not isinstance(x,(float,int)) or not .15<=x<=.85 for x in ratios):raise ValueError('上下窗口比例需要在 0.15 至 0.85 之间')
        value['presentation']={'layout':layout,'columns':columns,'ratios':ratios,'file_id':file_id,'explanation_id':explanation_id}
        self.write(value,data.get('expected_revision'));return value['presentation']
