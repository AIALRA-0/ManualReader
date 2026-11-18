"""固定接口的组合逻辑在无网络、只挂载练习目录的 Linux 环境编译测试。"""
from pathlib import Path
import itertools,json,re,subprocess,time,uuid,os,tempfile,sys
OUT=Path(__file__).resolve().parent.parent
INPUTS=('csr_enable','csr_sw_access','ext_access','csr_write','ext_write','csr_tag','ext_tag','csr_wdata','ext_wdata')
OUTPUTS=('core_access','core_write','core_tag','core_wdata')
HEADER='''module lru_arbiter (
input logic csr_enable, csr_sw_access, ext_access,
input logic csr_write, ext_write,
input logic [7:0] csr_tag, ext_tag,
input logic [15:0] csr_wdata, ext_wdata,
output logic core_access, core_write,
output logic [7:0] core_tag,
output logic [15:0] core_wdata
);
'''

def assignments(code):
    if not isinstance(code,str)or not code.strip()or len(code)>10000:raise ValueError('请填写四条 assign 组合逻辑')
    code=re.sub(r'/\*[\s\S]*?\*/|//[^\n]*','',code).strip()
    if code.startswith('module'):
        header=re.match(r'module\b[\s\S]*?\);',code)
        if not header or re.sub(r'\s+','',header[0])!=re.sub(r'\s+','',HEADER):raise ValueError('这道题使用固定模块接口，只修改四条 assign 即可')
        code=code[header.end():].strip()
        if not code.endswith('endmodule'):raise ValueError('模块末尾需要 endmodule')
        code=code[:-len('endmodule')].strip()
    matches=list(re.finditer(r'assign\s+([A-Za-z_]\w*)\s*=\s*([^;]*);',code))
    leftover=re.sub(r'assign\s+([A-Za-z_]\w*)\s*=\s*([^;]*);','',code)
    if leftover.strip()or len(matches)!=4 or sorted(m[1]for m in matches)!=sorted(OUTPUTS):raise ValueError('本题只接受四条 assign，每个输出各赋值一次')
    allowed=set(INPUTS)
    for match in matches:
        expression=match[2]
        if any(name not in allowed for name in re.findall(r'[A-Za-z_]\w*',expression)):raise ValueError('表达式只能使用题目列出的输入信号')
        if re.search(r'[^A-Za-z_0-9\s&|^~!?:()+*/%<>=-]',expression):raise ValueError('表达式中有本题不使用的符号')
    return '\n'.join(m[0]for m in matches)

def fixtures():
    rows=[]
    for flags in itertools.product((0,1),repeat=5):
        for csrt,extt,csrd,extd in [(0x12,0xA7,0x1357,0xCAFE),(0,255,0,65535),(255,0,65535,0)]:
            values=dict(zip(INPUTS,[*flags,csrt,extt,csrd,extd]));sw=values['csr_sw_access']
            expected={'core_access':int(bool(values['csr_enable']and(sw or values['ext_access']))),'core_write':values['csr_write']if sw else values['ext_write'],'core_tag':csrt if sw else extt,'core_wdata':csrd if sw else extd}
            rows.append({'id':len(rows)+1,'inputs':values,'expected':expected})
    return rows

def testbench(rows):
    text=['module tb;','logic csr_enable, csr_sw_access, ext_access, csr_write, ext_write;','logic [7:0] csr_tag, ext_tag;','logic [15:0] csr_wdata, ext_wdata;','wire core_access, core_write;','wire [7:0] core_tag;','wire [15:0] core_wdata;','lru_arbiter dut(.*);','initial begin']
    for row in rows:
        text+=[' '.join(name+' = '+str(value)+';'for name,value in row['inputs'].items()),'#1;',f'$display("ROW|{row["id"]}|%0d|%0d|%0d|%0d", core_access, core_write, core_tag, core_wdata);']
    return '\n'.join(text+['$finish;','end','endmodule'])

