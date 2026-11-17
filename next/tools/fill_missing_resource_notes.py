"""Fill empty resource explanations in an existing isolated run."""

from argparse import ArgumentParser
import asyncio
from collections import Counter
from hashlib import sha256
from pathlib import Path
from time import time

from manualreader.stages.finish.content import _p2_reply, explain_math, explain_visual
from manualreader.stages.finish.formatting import format_artifact
from manualreader.stages.finish.provider import Provider
from manualreader.artifacts import read_json as read, write_json as write


def archived_visual(root):
    for request in (root / 'requests/A2').glob('*/input.json'):
        result = request.with_name('result.json')
        if result.is_file():
            original = read(request)['input']
            response = read(result).get('result') or {}
            yield _p2_reply(response, original.get('semantic_groups', []))[0]


async def main(root, output):
    provider = Provider(output)
    saved_visuals = {
        resource_id: group
        for groups in archived_visual(root)
        for group in groups
        for resource_id in group['resource_ids']
    }
    selected = []
    for note in (root / 'jobs').glob('*/resource-explanations.json'):
        p2 = read(note)
        missing = {r['resource_id'] for r in p2['coverage_by_resource']
                   if r['eligible'] and r['status'] == 'pending' and not r['group_id']}
        if missing:
            selected.append((note.parent, p2, missing))

    async def fill(job, p2, missing):
        name = job.name
        body = sha256((job/'body.final.md').read_bytes()).hexdigest()
        full = read(job/'part-blocks.json')
        from_archive = {saved_visuals[rid]['group_id']: saved_visuals[rid] for rid in missing if rid in saved_visuals}
        reused = {rid for group in from_archive.values() for rid in group['resource_ids']} & missing
        scope = {**full, 'resources': [r for r in full['resources']
                 if r['resource_id'] in missing - reused]}
        ask = provider.scope(name)
        sources = read(root/'inputs'/name/'source-index.json')
        math, visual = await asyncio.gather(explain_math(root, name, scope, ask),
                                            explain_visual(root, name, scope, sources, ask))
        groups = [*from_archive.values(), *math['groups'], *visual['groups']]
        additions = {}
        for group in groups:
            for rid in group['resource_ids']:
                if rid in missing:
                    additions[rid] = {'group_id': group['group_id'], 'status': group['status']}
        notes, edits = await format_artifact(groups, ('attachments', 'resource_explanations', 'groups'))
        if notes:
            if sha256((job/'body.final.md').read_bytes()).hexdigest() != body:
                raise ValueError(f'{name} 的正文在补解读期间发生变化')
            offset = len(p2['groups'])
            for edit in edits:
                edit['path'][3] += offset
            p2['groups'].extend(notes)
            p2['coverage_by_resource'] = [{**r, **additions.get(r['resource_id'], {})}
                                          for r in p2['coverage_by_resource']]
            p2['issues'].extend(math['issues'] + visual['issues'])
            attachments = read(job/'attachments.final.json')
            patches = read(job/'format-patches.json') + edits
            attachments['resource_explanations'] = p2
            receipt = read(job/'run.json')
            receipt['p2'] = dict(Counter(r['status'] for r in p2['coverage_by_resource']))
            receipt['issues']['P2'] = p2['issues']
            write(job/'resource-explanations.json', p2)
            write(job/'attachments.final.json', attachments)
            write(job/'format-patches.json', patches)
            write(job/'run.json', receipt)
            write(job/'preview-revision.json', {'kind': 'filled-resource-notes', 'updated_at': time()})
        row = {'sample_id': name, 'requested': sorted(missing), 'restored_from_receipt': sorted(reused),
               'filled': additions, 'still_empty': sorted(missing - additions.keys()), 'body_sha256': body}
        print(name, len(additions), len(missing), flush=True)
        return row

    rows = await asyncio.gather(*(fill(*args) for args in selected))
    write(output/'resource-notes-results.json', {'samples': rows, 'model_requests': len(provider.log)})


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('cold', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    asyncio.run(main(args.cold,args.output))
