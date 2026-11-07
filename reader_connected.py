"""核对同次写作的前提、问题、回顾，再按原文顺序排版。"""
import copy,re
RECAP_FIELDS=('context','problem','claim','reason','evidence','implication')
START_TITLES={'previous':'接着上一节','current':'这一节要弄明白什么'}
END_TITLES={'recap':'把这一节串起来','next':'接下来要解决什么'}
_PROTECTED=re.compile(r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|\$[^$\n]+\$|https?://[^\s)]+')
def prose_punctuation(text):
 chunks=[];cursor=0
 for m in _PROTECTED.finditer(text):chunks.extend((text[cursor:m.start()].replace('。','；'),m[0]));cursor=m.end()
 chunks.append(text[cursor:].replace('。','；'));return ''.join(chunks)

def validate_connected(explanation):
 value=copy.deepcopy(explanation);framing=value.get('framing',{});parts=value.get('parts',[])
 if not parts:raise ValueError('需要完整讲解正文')
 ids=[p['id']for p in parts]
 for p in parts:
  if not all(isinstance(p.get(k),str)and p[k].strip()for k in('heading','premise','question','markdown')):raise ValueError('每部分需要短标题、独立前提、独立问题和正文：'+p['id'])
  if len(p['heading'])>24 or '\n'in p['heading']:raise ValueError('正文标题需要简短：'+p['id'])
  if p['premise'].strip()==p['question'].strip():raise ValueError('前提与问题不能重复：'+p['id'])
  for key in('premise','question','markdown'):p[key]=prose_punctuation(p[key])
  p['question']=p['question'].strip().rstrip('。；;，,？?')
 for kind in('prerequisites','facts','transitions'):
  for row in value.get('evidence',{}).get(kind,[]):row['quote']=prose_punctuation(row['quote'])
 if framing.get('map_heading')!='阅读流程':raise ValueError('路线栏目固定使用阅读流程')
 if [r.get('role')for r in framing.get('start',[])]not in (['current'],['previous','current']):raise ValueError('开头按前文、本节分开')
 if [r.get('role')for r in framing.get('end',[])]not in (['recap'],['recap','next']):raise ValueError('结尾按回顾、下一节分开')
 for r in framing['start']:
  if r.get('heading')!=START_TITLES[r['role']]or not isinstance(r.get('markdown'),str)or not r['markdown'].strip():raise ValueError('开头缺少单独的正文或固定栏目名')
  r['markdown']=prose_punctuation(r['markdown'])
 recaps=framing['end'][0].get('bullets',[])
 if [b.get('part_id')for b in recaps]!=ids:raise ValueError('结尾要点必须按顺序逐一回扣正文和流程')
 headings={p['id']:p['heading']for p in parts}
 for b in recaps:
  if not all(isinstance(b.get(k),str)and b[k].strip()and '\n'not in b[k]for k in RECAP_FIELDS):raise ValueError('回顾每个要点需要六句完整说明：'+str(b.get('part_id')))
  for key in RECAP_FIELDS:b[key]=prose_punctuation(b[key])
 framing['end'][0]['markdown']='\n\n'.join('- **'+headings[b['part_id']]+'**：'+''.join(b[k].strip().rstrip('。；;')+'；'for k in RECAP_FIELDS)for b in recaps)
 for r in framing['end']:
  if r.get('heading')!=END_TITLES[r['role']]or not isinstance(r.get('markdown'),str)or not r['markdown'].strip():raise ValueError('结尾缺少独立回顾或下一节引入')
  r['markdown']=prose_punctuation(r['markdown'])
 value['framing']=framing;return value

def flow_steps(parts):return [{'part_id':p['id'],'label':p['heading'],'premise':p['premise'],'question':p['question']}for p in parts]