def run(code):
    total=len(fixtures());began=time.perf_counter()
    try:body=assignments(code)
    except ValueError as error:return {'correct':False,'status':'input_error','passed':0,'total':total,'correction':str(error),'grading_method':'iverilog_sandbox','seconds':round(time.perf_counter()-began,3)}
    identity='run-'+uuid.uuid4().hex[:12];base=Path(os.environ.get('MANUAL_READER_CODE_RUNS',str(Path(tempfile.gettempdir())/'manual-reader-code-runs')));base.mkdir(parents=True,exist_ok=True);job=base/identity;job.mkdir(mode=0o700)
    (job/'solution.sv').write_text(HEADER+body+'\nendmodule\n','utf8');rows=fixtures();(job/'tb.sv').write_text(testbench(rows),'utf8')
    try:
        helper=Path(__file__).with_name('reader_code_sandbox.py')
        if os.name=='nt':
            linux=lambda p:'/mnt/'+str(p.resolve())[0].lower()+str(p.resolve())[2:].replace('\\','/')
            args=['wsl','-d','Ubuntu','--','/usr/bin/python3',linux(helper),os.environ.get('MANUAL_READER_BWRAP','/usr/bin/bwrap'),linux(job)]
        else:args=[sys.executable,str(helper),os.environ.get('MANUAL_READER_BWRAP','/usr/bin/bwrap'),str(job)]
        process=subprocess.run(args,capture_output=True,text=True,encoding='utf8',timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if process.returncode:raise RuntimeError(process.stderr[-1200:])
        receipt=json.loads(process.stdout)
    except Exception as error:
        return {'correct':False,'status':'environment_error','passed':0,'total':total,'correction':'隔离编译环境暂不可用，本次不计作答错；请维护环境后重试：'+str(error),'grading_method':'iverilog_sandbox','seconds':round(time.perf_counter()-began,3),'run_id':identity}
    if receipt['compile_returncode']:
        result={'correct':False,'status':'compile_error','passed':0,'total':total,'correction':'编译器报告：\n'+receipt['compile_stderr'][:1800]}
    elif receipt['simulation_returncode']:
        result={'correct':False,'status':'runtime_error','passed':0,'total':total,'correction':'模拟没有正常结束：\n'+receipt['simulation_stderr'][:1800]}
    else:
        actual={}
        for line in receipt['stdout'].splitlines():
            if line.startswith('ROW|'):
                pieces=line.split('|');actual[int(pieces[1])]=dict(zip(OUTPUTS,[int(v)if re.fullmatch(r'\d+',v)else v for v in pieces[2:]]))
        failures=[{**row,'actual':actual.get(row['id'])}for row in rows if actual.get(row['id'])!=row['expected']];passed=total-len(failures)
        result={'correct':not failures,'status':'passed'if not failures else'test_failed','passed':passed,'total':total,'first_failure':failures[0]if failures else None}
        if failures:
            first=failures[0];values=first['inputs'];mismatches=[name+' 应为 '+str(first['expected'][name])+'，实际为 '+str((first['actual']or{}).get(name,'没有输出'))for name in OUTPUTS if first['expected'][name]!=(first['actual']or{}).get(name)]
            result['correction']='这一组输入没有通过：使能='+str(values['csr_enable'])+'，软件请求='+str(values['csr_sw_access'])+'，外部请求='+str(values['ext_access'])+'；'+'；'.join(mismatches)
        else:result['correction']=''
    result.update(grading_method='iverilog_sandbox',run_id=identity,compile_seconds=receipt['compile_seconds'],simulation_seconds=receipt['simulation_seconds'],seconds=round(time.perf_counter()-began,3),sandbox={'network':False,'host_files':'只读工具链与本题工作目录','memory_limit_mb':512,'cpu_limit_seconds':5})
    (job/'receipt.json').write_text(json.dumps({'result':result,'raw_process':receipt},ensure_ascii=False,indent=2),'utf8')
    return result
