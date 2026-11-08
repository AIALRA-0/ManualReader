// 直接运行网页中的判断，验证输入块隔离、章首提问和原文范围补充。
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = process.argv[2] || path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const workspace = fs.readFileSync(path.join(root, 'static/workspace-ui.js'), 'utf8');
function line(source, name) {
  const value = source.split('\n').find(row => row.startsWith(' function ' + name + '('));
  assert(value, name);
  return value;
}
const eligible = vm.runInNewContext(line(app, 'inlineQuestionEligible') + ';inlineQuestionEligible');
for (const id of ['lesson--opening', 'lesson--opening-question', 'lesson--framework-start-0', 'lesson--framework-start-1']) {
  const passage = { id, progress_exempt: true };
  assert.equal(eligible(passage), true);
  assert.equal(passage.progress_exempt, true);
}
for (const id of ['lesson--route', 'lesson--framework-route', 'lesson--closing']) {
  assert.equal(eligible({ id, progress_exempt: true }), false);
}
assert.equal(eligible({ id: 'body' }), true);
assert.equal(eligible({ id: 'body', heading: true }), false);
assert.equal(eligible({ id: 'body', duplicate_of: 'original' }), false);
assert.equal(eligible({ id: 'body', role: 'page_furniture' }), false);
assert.equal(eligible(null), false);

const registry = vm.runInNewContext('const questionInputRefs=new Map(),questionInputKeys=new WeakMap();\n' +
  line(app, 'registerQuestionInput') + '\n' + line(app, 'questionInputs') + ';({registerQuestionInput,questionInputs})', { WeakRef });
const first = { dataset: { blockId: 'project:a' } };
const second = { dataset: { blockId: 'project:b' } };
const mirror = { dataset: { blockId: 'project:a' } };
registry.registerQuestionInput(first, 'project:a');
registry.registerQuestionInput(first, 'project:a');
registry.registerQuestionInput(second, 'project:b');
registry.registerQuestionInput(mirror, 'project:a');
assert.equal(registry.questionInputs('project:a').length, 2);
assert.equal(registry.questionInputs('project:b').length, 1);
first.dataset.blockId = 'project:c';
registry.registerQuestionInput(first, 'project:c');
assert.equal(registry.questionInputs('project:a').length, 1);
assert.equal(registry.questionInputs('project:c')[0], first);
assert.equal(registry.questionInputs('missing').length, 0);

const passages = Array.from({ length: 10000 }, (_, i) => ({ id: 'p' + i, file_id: 'source', section_id: 'section' }));
const fixture = { ctx: { material: () => ({ passages }) }, activeNode: () => ({ source_range: { passage_ids: ['p1', 'p2'] } }),
  browseOriginal: false, revealedSourceIds: ['p7'], readable: () => true, fileId: 'source', sourceLinks: () => [], sourcePassage: () => passages[0] };
const sources = vm.runInNewContext(line(workspace, 'sourceItems') + ';sourceItems', fixture);
assert.equal(Array.from(sources(false), p => p.id).join(','), 'p1,p2,p7');
assert.equal(sources(true).length, 10000);
assert.equal(passages.length, 10000);
assert.equal(passages[7].id, 'p7');
console.log('Shared question interaction checks passed');
