"""Read and validate the inputs and outputs of the Reader postprocessing DAG."""
from collections import Counter, defaultdict
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import re
from zipfile import ZipFile

from jsonschema import Draft202012Validator
from reader_p0_resources import build_inventory, scan_markdown
from reader_p15_revision import invert, layer, mechanical


def read(p):
    return json.loads(Path(p).read_bytes())


def sha(x):
    return sha256(x.encode('utf8') if isinstance(x,str) else x).hexdigest()


def fingerprint(x):
    return sha(json.dumps(x,sort_keys=True,ensure_ascii=False,separators=(',',':')))


def atomic(p, value):
    p=Path(p)
    p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_name(p.name+'.tmp')
    data=value if isinstance(value,bytes) else (json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()
    t.write_bytes(data)
    t.replace(p)


def errors(schema,value):
    return sorted('/'.join(str(x) for x in e.absolute_path)+': '+e.message
                  for e in Draft202012Validator(schema).iter_errors(value))


class Spec:
    """The attached instructions and JSON contracts are copied without rewriting."""
    def __init__(self, zip_path, destination):
        self.root=destination
        self.prompts={}
        self.schemas={}
        with ZipFile(zip_path) as z:
            if z.testzip():
                raise ValueError('规格包有文件未通过校验')
            for name in z.namelist():
                target=(destination/name).resolve()
                if not target.is_relative_to(destination.resolve()) or name.endswith('/'):
                    raise ValueError('规格包路径不合法')
                data=z.read(name)
                atomic(target,data)
                if name.startswith('prompts/'):
                    self.prompts[Path(name).stem.split('-')[0]]=data.decode()
                if name.startswith('schemas/'):
                    schema=json.loads(data)
                    Draft202012Validator.check_schema(schema)
                    self.schemas[Path(name).name.split('.schema')[0]]=schema
            ledger=json.loads(z.read('PROMPT_AND_CONTRACT_SHA256.json'))
            for section in ('prompts','contracts'):
                for name, expected in ledger[section].items():
                    prefix='prompts/' if section=='prompts' else 'schemas/'
                    if sha(z.read(prefix+name))!=expected:
                        raise ValueError('规格包内哈希不一致：'+name)
        if len(self.prompts)!=9 or len(self.schemas)!=11:
            raise ValueError('未找到用户确认版里的 9 份提示词和 11 份数据约定')

    def checked(self,key,value):
        check=errors(self.schemas[key],value)
        if check:
            raise ValueError('；'.join(check[:5]))
        return value


def with_native_code(text,index):
    code=[x for x in scan_markdown(text,details=True)['resources'] if x['kind']=='code']
    index=deepcopy(index)
    known={(r['kind'],p['start_offset'],p['end_offset']) for r in index['resources']
            if (p:=r.get('preview_location'))}
    rows=build_inventory(index['sample_id'],text.encode(),code,extra_kinds={'code'})['resources'] if code else []
    for r in rows:
        loc=r['location']
        if ('code',loc['start_offset'],loc['end_offset']) not in known:
            index['resources'].append(r|{'preview_location':loc,'preview_state':'active'})
    return index


def format_body(text,index):
    steps=mechanical(text)
    revised, registered, log=layer(text,index,steps,coordinate='body.normalized.md')
    if invert(revised,log)!=text or mechanical(revised):
        raise ValueError('格式补丁不能恢复原文，或运行两次结果不同')
    return revised,registered,log


TYPE={'paragraph_open':'paragraph','table_open':'table','fence':'code','code_block':'code'}
R_KIND={'formula_display':'display_formula','mermaid':'mermaid','table':'table','code':'code','image':'image'}


def structure(text,index, title):
    """Use actual headings and block positions without rewriting a prose sentence."""
    native=scan_markdown(text,details=True)
    active=[r for r in index['resources'] if r.get('preview_location')]
    signature=[text[b['start']:b['end']] for b in native['blocks'] if b['type']!='heading_open']
    headings={b['start']:b for b in native['headings']}
    edits=[]
    for start,h in headings.items():
        m=re.match(r'(#{1,6})(\s+)',text[start:])
        # The first heading level below the external chapter title is H2.
        if m and len(m[1])==1:
            edits.append({'start_offset':start,'end_offset':start+1,'old':'#','new':'##',
                          'kind':'normalize_heading','heading':h['name']})
    before=text
    text,index,applied=layer(text,index,edits,coordinate='body.structured.md')
    final=scan_markdown(text,details=True)
    if [text[b['start']:b['end']] for b in final['blocks'] if b['type']!='heading_open']!=signature:
        raise ValueError('整理标题时动到了其他正文')
    if invert(text,applied)!=before:
        raise ValueError('标题补丁不能恢复原稿')
    active=[r for r in index['resources'] if r.get('preview_location')]
    heads={b['start']:b for b in final['headings']}
    parts=[]; blocks=[]; resource_to_block=[]
    part=None
    for raw in final['blocks']:
        a,b=raw['start'],raw['end']
        if b<=a:
            continue
        h=heads.get(a)
        if part is None or (h and len(re.match(r'#{1,6}',text[a:])[0])<=2 if h and re.match(r'#{1,6}',text[a:]) else False):
            heading=h['name'] if h else title
            part={'part_id':'part-'+sha(f'{a}\0{heading}')[:14],'title':heading,'block_ids':[]}
            parts.append(part)
        inside=[r for r in active if a<=r['preview_location']['start_offset']
                and r['preview_location']['end_offset']<=b]
        kind=TYPE.get(raw['type'],'other')
        direct=next((r for r in inside if r['kind'] in R_KIND and
                    text[a:b].strip()==r['raw_content'].strip()),None)
        if direct:
            kind=R_KIND[direct['kind']]
        elif kind=='paragraph' and text[a:b].strip() in ('$$','\\[','\\]'):
            # A display math resource may span several parser tokens.
            continue
        bid='block-'+sha(f'{a}\0{text[a:b]}')[:16]
        row={'block_id':bid,'part_id':part['part_id'],'kind':kind,'start_offset':a,'end_offset':b,
             'resource_ids':[r['resource_id'] for r in inside],'text_sha256':sha(text[a:b])}
        blocks.append(row); part['block_ids'].append(bid)
    # The native tokenizer can give a display formula soft sub-blocks. Merge those spans.
    for r in active:
        if any(r['resource_id'] in b['resource_ids'] for b in blocks):
            continue
        p=r['preview_location']; a,z=p['start_offset'],p['end_offset']
        hits=[b for b in blocks if a<b['end_offset'] and b['start_offset']<z]
        part_id=(hits or [blocks[0]])[0]['part_id']
        a=min([a]+[b['start_offset'] for b in hits])
        z=max([z]+[b['end_offset'] for b in hits])
        transferred=[rid for b in hits for rid in b['resource_ids']]
        blocks=[b for b in blocks if b not in hits]
        blocks.append({'block_id':'block-'+sha(f'{a}\0{text[a:z]}')[:16],
            'part_id':part_id,'kind':R_KIND.get(r['kind'],'other'),'start_offset':a,'end_offset':z,
            'resource_ids':list(dict.fromkeys(transferred+[r['resource_id']])), 'text_sha256':sha(text[a:z])})
    blocks.sort(key=lambda b:b['start_offset'])
    for p in parts:
        p['block_ids']=[b['block_id'] for b in blocks if b['part_id']==p['part_id']]
    parts=[p for p in parts if p['block_ids']]
    covered=set()
    cursor=0
    for b in blocks:
        a,z=b['start_offset'],b['end_offset']
        if a<cursor or text[cursor:a].strip():
            raise ValueError('正文切分有重叠，或遗漏了实际文字')
        cursor=z
        for rid in b['resource_ids']:
            if rid in covered:
                raise ValueError('一个资源出现位置被分到两个正文块')
            covered.add(rid)
            resource_to_block.append({'resource_id':rid,'block_id':b['block_id']})
    if text[cursor:].strip() or covered!={r['resource_id'] for r in active}:
        raise ValueError('有正文或资源没有定位到正文块')
    return text,index,{'schema_version':'reader-structure-v1','body_sha256':sha(text),
        'parts':parts,'blocks':blocks,'resource_to_block':resource_to_block}, {
        'body_sha256':sha(before),'patches':[{'action':'normalize_heading','heading_id':None,
             'before':p['old'],'after':p['new'],'anchor_block_id':next(
                 b['block_id'] for b in blocks if b['start_offset']==p['view_start']),
             'reason':'当前章节标题自带一级标题；正文同层标题用二级'} for p in applied]}


def original_text(p):
    return next((p[k] for k in ('original','en','raw_text','text','zh','alt')
                  if isinstance(p.get(k),str) and p[k].strip()),'')


def needs_translation(p):
    if p['kind'] in ('image','visual','page_image','figure','scan','code','equation','formula'):
        return False
    chars=Counter('han' if '\u3400'<=x<='\u9fff' else 'latin'
                  for x in p['original'] if '\u3400'<=x<='\u9fff' or x.isascii() and x.isalpha())
    return chars['latin']>0 and chars['han']*3<chars['latin']


def compact(s):
    return ' '.join(s.split())


def tokens(s):
    return set(x.lower() for x in re.findall(
        r'[A-Za-z][A-Za-z0-9-]{2,}|\d+(?:\.\d+)?|\\[A-Za-z]+|[\u3400-\u9fff]{2,}',s))


def evidence_match(quote,source):
    q=compact(quote).strip('“”「」"')
    return len(q)>=4 and q in compact(source)


def verified_mapping(candidates,sources,blocks,translated):
    by_source={p['id']:p for p in sources}
    verified={}; rejected=[]
    for proposed in candidates:
        bid=proposed['block_id']; quote=proposed['evidence_quote']
        for sid in proposed['source_ids']:
            source=by_source.get(sid)
            if (bid not in blocks or not source or proposed['kind']=='unmapped' or
                proposed['confidence'] not in ('validated','tentative') or not evidence_match(quote,source['original'])):
                rejected.append({'block_id':bid,'source_id':sid,'reason':'原文摘句或锚点未能逐字核对'})
                continue
            block=blocks[bid]
            src=quote+' '+translated.get(sid,'')
            if proposed['confidence']=='tentative':
                literal=compact(quote)
                peer=compact(block)
                # A shared, case-preserved name can locate an explicit quote across languages.
                names={compact(x) for x in re.findall(r'(?:[A-Z]{2,}|[A-Z][a-z]+)(?:[ -]+(?:[A-Z]{2,}|[A-Z][a-z]+))+',quote)}
                named=any(len(x)>=10 and x in peer for x in names)
                if proposed['kind']!='explicit' or not (len(literal)>=10 and literal in peer or named):
                    rejected.append({'block_id':bid,'source_id':sid,'reason':'模型只给出候选，正文与原文没有逐字共用这段证据'})
                    continue
            shared=tokens(src)&tokens(block)
            exact=bool([s for s in re.findall(r'[\u3400-\u9fff]{4,}',quote)
                        if any(s[n:n+4] in block for n in range(len(s)-3))])
            if not exact and not any(re.search(r'[A-Za-z]|\\',s) for s in shared) and not any(
                    s.isnumeric() and len(s)>1 for s in shared):
                rejected.append({'block_id':bid,'source_id':sid,'reason':'摘句存在，但与该正文块的关系尚无可核对的共同对象'})
                continue
            verified[sid,bid]={'source_id':sid,'block_id':bid,'kind':proposed['kind'],
                               'evidence_quote':quote,'validated':True}
    return list(verified.values()),rejected


def verified_media_mapping(folder,assets,index,structure,sources):
    by_path={item['path']:item for item in assets if item.get('original')}
    anchor={row['resource_id']:row['block_id'] for row in structure['resource_to_block']}
    result=[]
    for resource in index['resources']:
        if resource['kind']!='image' or resource['status']!='ok':
            continue
        relative=resource.get('media_path')
        item=by_path.get(relative)
        block=anchor.get(resource['resource_id'])
        if not item or not block:
            continue
        file=(folder/relative).resolve()
        if not file.is_relative_to(folder.resolve()) or not file.is_file() or sha(file.read_bytes())!=item['sha256']:
            continue
        for sid in item['source_ids']:
            if sid in sources:
                result.append({'source_id':sid,'block_id':block,'kind':'explicit',
                    'evidence_quote':'SHA-256 '+item['sha256'],'validated':True})
    return result


def coverage(sample,source_version,body_sha,sources,blocks,translations,edges):
    srcs=[p['id'] for p in sources]; foreign=[p['id'] for p in sources if needs_translation(p)]
    st=[{'source_id':s,'translation_id':s,'relation':'identity'} for s in srcs if s not in foreign]
    st.extend({'source_id':s,'translation_id':v['translation_id'],'relation':'translated'}
              for s,v in translations.items() if s in foreign)
    tids=list(dict.fromkeys(r['translation_id'] for r in st))
    se=[r for r in edges if r['source_id'] in srcs and r['block_id'] in blocks and r['validated']]
    measurements={}; gaps=[]
    def measure(key,side,eligible,rows,field,reason):
        used={r[field] for r in rows}
        missing=[i for i in eligible if i not in used]
        measurements[key]=(len(eligible)-len(missing))/len(eligible) if eligible else 1.0
        gaps.extend({'side':side,'id':i,'reason':reason} for i in missing)
    measure('source_to_translation','source_translation',srcs,st,'source_id','尚未读回经校验的译文')
    measure('translation_to_source','translation_source',tids,st,'translation_id','译文没有真实原文指向')
    measure('source_to_block','source_block',srcs,se,'source_id','没有得到经证据核对的讲解覆盖')
    measure('block_to_source','block_source',blocks,se,'block_id','没有得到经证据核对的来源')
    return {'schema_version':'source-coverage-v2','sample_id':sample,'source_version':source_version,
            'body_sha256':body_sha,'eligible_source_ids':srcs,'translation_required_source_ids':foreign,
            'eligible_translation_ids':tids,'eligible_block_ids':list(blocks),'source_translation_links':st,
            'source_block_links':se,'coverage':measurements,'gaps':gaps,
            'status':'coverage_blocked' if gaps else 'complete'}
