"""比较两个版本的章节计数，使用同一组合成数据，不读取个人材料。"""
import argparse,importlib.util,json,platform,statistics,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def benchmark(baseline):
    before=module('baseline_features',Path(baseline).resolve()/'reader_features.py');after=module('candidate_features',ROOT/'reader_features.py');rows=[]
    for size,chapters in [(600,60),(6000,600)]:
        material={'passages':[{'id':'p'+str(i),'en':'This is a synthetic paragraph.','heading':i%10==0}for i in range(size)],'outline':[{'id':'chapter-'+str(i),'passage_id':'p'+str(i*10),'level':1 if i%6==0 else 2}for i in range(chapters)]}
        state={'read_passages':{'p'+str(i):True for i in range(0,size,3)}};results={};outputs=[]
        for label,implementation in [('before',before),('after',after)]:
            times=[]
            for _ in range(9):
                began=time.perf_counter();result=implementation.reading_progress(material,state);times.append((time.perf_counter()-began)*1000)
            results[label]={'median_ms':round(statistics.median(times),3),'n':len(times)};outputs.append(result)
        if outputs[0]!=outputs[1]:raise AssertionError('两个版本的进度结果不一致')
        rows.append({'passages':size,'chapters':chapters,'results':results,'identical_output':True})
    return {'python':platform.python_version(),'platform':platform.system(),'cpu':platform.processor(),'benchmarks':rows}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--baseline',required=True);args=parser.parse_args()
    print(json.dumps(benchmark(args.baseline),ensure_ascii=False,indent=2))
