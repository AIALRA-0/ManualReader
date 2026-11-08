"""复用同项目实际打开过的网页短片段，不把旧回答当证据。"""
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit,unquote
import json,math,re,time,threading,os,hashlib,uuid
from reader_web_sources import pieces,opened
_LOCK=threading.RLock()

def _text(value):
    if isinstance(value,str):return value
    if isinstance(value,list):return '\n'.join(x.get('text','')for x in value if isinstance(x,dict))
    return ''

def _words(text):
    text=str(text).casefold();stop={'什么','么是','为什','什麽','为啥','怎么','怎样','如何','这个','那个','这里','以及','是否','可以','不能','解释','说明','问题','具体','它的','作用','哪些','需要'}
    words=re.findall(r'[a-z][a-z0-9_-]{2,}',text)
    for chinese in re.findall(r'[\u3400-\u9fff]{2,}',text):words.extend(chinese[i:i+2]for i in range(len(chinese)-1)if chinese[i:i+2]not in stop)
    return set(words)

def _url(value):
    parsed=urlsplit(unquote(value));return parsed.scheme+'://'+parsed.netloc+parsed.path.rstrip('/')+('?'+parsed.query if parsed.query else'')

class SourceCache:
    def __init__(self,path,max_age=86400):self.path=Path(path);self.max_age=max_age
    def read(self):
        try:
            data=json.loads(self.path.read_text('utf8'));rows=data.get('fragments',[])if data.get('version',0)>=3 else[]
        except (OSError,ValueError,AttributeError):return []
        return [r for r in rows if isinstance(r,dict)and r.get('used_in_completed_generation')and time.time()-r.get('observed_at',0)<=self.max_age and r.get('url','').startswith(('https://','http://'))and r.get('text')]
    def collect(self,folders):
        with _LOCK:return self._collect(folders)
    def _collect(self,folders):
        existing=self.read();seen={(r['url'],r['text'])for r in existing};added=0
        for folder in folders:
            folder=Path(folder);citations=folder/'cache-citations.json'
            if not citations.exists():continue
            used={_url(u)for u in json.loads(citations.read_text('utf8'))}
            paths=sorted(Path(folder).glob('sent-*.json'),key=lambda p:int(p.stem.split('-')[-1]))
            if not paths:continue
            try:request=json.loads(paths[-1].read_text('utf8'))
            except (OSError,ValueError):continue
            calls={m.get('call_id')for m in request.get('input',[])if m.get('type')=='custom_tool_call'and re.search(r'web__run|web\.run',str(m.get('input','')))}
            for message in request.get('input',[]):
                if message.get('type')!='custom_tool_call_output'or message.get('call_id')not in calls:continue
                text=_text(message.get('output'));blocks=pieces(text)
                for block in blocks:
                    # 只有当前网页块自己的网址和正文可以一起进入缓存。
                    page=opened(block)
                    if page is None:continue
                    title,url,lines=page['title'],page['url'],page['lines']
                    if _url(url)not in used:continue
                    if not lines:continue
                    current=[];size=0
                    def store():
                        nonlocal added
                        if not current:return
                        body='\n'.join(t for _,t in current).strip();key=(url,body)
                        if len(body)>=30 and key not in seen:
                            existing.append({'url':url,'title':title,'text':body,'line_start':int(current[0][0]),'line_end':int(current[-1][0]),'observed_at':time.time(),'used_in_completed_generation':True});seen.add(key);added+=1
                    for number,line in lines:
                        if not line.strip():continue
                        if len(re.findall(r'【\d+†',line))>=6:continue
                        if size+len(line)>850 and current:store();current=[];size=0
                        current.append((number,line));size+=len(line)
                    store()
        self.path.parent.mkdir(parents=True,exist_ok=True);temp=self.path.with_suffix('.'+uuid.uuid4().hex+'.next');temp.write_text(json.dumps({'version':3,'parser_version':'web-source-blocks-v1','fragments':existing[-2400:]},ensure_ascii=False),'utf8');temp.replace(self.path)
        return {'added':added,'total':len(existing),'model_calls':0}
    def select(self,packet,max_characters=12000):
        rows=self.read()
        if not rows:return {'notes':[],'cache_hits':0,'source':'actual_opened_pages'}
        index=[_words(r['text'])for r in rows];df=Counter(w for terms in index for w in terms)
        queries=[q['text']for q in packet.get('questions',[])]
        if not queries:
            sources=[s.get('original')or s.get('en')or s.get('zh')or''for s in packet.get('sources',[])];step=max(1,len(sources)//12);queries=[' '.join(sources[i:i+step])for i in range(0,len(sources),step)]
        candidates={}
        for query in queries:
            words=_words(query)
            scored=[]
            for i,terms in enumerate(index):
                overlap=words&terms
                strong={w for w in overlap if len(w)>=5 and re.fullmatch(r'[a-z][a-z0-9_-]+',w)and df[w]<=max(3,len(rows)//10)}
                if len(overlap)<2 and not strong:continue
                score=sum(math.log(1+len(rows)/(1+df[w]))for w in overlap)/math.sqrt(max(1,len(terms)))
                scored.append((score,i))
            for score,i in sorted(scored,reverse=True)[:3]:candidates[i]=max(candidates.get(i,0),score)
        selected=[];size=0
        for i,score in sorted(candidates.items(),key=lambda r:r[1],reverse=True):
            row=rows[i]
            if size+len(row['text'])>max_characters:continue
            selected.append({**row,'score':round(score,3)});size+=len(row['text'])
        notes=[{'url':r['url'],'text':r['text'],'source_lines':[r['line_start'],r['line_end']]}for r in selected]
        return {'notes':notes,'cache_hits':len(notes),'characters':size,'source':'actual_opened_pages','rule':'这些是实际打开过的来源短片段；直接复用支持本题的事实，缺口继续搜索，不把缓存视为完整结论'}


def for_bridge(bridge):
    """按网站和当前项目分开保存来源片段；不读取或写入连接令牌。"""
    base=Path(os.environ.get('MANUAL_READER_SOURCE_CACHE_DIR')or(Path(os.environ.get('LOCALAPPDATA')or Path.home()/'.cache')/'ManualReader'/'source-cache'))
    material=bridge.headers.get('X-Reader-Material','')
    if not material:return None
    identity=hashlib.sha256((bridge.base+'\n'+material).encode()).hexdigest()[:32]
    return SourceCache(base/(identity+'.json'))

def augment(packet,cache):
    """只附上与本次问题相关的已打开来源；已有原文和问题保持原样。"""
    if cache is None:return packet
    selected=cache.select(packet)
    if not selected['cache_hits']:return packet
    return {**packet,'opened_source_cache':selected}

def remember(cache,folder,value):
    """只有写作通过结构核对后，才允许复用它实际引用过的来源页面。"""
    if cache is None:return {'added':0,'total':0,'model_calls':0}
    def strings(v):
        if isinstance(v,str):yield v
        elif isinstance(v,list):
            for row in v:yield from strings(row)
        elif isinstance(v,dict):
            for row in v.values():yield from strings(row)
    text='\n'.join(strings(value))
    urls=list(dict.fromkeys(re.findall(r'https?://[^\s<>"）)]+',text)))
    folder=Path(folder)
    folders=[folder]+([folder/'supplement']if(folder/'supplement').exists()else[])
    for target in folders:(target/'cache-citations.json').write_text(json.dumps(urls,ensure_ascii=False),'utf8')
    return cache.collect(folders)
