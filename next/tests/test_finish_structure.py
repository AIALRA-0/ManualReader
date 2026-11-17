import asyncio
from hashlib import sha256
import json

from manualreader.stages.finish.formatting import finalize, type_patches
from manualreader.stages.finish.structure import build_structure, reading_groups
from manualreader.stages.step07.fusion import invert
from manualreader.stages.step07.resources import scan_batch


def fixture(root, body, assets):
    base = root / 'upstream07' / 's'
    base.mkdir(parents=True)
    inputs = root / 'inputs' / 's'
    inputs.mkdir(parents=True)
    hash_body = sha256(body.encode()).hexdigest()
    base.joinpath('body.fused.md').write_bytes(body.encode())
    data = {
        'run.json': {'task_id': 'task', 'mode': 'NORMAL', 'fixture_based': True, 'fused_sha256': hash_body},
        'anchor-remap.json': {
            'fused_sha256': hash_body,
            'anchors': {'a': {'surface': 'body', 'path': 'body.fused.md', 'start': 0, 'end': len(body)}},
            'mapping': {
                'source_navigation': {'total': 1},
                'links': [{'source_id': 'source', 'relation_type': 'navigation_only', 'body_anchor_id': 'a'}],
            },
            'goal_question_coverage': {'goal_items': [{'target_ids': ['a']}]},
        },
        'resources.fused.json': {'fused_sha256': hash_body, 'items': assets},
        'deferred.json': {},
    }
    for name, row in data.items():
        base.joinpath(name).write_text(json.dumps(row, ensure_ascii=False), encoding='utf-8')
    inputs.joinpath('task-card.json').write_text('{"chapter": {"title":"测试"}}', encoding='utf-8')
    return base


def test_translated_text_only_and_protected_blocks(tmp_path):
    formula = r'\(x+2\)'
    body = f'## 标题\n\n看IPC值。使用{formula}计算。\n\n> “原话。不做调整；”'
    start = body.index(formula)
    fixture(tmp_path, body, [{
        'resource_id': 'math', 'surface': 'body', 'path': 'body.fused.md', 'origin': 'C3', 'kind': 'formula_inline',
        'start': start, 'end': start + len(formula), 'raw_content': formula,
    }])
    structure = build_structure(tmp_path, 's')
    assert structure['source_edges'] and {e['relation_type'] for e in structure['source_edges']} == {'navigation_only'}
    assert structure['coverage']['goal_coverage']['covered'] == 1
    assert len(structure['anchor_lineage']['a']) == 3
    attachments = {'terms': [{'name': 'IPC', 'definition': '量IPC。'}], 'components': [{'raw': '原符号。', 'meaning': '看2种。'}]}
    translations = {'groups': [{'segments': [
        {'relation': 'translated', 'text': '保留2个。'},
        {'relation': 'identity', 'text': '原句不动。'},
    ]}]}
    result = asyncio.run(finalize(tmp_path, 's', structure, attachments, translations))
    assert '看 IPC 值；使用' in result['body']
    assert '“原话。不做调整；”' in result['body']
    assert result['attachments']['terms'][0]['definition'] == '量 IPC'
    assert result['attachments']['components'][0] == {'raw': '原符号。', 'meaning': '看 2 种'}
    assert result['translations']['groups'][0]['segments'] == [
        {'relation': 'translated', 'text': '保留 2 个'}, {'relation': 'identity', 'text': '原句不动。'},
    ]
    final_r = result['structure']['resources'][0]
    assert result['body'][final_r['start']:final_r['end']] == formula
    assert final_r['block_id'] == structure['resources'][0]['block_id']
    assert not type_patches(result['body'], scan_batch([result['body']])[0])
    undone = result['body']
    for layer in reversed(result['offset_map']['body']['phases']):
        undone = invert(undone, layer['operations'])
    assert undone == body


def test_lists_emitted_from_literal_spans(tmp_path):
    text = '对比两种方案：引入 Arm，要核对两种 ISA；扩展推理，要考虑带宽'
    fixture(tmp_path, text, [])

    async def ask(stage, payload, *, images=None):
        assert stage == 'C1' and images is None
        row = payload['candidates'][0]
        assert row['original'] == text
        return {'decisions': [{'candidate_id': row['candidate_id'], 'action': 'list',
                               'item_spans': row['possible_item_spans']}]}

    structure = build_structure(tmp_path, 's')
    result = asyncio.run(finalize(tmp_path, 's', structure, {}, {}, ask))
    assert result['body'] == '对比两种方案：\n\n- 引入 Arm，要核对两种 ISA；\n- 扩展推理，要考虑带宽'
    assert result['structure']['blocks'][0]['block_id'] == structure['blocks'][0]['block_id']
    assert not type_patches(result['body'], scan_batch([result['body']])[0])


def test_invalid_model_spans_do_not_drop_readable_content(tmp_path):
    body = '先看定义：初始状态要验证；结束状态要记录'
    fixture(tmp_path, body, [])

    async def ask(stage, payload, *, images=None):
        return {'decisions': [{'candidate_id': payload['candidates'][0]['candidate_id'], 'action': 'list',
                               'item_spans': [{'start': 7, 'end': 8}, {'start': 9, 'end': 12}]}]}

    structure = build_structure(tmp_path, 's')
    result = asyncio.run(finalize(tmp_path, 's', structure, {}, {}, ask))
    assert result['body'] == body


def test_reading_group_never_crosses_file_or_page():
    def row(n, page, fid='f'):
        return {'source_id': f's{n}', 'file_id': fid, 'kind': 'paragraph', 'text': '- 子项',
                'position': {'type': 'pdf_page', 'start': page, 'end': page},
                'refs': [{'page_id': str(page)}]}

    groups = reading_groups([row(1, 1), row(2, 1), row(3, 2), row(4, 2, 'g')])
    assert [group['source_ids'] for group in groups] == [['s1', 's2'], ['s3'], ['s4']]
