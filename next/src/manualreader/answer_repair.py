"""Reduce native answer repair input when the validator pinpoints the faulty field."""

from copy import deepcopy
import json
import re
from time import strftime, gmtime

from .artifacts import write_json


QUOTE_ERROR = re.compile(r'plan 引用没有对应到输入原句：[A-Za-z0-9_-]+')
TERM_ERROR = re.compile(r'[A-Za-z0-9_-]+/terms 缺少「([^」]+)」的登记或关联')


def compact_text(text):
    return re.sub(r'\s+', '', text)


def repair_plan_inputs(draft, task):
    from reader_generation import answer_evidence_sources, source_texts

    sources = {row['id']: [text for text in source_texts(row) if text]
               for row in answer_evidence_sources(task)}
    compact = {sid: [compact_text(text) for text in texts] for sid, texts in sources.items()}
    patched = deepcopy(draft)
    targets = {}
    changes = []
    for unit_index, unit in enumerate(patched['plan']['units']):
        for source_index, reference in enumerate(unit['source_refs']):
            quote = compact_text(reference['quote'])
            if not quote or any(quote in text for text in compact.get(reference['source_id'], [])):
                continue
            path = f'/plan/units/{unit_index}/source_refs/{source_index}'
            matches = [sid for sid, texts in compact.items() if any(quote in text for text in texts)]
            if len(matches) == 1:
                reference['source_id'] = matches[0]
                changes.append(path)
            else:
                targets[path] = {'current': reference, 'unit': unit['say']}
    if not changes and not targets:
        return None
    return patched, targets, sources, changes


def targeted_supplement(executor):
    """Wrap the existing isolated worker. Its full validator still checks the merged answer."""
    from reader_writer_patch import apply
    from reader_writer_schema import S, arr, enum, obj

    fallback = executor.supplemental_patch

    def supplement(folder, draft, task, issues, images, executable):
        lines = [line for value in (issues.values() if isinstance(issues, dict) else issues)
                 for line in str(value).splitlines() if line.strip()]
        if not lines:
            return fallback(folder, draft, task, issues, images, executable)
        origin = folder / 'supplement'
        origin.mkdir(exist_ok=True)

        if all(QUOTE_ERROR.fullmatch(line) for line in lines):
            prepared = repair_plan_inputs(draft, task)
            if prepared is None:
                return fallback(folder, draft, task, issues, images, executable)
            base, targets, sources, changes = prepared
            if not targets:
                usage = {'usage': {}, 'seconds': 0, 'http_requests': 0, 'usage_complete': True,
                         'model_completed_at': strftime('%Y-%m-%dT%H:%M:%SZ', gmtime())}
                write_json(origin/'verified-local-changes.json', changes)
                return base, usage, None
            payload = {'invalid_references': targets,
                       'original_sources': [{'source_id': sid, 'original': texts} for sid, texts in sources.items()]}
            fields = obj(path=enum(targets), value=obj(source_id=enum(sources), quote=S))
            instructions = (
                '只修复 invalid_references 中的引用。对每个项选一个相关的输入来源，'
                '从该来源准确摘录连续原句，并保留真实 source_id。每个 path 返回一次。'
                '保持计划正文、答案、练习和术语的原样。'
            )
            allowed = set(targets)
        elif all(TERM_ERROR.fullmatch(line) for line in lines):
            names = sorted({TERM_ERROR.fullmatch(line)[1] for line in lines})
            terms = draft.get('terms', [])
            # Names and aliases are the only fields this path can change. If a concept was
            # never written, the full native repair still has the complete answer context.
            if not terms:
                return fallback(folder, draft, task, issues, images, executable)
            allowed = {f'/terms/{index}/{field}' for index in range(len(terms)) for field in ('name', 'aliases')}
            excerpts = []
            for answer in draft.get('answers', []):
                for part in answer.get('parts', []):
                    excerpts.extend(line for line in part.get('markdown', '').splitlines()
                                    if any(name in line for name in names))
            if not excerpts:
                return fallback(folder, draft, task, issues, images, executable)
            payload = {'missing_terms': names, 'answer_excerpts': excerpts, 'already_written_terms': terms}
            fields = {'anyOf': [
                obj(path=enum(sorted(k for k in allowed if k.endswith('/name'))), value=S),
                obj(path=enum(sorted(k for k in allowed if k.endswith('/aliases'))), value=arr(S)),
            ]}
            instructions = (
                '只核对已经写好的词条和回答实际用词。只有它们表示同一概念时，才可调整名称或别名来连接。'
                '保留中英文名称合同、两段解释和已登记编号。'
                '若现有词条里没有对应概念，返回空 changes 数组，由原写作流程处理。'
            )
            base = draft
        else:
            return fallback(folder, draft, task, issues, images, executable)

        # Store a scoped attempt separately so its usage and any fallback remain inspectable.
        attempt = origin / 'focused'
        attempt.mkdir(exist_ok=True)
        write_json(attempt/'output-schema.json', obj(changes=arr(fields)))
        write_json(attempt/'repair-input.json', payload)
        raw, usage, session = executor.invoke_writer(
            attempt, json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
            [], executable, system_override=instructions, allow_image_generation=False,
            research=False, request_limit=1)
        try:
            result = executor.parse_draft(raw)
            changes = result['changes']
            paths = {row['path'] for row in changes}
            if not paths or not paths <= allowed:
                raise ValueError('定点补修没有返回对应字段')
            if 'invalid_references' in payload:
                if paths != allowed:
                    raise ValueError('定点补修缺少需要核对的引用')
                for row in changes:
                    reference = row['value']
                    quote = compact_text(reference['quote'])
                    if not quote or not any(quote in compact_text(text) for text in sources[reference['source_id']]):
                        raise ValueError('补修引用与原文不符')
            amended = apply(base, result)
            if 'missing_terms' in payload:
                from reader_term_quality import cover_planned_terms
                if cover_planned_terms(deepcopy(amended),task['reader_analysis'],task.get('registered_terms',[])):
                    raise ValueError('定点补修未补齐原生术语检查')
        except (ValueError, KeyError, TypeError, IndexError):
            merged, extra, fallback_session = fallback(folder, draft, task, issues, images, executable)
            a = executor.combine_supplement(usage, extra, fallback_session)
            return merged, a, fallback_session
        write_json(attempt/'merged.json', amended)
        return amended, usage, session

    return supplement
