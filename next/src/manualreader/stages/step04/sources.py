"""Read exactly the originals authorized by the frozen task and round."""

from base64 import b64decode
from hashlib import sha256
from pathlib import PurePosixPath

from ..step02.card import CARD_KEYS, POLICY_REFS, policies
from ..step02.original import original
from ..step03.engine import flat, matches, overlaps, source_pool
from ..step03.media import message
from ..step01.scan import inside


class SourceUnavailable(ValueError):
    pass


def context_for(project, round_id, mode, card, confirm_finale, revise):
    state = project.locate_round(round_id)
    if state['mode'] != mode or (state.get('m2') or {}).get('status') != 'ready':
        raise ValueError('任务模式或已核对的原件轮次不匹配')
    if mode == 'ANSWER':
        keys = ('question', 'context', 'question_styles', 'goals', 'material_plan',
                'policy_refs', 'custom_instruction')
        question_id = card['question']['id']
        if card['question_styles']:
            raise ValueError('独立回答不能继承无关的历史问法')
    else:
        keys = CARD_KEYS
        question_id = None
    if set(card) != set(keys) or card['policy_refs'] != POLICY_REFS:
        raise ValueError('任务卡结构或冻结规则版本不匹配')
    context = project.context({
        'round_id': round_id, 'question_id': question_id,
        'explicit_finale': confirm_finale, 'revise_existing_answer': revise,
        'custom_instruction': card['custom_instruction'],
    })
    fixed = context['fixed']
    fields = ('question', 'context') if mode == 'ANSWER' else ('chapter', 'versions', 'previous', 'progress')
    if any(card[field] != fixed[field] for field in fields):
        raise ValueError('任务卡身份、前文或上下文已经变化')
    if mode != 'ANSWER' and any(card['project'][key] != fixed['project'][key] for key in ('id', 'name')):
        raise ValueError('任务卡项目身份不匹配')
    return state, context


def read_file(engine, project, round_id, state, item, mode):
    file_id = item['file_id']
    if file_id not in state['selected']:
        raise ValueError('原件没有登记在本轮实际选取的文件中')
    _, entry = engine.entry(project, round_id, file_id)
    if item['relative_path'] != entry['relative_path']:
        raise ValueError('任务卡原件路径与已登记版本不一致')
    spans = item['source_ranges']
    pool = {row['id']: row for row in source_pool(project, entry)}
    locators = {}
    for sid in item['source_ids']:
        positions = [point for key in ('position', 'time') if (point := pool.get(sid, {}).get(key))]
        if not any(overlaps(point, span) for point in positions for span in spans):
            raise ValueError(f'原文编号不在本轮任务卡范围内：{sid}')
        locators[sid] = positions
    _, (kind, rows) = original(inside(project.root, 'originals'), entry)
    scope = [row for row in sorted(rows, key=lambda row: row['n'])
             if any(matches({'type':kind, 'number':row['n'], 'time':row.get('time')}, span)
                    for span in spans)]
    if not scope:
        raise ValueError('原件没有任务卡指定的内容')
    # The introduction can use the entire authorized file as context.
    supplied = sorted(rows, key=lambda row: row['n']) if mode == 'INTRO' else scope
    selected = {row['n'] for row in supplied}
    if mode == 'NORMAL':
        boundary = {n for span in spans if span['type'] == kind
                    for n in (span['start']-1, span['end']+1)} - selected
        nearby = [row for row in rows if row['n'] in boundary]
    else:
        nearby = []
    pages = sorted(selected)
    windows = ([pages[i:i+24] for i in range(0, len(pages), 24)]
               if PurePosixPath(entry['relative_path']).suffix.lower() == '.pdf' else [None])
    cache = []
    visual = []
    structures = []
    exceptions = []
    for page_set in windows:
        doc = engine.inspect(project, round_id, file_id, page_set)
        if doc['content_sha256'] != entry['content_sha256']:
            raise ValueError('原件解析结果与本轮文件版本不一致')
        cache.append(doc['cache'])
        page_images = {p['number']: next((a for a in p['assets'] if a['kind']=='page_visual'
                        and 'inspect_visual' in a['capabilities']), None) for p in doc['pages']}
        use_full_pages = mode=='INTRO' and len(pages)>24
        with_visuals = set()
        for asset in flat(doc):
            point = asset['locator']
            if kind != 'pdf_page' and not (
                any(matches(point, span) for span in spans)
                or mode == 'INTRO' and matches(point, {'type':'whole_file'})
            ):
                continue
            if asset['status'] == 'unavailable':
                exceptions.append({'asset_id':asset['asset_id'], 'locator':point, 'reason':asset['reason']})
            allowed = asset['capabilities']
            if 'compute_structure' in allowed:
                structures.append({'asset_id':asset['asset_id'], 'locator':point, 'kind':asset['kind'],
                                   'structure':asset['structure']})
            if 'inspect_visual' in allowed:
                if asset['kind'] != 'page_visual' or not any(
                    row['n'] == point.get('number') and row['text'].strip() for row in supplied
                ):
                    page_image = page_images.get(point.get('number')) if use_full_pages else None
                    if page_image:
                        if point['number'] not in with_visuals:
                            visual.append(page_image)
                            with_visuals.add(point['number'])
                    else:
                        visual.append(asset)
    return {
        'file_id':file_id, 'relative_path':entry['relative_path'], 'sha256':entry['content_sha256'],
        'file_revision':entry['file_revision'], 'source_ranges':spans, 'source_locations':locators,
        'type':kind, 'original':supplied, 'boundary_context':nearby, 'structures':structures,
        'unavailable_assets':exceptions, 'local_cache':cache,
    }, visual


def assemble(engine, project, round_id, mode, card, state, context):
    documents = []
    images = {}
    image_log = []
    for item in card['material_plan']:
        try:
            doc, visuals = read_file(engine, project, round_id, state, item, mode)
        except (ValueError, OSError, RuntimeError) as error:
            raise SourceUnavailable(f'{item["file_id"]}：{error}') from error
        documents.append(doc)
        for asset in visuals:
            point = asset['locator']
            expected = asset['carrier']['sha256']
            try:
                if expected not in images:
                    try:
                        pixels = message(engine.cache, asset, 'inspect_visual')
                    except (ValueError, OSError):
                        pixels = engine.consume(project, round_id, doc['file_id'], asset['asset_id'],
                                                'inspect_visual',
                                                point['number'] if doc['type']=='pdf_page' else None)['content']
                    raw = b64decode(pixels['image_url'].split(',', 1)[1], validate=True)
                    if sha256(raw).hexdigest() != expected:
                        raise ValueError('原图像素与 STEP-03 登记哈希不一致')
                    images[expected] = raw
                image_log.append({'file_id':doc['file_id'], 'asset_id':asset['asset_id'],
                                  'locator':point, 'kind':asset['kind'], 'sha256':expected,
                                  'media_path':f'assets/{expected}.png'})
            except (ValueError, OSError, RuntimeError) as error:
                doc['unavailable_assets'].append({'asset_id':asset['asset_id'], 'locator':point,
                                                  'reason':str(error)})
        if not any(row['text'].strip() for row in doc['original']) and not any(
           img['file_id'] == doc['file_id'] for img in image_log):
            raise SourceUnavailable(f'{doc["file_id"]}：没有可读取的原文或图像')
    payload = {
        'mode': mode, 'task_card': card, 'writing_policies': policies(),
        'saved_explanations': context['saved_explanations'],
        'original_documents': [{k:v for k,v in d.items() if k!='local_cache'} for d in documents],
        'original_images': image_log,
    }
    return payload, documents, images
