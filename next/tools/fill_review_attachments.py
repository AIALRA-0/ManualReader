"""Fill missing Reader attachments without asking the Writer to regenerate a chapter."""

from argparse import ArgumentParser
import asyncio
from pathlib import Path
import sys
from time import perf_counter, time

from manualreader.stages.finish.formatting import format_artifact
from manualreader.stages.finish.provider import Provider
from manualreader.artifacts import read_json as read, write_json as write
from manualreader.steps import step_13_terminology as terminology
from manualreader.steps import step_14_practice as practice
from manualreader.steps import step_15_chapter_guidance as guidance


async def run(cold, output):
    began = perf_counter()
    provider = Provider(output)
    published = read(cold/'reader-projections.json')['samples']
    jobs = []
    for view in published:
        name = view['sample_id']
        folder = cold/'jobs'/name
        run = read(folder/'run.json')
        missing = []
        if not view['quizzes']:
            missing.append('questions')
        if not view['terms']:
            missing.append('terms')
        if not view['recaps'] and run['mode'] != 'ANSWER':
            missing.append('guidance')
        if missing:
            jobs.append((name, folder, run, missing))

    async def fill(name, folder, receipt, missing):
        card = read(cold/'inputs'/name/'task-card.json')
        structure = read(folder/'part-blocks.json')
        ask = provider.scope(name)
        async def term_ask(stage, payload):
            return await ask(stage, {**payload, 'review_request':
                      '从正文中选三到五个真正出现的核心词，词库已读取。使用正文里的确切词形，逐个写清定义和直观解释。'})
        async def terms():
            result = await terminology.generate(cold, name, card, term_ask)
            return {**result, 'terms': terminology.bind(result, structure)}
        factories = {
            'questions': lambda: practice.practice(cold, name, structure, card, ask),
            'terms': terms,
            'guidance': lambda: guidance.guidance(cold, name, structure, card, ask)
        }
        filenames = {'questions': 'quiz-candidates.json', 'terms': 'term-candidates.json',
                     'guidance': 'chapter-guidance.json'}
        stages = {'terms': 'F2', 'questions': 'F3', 'guidance': 'F1'}
        before = (folder/'body.final.md').read_bytes()
        outcomes = await asyncio.gather(*(factories[k]() for k in missing), return_exceptions=True)
        attachments = read(folder/'attachments.final.json')
        patches = read(folder/'format-patches.json')
        changed = {}
        report = {}
        for key, result in zip(missing, outcomes, strict=True):
            if isinstance(result, Exception):
                report[key] = {'error': str(result)}
                continue
            raw = result.get(key)
            if not raw or (key == 'guidance' and not raw.get('part_recaps') and not raw.get('chapter_synthesis')):
                report[key] = {'saved': 0, 'issues': result.get('issues', [])}
                continue
            value, edits = await format_artifact(raw, ('attachments', key))
            attachments[key] = value
            patches = [p for p in patches if p['path'][:2] != ['attachments', key]] + edits
            changed[filenames[key]] = value
            receipt['issues'][stages[key]] = result.get('issues', [])
            if key == 'guidance':
                receipt['recaps'] = len(value.get('part_recaps', []))
                receipt['reading_route_items'] = len(value.get('reading_flow', []))
                count = receipt['recaps']
            else:
                count = len(value)
                receipt['exercises' if key == 'questions' else 'terms'] = count
            report[key] = {'saved': count, 'issues': result.get('issues', [])}
        if changed:
            if (folder/'body.final.md').read_bytes() != before:
                raise ValueError(f'{name} 的成稿在补附件期间发生变化')
            for filename, value in changed.items():
                write(folder/filename, value)
            write(folder/'attachments.final.json', attachments)
            write(folder/'format-patches.json', patches)
            write(folder/'run.json', receipt)
            write(folder/'preview-revision.json', {'kind': 'reader-review-attachments', 'updated_at': time()})
        print(name, report, flush=True)
        return name, report

    rows = await asyncio.gather(*(fill(*job) for job in jobs))
    write(output/'attachment-results.json', {'samples': dict(rows), 'wall_seconds': perf_counter()-began,
          'model_requests_by_stage': {stage: sum(r['stage']==stage for r in provider.log)
                                      for stage in ('F1','F2','F3')}})


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('cold', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('legacy', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.legacy))
    asyncio.run(run(args.cold, args.output))
