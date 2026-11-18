"""Run the existing Next stages for one chapter supplied by a paired Reader."""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import shutil
from time import time

from .artifacts import read_json as read, write_json as write
from .stages.step06.pipeline import JointJobs
from .stages.step07 import inference
from .stages.step07.fusion import assemble
from .stages.finish.pipeline import finish_batch
from .stages.finish.provider import Provider
from .stages.finish.runner import Timings, persist
from .stages.finish.content import build_translation_catalog, translate
from .transport import executable


def prepare(root, packet, card, context):
    sample = 'chapter'
    folder = root / 'inputs' / sample
    folder.mkdir(parents=True, exist_ok=True)
    first = root / 'chapter.md'
    for name in ('task-card.json', 'writing-policies.json', 'chapter.md'):
        shutil.copy2(root / name, folder / name)
    lifecycle = read(root / 'planner' / 'step01-start.json')
    catalog = {row['file_id']: row for row in lifecycle['manifest']['files']}
    selected = {sid for item in card['material_plan'] for sid in item['source_ids']}
    assets, sources = {}, []
    for row in packet['sources']:
        if row['id'] not in selected:
            continue
        refs = []
        for ref in row.get('source_evidence', []):
            path = root / 'source-images' / ('attachment-' + f"{ref['attachment_number']:03d}" + '.png')
            raw = path.read_bytes()
            sha = sha256(raw).hexdigest()
            target = root / 'media' / (sha + '.png')
            target.write_bytes(raw)
            asset_id = 'asset-' + sha[:20]
            assets[asset_id] = {'asset_id': asset_id, 'sha256': sha, 'kind': ref['kind'],
                                'file_id': row['file_id'], 'locator': row.get('position')}
            refs.append(asset_id)
        sources.append({'source_id': row['id'], 'file_id': row['file_id'],
                        'position': row.get('position'), 'time': row.get('time_seconds'),
                        'kind': row.get('kind'), 'role': row.get('role'),
                        'section_id': row.get('section_id') or row['file_id'],
                        'text': row.get('original') or '', 'refs': row.get('refs', []),
                        'asset_refs': refs})
    source = {'sources': sources, 'assets': assets,
              'source_files': {item['file_id']: catalog[item['file_id']]['content_sha256']
                               for item in card['material_plan']},
              'snapshot_sha256': sha256(json.dumps(packet['file_signatures'], sort_keys=True).encode()).hexdigest(),
              'saved_chapters': []}
    references = []
    for row in (root / 'media').glob('*.png'):
        sha = sha256(row.read_bytes()).hexdigest()
        if row.stem != sha:
            target = row.with_name(sha + '.png')
            if not target.exists():
                shutil.copy2(row, target)
            references.append({'path': 'media/' + row.name,
                               'available': True, 'media_path': 'assets/' + sha + '.png'})
    record = {'task_id': 'connected-' + sha256(first.read_bytes()).hexdigest()[:20],
              'project_id': card['project']['id'], 'mode': context['mode'],
              'output_file': 'chapter.md', 'output_hash': sha256(first.read_bytes()).hexdigest(),
              'task_card_sha256': sha256((folder / 'task-card.json').read_bytes()).hexdigest(),
              'fixture_based': False, 'resource_references': references}
    write(folder / 'step04-run.json', record)
    write(folder / 'source-index.json', source)
    write(folder / 'reader-context.json', context)
    for stage in ('joint', 'fusion'):
        destination = root / stage / 'inputs' / sample
        shutil.copytree(folder, destination, dirs_exist_ok=True)
        shutil.copytree(root / 'media', root / stage / 'media', dirs_exist_ok=True)
    return sample, source


def upstream(root, sample):
    jobs = JointJobs(root / 'joint')
    try:
        jobs.submit(sample)
        while True:
            result = jobs.status(sample)
            if result['status'] not in ('queued', 'running'):
                break
            __import__('time').sleep(.2)
        if result['status'] not in ('complete', 'partial'):
            raise RuntimeError(result.get('error') or 'Next 的来源映射没有返回结果')
    finally:
        jobs.close()
    prior = root / 'fusion' / 'previous' / sample
    shutil.copytree(root / 'joint' / 'jobs' / sample, prior, dirs_exist_ok=True)
    payload, _ = inference.prepare(root / 'fusion', sample)
    model = root / 'fusion' / 'jobs' / sample / 'model'
    if not (model/'outcome.json').is_file():
        inference.invoke(model, payload, executable(), time())
    assemble(root / 'fusion', sample)
    shutil.copytree(root / 'fusion' / 'jobs' / sample, root / 'upstream07' / sample, dirs_exist_ok=True)


def run(root, packet, card, context):
    root = Path(root)
    began=time()
    write(root/'connected-run.json',{'project_id':card['project']['id'],'mode':context['mode'],
                                    'started_at':began,'state':'running','source_quality_version':packet.get('source_quality_version')})
    sample, source = prepare(root, packet, card, context)
    async def finish():
        providers, timing = Provider(root, sol=40, ds=12, vision=12), Timings()
        async def translate_sources():
            result=await translate(root,sample,source,providers.scope(sample),build_translation_catalog([source]))
            write(root/'jobs'/sample/'translation-initial.json',result)
        translations = asyncio.create_task(translate_sources())
        try:
            await asyncio.to_thread(upstream, root, sample)
            result = await finish_batch(root, providers, timing, {sample: source})
            if result['failures']:
                raise RuntimeError(str(result['failures']))
            return result
        finally:
            await translations
            persist(root, 'connected', providers, timing)
    try:
        result=asyncio.run(finish())
    except BaseException as error:
        write(root/'connected-run.json',{'project_id':card['project']['id'],'mode':context['mode'],
                                        'started_at':began,'ended_at':time(),'state':'partial','error':str(error)})
        raise
    write(root/'connected-run.json',{'project_id':card['project']['id'],'mode':context['mode'],
                                    'started_at':began,'ended_at':time(),'state':'preview_ready',
                                    'formal_commit':'awaiting_user_confirmation'})
    from .stages.finish.review import next_calls, next_run
    write(root/'metrics/connected-calls.json',next_calls(root,card['project']['id']))
    write(root/'metrics/connected-stages.json',next_run(root,card['project']['id']))
    return result
