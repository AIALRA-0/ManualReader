"""分别保存原文文件、译文和讲解，所有提问继续引用稳定的段落与片段编号。"""
from pathlib import Path, PurePosixPath
import base64, copy, difflib, json, re, uuid
from source_parser import parse, Document, apply_translations, attach_reading_segments
from reader_learning import learning_payload,group_pdf_figures
from reader_coverage import validate_source_mapping


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


class Workspace:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder/'reading-workspace.json'
        self.files = self.folder/'reading-files'

    def read(self):
        return json.loads(self.path.read_text('utf8')) if self.path.exists() else {'revision':0, 'files':[], 'explanations':[], 'presentation':{}}

    def revision(self):
        return self.path.stat().st_mtime_ns if self.path.exists() else 0

    def write(self, value, expected=None):
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
        value=self.read();result=copy.deepcopy(original)
        primary={'id':'primary','name':original.get('original_name') or original['title'], 'kind':original.get('kind'),
                 'outline':copy.deepcopy(original['outline']), 'passage_ids':[p['id'] for p in original['passages']],
                 'original':bool(original.get('original_file') or original.get('kind')=='pdf'), 'primary':True}
        for p in result['passages']:
            p.setdefault('content_kind','explanation' if original.get('reader_mode')=='authored' else 'source')
            p.setdefault('file_id','primary')
        files=([primary] if original['passages'] else [])+[{k:v for k,v in row.items() if k!='document'} for row in value['files']]
        for row in value['files']:
            document=copy.deepcopy(row['document'])
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
        result['reading_files']=files
        result['explanations']=[{k:v for k,v in row.items() if k!='passages'} for row in value['explanations']]
        result['workspace_revision']=value['revision'];result['presentation']=value['presentation']
        result['learning']=learning_payload(result,value,{})
        result['association_rules']=RULES
        return attach_reading_segments(result)

    def add_file(self,data):
        return self.import_source(data)

    def import_source(self,data,*,allow_private_urls=False):
        """用户与 Agent 都向当前 Workspace 导入；本机路径不属于远程请求的输入。"""
        from reader_import import SourceImporter
        return SourceImporter(self,allow_private_urls=allow_private_urls).import_source(data)

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
            chunks=re.findall(r'```[\s\S]*?```|~~~[\s\S]*?~~~|(?:(?!\n\s*\n)[\s\S])+',markdown.strip())
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
            protected={}
            def protect(match):
                key=len(protected);protected[key]=match[0];return '\ue000'+str(key)+'\ue001'
            safe=re.sub(r'```[\s\S]*?```|`[^`]*`|\\\([\s\S]*?\\\)|\\\[[\s\S]*?\\\]|\$\$[\s\S]*?\$\$|\$[^$\n]+\$',protect,text)
            pieces=[safe] if re.search(r'^\s*(?:[|#>]|```|~~~)',text,re.M) else re.split(r'(?<=[。！？!?；;])\s*|(?<=[a-zA-Z])\.\s+(?=[A-Z])',safe)
            restored=[re.sub(r'\ue000(\d+)\ue001',lambda m:protected[int(m[1])],s).strip() for s in pieces if s.strip()]
            old_p=next((r for r in (old or {}).get('passages',[]) if r['id']==pid),None)
            previous_sentences=(old_p or {}).get('sentences',[])
            current_sentences=[s for s in previous_sentences if not s.get('historical')]
            matched={j+k:current_sentences[i+k]['id'] for i,j,n in difflib.SequenceMatcher(a=[s['zh'] for s in current_sentences],b=restored,autojunk=False).get_matching_blocks() for k in range(n)}
            p['sentences']=[{'id':matched.get(i,pid+'-s'+(uuid.uuid4().hex[:12] if old_p else str(i+1))),'en':'','zh':s,'refs':[],'precision':'authored'} for i,s in enumerate(restored)]
            kept={s['id'] for s in p['sentences']}
            p['sentences'].extend({**s,'historical':True} for s in previous_sentences if s['id'] not in kept)
            mapping=validate_source_mapping(material,block)
            p.update(mapping)
            clean.append({'id':bid,'markdown':text,**mapping})
        new={'id':identity,'title':title.strip(),'section_id':section,'source_links':clean_links,
             'blocks':clean,'passages':doc.data['passages'],'passage_ids':[p['id'] for p in doc.data['passages']], 'version':(old or {}).get('version',0)+1}
        new.update(validate_source_mapping(material,data))
        if old and (set(used_passages or []) & set(old['passage_ids']))-set(new['passage_ids']):
            raise ValueError('已提问或已读的片段需要保留，请沿用该 blocks 编号修改')
        value['explanations']=[new if r['id']==identity else r for r in value['explanations']] if old else value['explanations']+[new]
        if node is not None:node['explanation_ids']=list(dict.fromkeys(node.get('explanation_ids',[])+[identity]))
        self.write(value,data.get('expected_revision'));return {**{k:v for k,v in new.items() if k!='passages'},'learning_node_ids':[n['id'] for n in value.get('learning_outline',[]) if identity in n.get('explanation_ids',[])]}

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
