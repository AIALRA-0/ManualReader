"""核对同次写作的前提、问题、回顾，再按原文顺序排版。"""
import copy,re
RECAP_FIELDS=('context','problem','claim','reason','evidence','implication')
START_TITLES={'previous':'接着上一节','current':'这一节要弄明白什么'}
FIRST_START_TITLE='从眼前的任务说起'
END_TITLES={'recap':'把这一节串起来','next':'接下来要解决什么'}
_PROTECTED=re.compile(r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|\$[^$\n]+\$|https?://[^\s)]+')
def prose_punctuation(text):
 chunks=[];cursor=0
 for m in _PROTECTED.finditer(text):chunks.extend((text[cursor:m.start()].replace('。','；'),m[0]));cursor=m.end()
 chunks.append(text[cursor:].replace('。','；'));return ''.join(chunks)


def prose_line_breaks(text):
 """在偏长的叙述行内按已有句读换行，保留段落、列表和技术内容。"""
 protected = list(_PROTECTED.finditer(text))
 lines = []
 offset = 0
 protection = 0
 for line in text.splitlines(keepends=True):
  end = offset+len(line)
  content_end = len(line.rstrip())
  while protection<len(protected) and protected[protection].end()<=offset:
   protection+=1
  if (content_end>82 and '|' not in line and not re.search(r'\[[^\]\n]+\]\(',line)
      and not re.match(r'^\s*(?:[#>]|[-*+]\s|\d+[.)]\s|<)',line) and not line.startswith((' ','\t'))):
   index = protection
   start = offset
   cuts = []
   for match in re.finditer(r'[；。！？](?![”’」』）)])',line):
    position = offset+match.start()
    while index<len(protected) and protected[index].end()<=position:
     index+=1
    if index<len(protected) and protected[index].start()<=position:
     continue
    if position-start>=38 and match.end()<content_end:
     cuts.append(match.end())
     start = position+1
   if cuts:
    line = '\n'.join([line[:cuts[0]],*(line[a:b].lstrip(' \t') for a,b in zip(cuts,[*cuts[1:],len(line)]))])
  lines.append(line)
  offset = end
 return ''.join(lines)


def validate_connected(explanation,preserve_prose=False):
 punctuation=(lambda text:text) if preserve_prose else prose_punctuation
 value=copy.deepcopy(explanation);framing=value.get('framing',{});parts=value.get('parts',[])
 if not parts:raise ValueError('需要完整讲解正文')
 ids=[p['id']for p in parts]
 for p in parts:
  if not all(isinstance(p.get(k),str)and p[k].strip()for k in('heading','premise','question','markdown')):raise ValueError('每部分需要短标题、呈上、启下和正文：'+p['id'])
  if len(p['heading'])>24 or '\n'in p['heading']:raise ValueError('正文标题需要简短：'+p['id'])
  if p['premise'].strip()==p['question'].strip():raise ValueError('呈上和启下需要各自交代不同的内容：'+p['id'])
  for key in('premise','question','markdown'):p[key]=prose_line_breaks(punctuation(p[key]))
 for kind in('prerequisites','facts','transitions'):
  for row in value.get('evidence',{}).get(kind,[]):row['quote']=punctuation(row['quote'])
 if framing.get('map_heading')!='阅读流程':raise ValueError('路线栏目固定使用阅读流程')
 if [r.get('role')for r in framing.get('start',[])]not in (['current'],['previous','current']):raise ValueError('开头按前文、本节分开')
 if [r.get('role')for r in framing.get('end',[])]not in (['recap'],['recap','next']):raise ValueError('结尾按回顾、下一节分开')
 for r in framing['start']:
  headings={START_TITLES[r['role']]}
  if r['role']=='previous':headings.add(FIRST_START_TITLE)
  if r.get('heading') not in headings or not isinstance(r.get('markdown'),str)or not r['markdown'].strip():raise ValueError('开头缺少单独的正文或固定栏目名')
  r['markdown']=prose_line_breaks(punctuation(r['markdown']))
 recaps=framing['end'][0].get('bullets',[])
 if [b.get('part_id')for b in recaps]!=ids:raise ValueError('结尾要点必须按顺序逐一回扣正文和流程')
 headings={p['id']:p['heading']for p in parts}
 for b in recaps:
  if not all(isinstance(b.get(k),str)and b[k].strip()and '\n'not in b[k]for k in RECAP_FIELDS):raise ValueError('回顾每个要点需要六句完整说明：'+str(b.get('part_id')))
  for key in RECAP_FIELDS:b[key]=punctuation(b[key])
 framing['end'][0]['markdown']=recap_markdown((headings[b['part_id']], b) for b in recaps)
 for r in framing['end']:
  if r.get('heading')!=END_TITLES[r['role']]or not isinstance(r.get('markdown'),str)or not r['markdown'].strip():raise ValueError('结尾缺少独立回顾或下一节引入')
  r['markdown']=prose_line_breaks(punctuation(r['markdown']))
 value['framing']=framing;return value

def recap_markdown(groups):
 """每个阅读步骤占一项，在同一项内分行读完六句。"""
 items=[]
 for heading,recap in groups:
  lines=[recap[key].strip().rstrip('。；;') for key in RECAP_FIELDS]
  items.append('- **'+heading.strip()+'**\n\n'+'  '+'  \n  '.join(lines))
 return '\n\n'.join(items)

def flow_steps(parts):return [{'part_id':p['id'],'label':p['heading'],'premise':p['premise'],'question':p['question']}for p in parts]
