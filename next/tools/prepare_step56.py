"""Copy the frozen acceptance inputs and recover exact registered source text."""

from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from shutil import copy2
from threading import Lock
import json
import sys


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n',encoding='utf8')


def within(left,right):
    return (left['type']==right['type'] and left['start']<=right['end'] and right['start']<=left['end'])


def location_match(position,loc):
    kind=loc['type']
    number=loc.get('number')
    if kind=='pdf_bbox':
        kind='pdf_page'
    return bool(position and position['type']==kind and number is not None
                and position['start']<=number<=position['end'])


def box_matches(references, loc):
    if not loc.get('bbox') or not references:
        return True
    a=loc['bbox']
    for r in references:
        b=r.get('bbox')
        if not b or int(r['page_id'].rsplit(':',1)[-1])!=loc['number']:
            continue
        intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
        small=min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1]))
        if small and intersection/small>=.35:
            return True
    return False


def main(step04, output):
    output.mkdir(parents=True,exist_ok=True)
    (output/'inputs').mkdir(exist_ok=True)
    (output/'media').mkdir(exist_ok=True)
    info={row['project_id']:row for row in read(step04/'isolated-data/import-report.json').values()}
    media_lock=Lock()

    def copy_media(path):
        target=output/'media'/path.name
        with media_lock:
            if not target.exists():
                copy2(path,target)

    @lru_cache
    def indexes(project):
        packet=info[project]
        root=step04/'isolated-data/projects'/project
        locs=read(root/'source-index.json')
        snapshots=[(Path(f),h) for f,h in packet['preserved'].items() if Path(f).name=='reading-workspace.json']
        if len(snapshots)!=1 or digest(snapshots[0][0])!=snapshots[0][1]:
            raise ValueError('无法核对原项目保存的材料快照：'+project)
        snapshot=read(snapshots[0][0])
        files={}
        for f in snapshot['files']:
            fid=f['id']
            if fid not in locs:
                continue
            entry=packet['files'][fid]
            if digest(root/'originals'/entry['relative_path'])!=locs[fid]['sha256'] or entry['sha256']!=locs[fid]['sha256']:
                raise ValueError('原件 SHA-256 与登记的来源版本不一致：'+fid)
            files[fid]=({r['id']:r for r in locs[fid]['rows']},
                        {p['id']:p for p in f['document']['passages']},entry)
        return files,snapshots[0][1]

    def prepare(folder):
        run=read(folder/'run.json')
        assert run['status']=='complete'
        card=read(folder/'task-card.json')
        assert digest(folder/'task-card.json')==run['task_card_sha256']
        draft=folder/run['output_file']
        assert digest(draft)==run['output_hash']
        packet=read(folder/'writer-input.json')
        docs={doc['file_id']:doc for doc in packet['original_documents']}
        files,witness=indexes(run['project_id'])
        originals=packet['original_images']
        protected=step04/'service-runs'/run['task_id']/'assets'
        asset_rows={}
        for asset in originals:
            h=asset['sha256']
            f=protected/(h+'.png')
            assert digest(f)==h
            copy_media(f)
            asset_rows[asset['asset_id']]=asset
        for ref in run.get('resource_references',[]):
            if ref.get('available') and ref.get('media_path'):
                f=protected/Path(ref['media_path']).name
                assert digest(f)==f.stem
                copy_media(f)
        all_sources=[]
        for entry in card['material_plan']:
            fid=entry['file_id']
            registered,parsed,file=files[fid]
            doc=docs[fid]
            assert file['sha256']==doc['sha256']==run['source_file_shas'][fid]
            for sid in entry['source_ids']:
                row=registered[sid]
                original=parsed[sid]
                pos=row['position']
                assert sid in doc['source_locations']
                assert any(within(p,span) or span['type']=='whole_file' for p in doc['source_locations'][sid]
                           for span in entry['source_ranges'])
                selected=[x for x in doc['original'] if pos and x['n']>=pos['start'] and x['n']<=pos['end']]
                if pos and pos['type']=='subtitle_sid':
                    assert selected
                    text='\n'.join(r['text'] for r in selected)
                else:
                    text=original.get('raw_text') or original.get('en') or original.get('alt') or ''
                refs=original.get('refs') or original.get('source_location',{}).get('refs') or []
                asset_ids=[a['asset_id'] for a in originals if a['file_id']==fid and
                           location_match(pos,a['locator']) and box_matches(refs,a['locator'])]
                all_sources.append({'source_id':sid,'file_id':fid,'position':pos,'time':row['time'],
                    'kind':original.get('kind'),'role':original.get('role'),
                    'section_id':original.get('section_id'),'registered_unit_ids':original.get('source_ids',[]),
                    'text':text,'refs':refs,'asset_refs':asset_ids})
            for structure in doc['structures']:
                asset_rows.setdefault(structure['asset_id'],{**structure,'file_id':fid})
        assert len({r['source_id'] for r in all_sources})==run['source_id_references']
        target=output/'inputs'/folder.name
        target.mkdir(exist_ok=True)
        copy2(folder/'run.json',target/'step04-run.json')
        copy2(folder/'task-card.json',target/'task-card.json')
        copy2(draft,target/draft.name)
        write(target/'source-index.json', {'sources':all_sources,'assets':asset_rows,'snapshot_sha256':witness,
              'saved_chapters':[{k:v for k,v in row.items() if k!='blocks'} for row in packet['saved_explanations']],
              'source_files':run['source_file_shas']})
        return {'sample_id':folder.name,'project_id':run['project_id'],'mode':run['mode'],
                'sources':len(all_sources),'exact_text_sources':sum(bool(r['text']) for r in all_sources),
                'media_sources':sum(bool(r['asset_refs']) for r in all_sources),'body_sha256':run['output_hash']}

    with ThreadPoolExecutor(max_workers=12) as pool:
        rows=list(pool.map(prepare, sorted((step04/'delivery/runs').iterdir())))
    assert len(rows)==40
    media=output/'media'
    for path in media.glob('*.png'):
        assert digest(path)==path.stem
    write(output/'preflight.json',rows)
    print(len(rows),sum(r['sources'] for r in rows),sum(r['exact_text_sources'] for r in rows),len(list(media.iterdir())))


if __name__=='__main__':
    main(Path(sys.argv[1]),Path(sys.argv[2]))
