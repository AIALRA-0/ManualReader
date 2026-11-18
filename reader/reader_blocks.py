"""先保留完整数学环境和代码，再划分普通段落；旧片段只合并显示。"""
import copy,re

ATOMIC=re.compile(r'(?m:^[ \t]*(?P<fence>`{3,}|~{3,})[^\n]*\n[\s\S]*?^[ \t]*(?P=fence)[ \t]*$)|`[^`\n]*`|\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|(?<![\\$])\$\$[\s\S]*?\$\$(?!\$)|(?<![\\$])\$(?!\$)[^$\n]+(?<!\\)\$|\\begin\{(?P<env>aligned\*?|align\*?|gathered|gather\*?|cases|[pbBvV]?matrix|array|equation\*?)\}[\s\S]*?\\end\{(?P=env)\}')

def normalize_math_escapes(text):
    """只纠正数学定界符的重复转义，代码与矩阵换行仍保持原样。"""
    saved=[]
    def hide(m):saved.append(m[0]);return '\ue300'+str(len(saved)-1)+'\ue301'
    masked=re.sub(r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`',hide,text)
    changes=[]
    def fix(m):
        changes.append({'kind':'repeated_math_delimiter','before':m[0],'after':'\\'+m[1]});return '\\'+m[1]
    masked=re.sub(r'\\{2,}([\[\]()])',fix,masked)
    commands=r'(?:frac|tfrac|dfrac|mid|leq|geq|quad|qquad|text|mathrm|mathbf|begin|end|sum|max|min|sqrt|cdot|times|left|right|in|partial|lambda|alpha|beta|phi)'
    def inside(m):
        def command(c):changes.append({'kind':'repeated_math_command','before':c[0],'after':'\\'+c[1]});return '\\'+c[1]
        return re.sub(r'\\{2,}('+commands+r')\b',command,m[0])
    masked=ATOMIC.sub(inside,masked)
    return re.sub(r'\ue300(\d+)\ue301',lambda m:saved[int(m[1])],masked),changes

def markdown_chunks(text):
    """空行只切普通段落，不切公式、代码围栏或完整表格。"""
    saved=[]
    def hide(m):saved.append(m[0]);return '\ue200'+str(len(saved)-1)+'\ue201'
    masked=ATOMIC.sub(hide,text.strip())
    return [re.sub(r'\ue200(\d+)\ue201',lambda m:saved[int(m[1])],p).strip() for p in re.split(r'\n[ \t]*\n+',masked) if p.strip()]

def sentence_pieces(text):
    saved=[]
    def hide(m):saved.append(m[0]);return '\ue200'+str(len(saved)-1)+'\ue201'
    masked=ATOMIC.sub(hide,text)
    pieces=[masked]if re.search(r'^\s*(?:[|#>]|```|~~~)',text,re.M) else re.split(r'(?<=[。！？!?；;])\s*|(?<=[a-zA-Z])\.\s+(?=[A-Z])',masked)
    return [re.sub(r'\ue200(\d+)\ue201',lambda m:saved[int(m[1])],p).strip()for p in pieces if p.strip()]

def project_legacy_math(passages):
    """相邻旧片段的数学边界明确时合并显示，原编号和原文仍在记录中。"""
    changes=[];i=0
    while i<len(passages):
        first=passages[i];text=first.get('display_markdown',first.get('zh')or first.get('en')or'').strip()
        if first.get('content_kind')!='explanation'or text not in ('[','\\[','$$'):
            i+=1;continue
        close={'[':']','\\[':'\\]','$$':'$$'}[text];end=None
        for j in range(i+1,min(len(passages),i+60)):
            p=passages[j]
            if p.get('explanation_id')!=first.get('explanation_id')or p.get('section_id')!=first.get('section_id'):break
            candidate=p.get('display_markdown',p.get('zh')or p.get('en')or'').strip()
            if candidate==close:end=j;break
        if end is None:i+=1;continue
        middle='\n\n'.join(p.get('display_markdown',p.get('zh')or p.get('en')or'')for p in passages[i+1:end])
        if not re.search(r'\\(?:begin|frac|tfrac|sum|leq|geq|text|sqrt|mathbf)\b',middle):i=end+1;continue
        first['display_markdown']='\\[\n'+middle+'\n\\]';first['display_group_ids']=[p['id']for p in passages[i:end+1]]
        for p in passages[i+1:end+1]:p['presentation_omit']=True;p['display_group_leader']=first['id']
        changes.append({'leader':first['id'],'members':first['display_group_ids']});i=end+1
    return changes

def sequence_context(nodes,identity):
    """目录已经按前序保存；上一条和下一条与层级及折叠状态无关。"""
    index=next(i for i,n in enumerate(nodes)if n['id']==identity)
    return {'previous':copy.deepcopy(nodes[index-1])if index else None,'current':copy.deepcopy(nodes[index]),'next':copy.deepcopy(nodes[index+1])if index+1<len(nodes)else None}
