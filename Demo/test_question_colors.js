// 直接运行页面使用的状态计算，检查子追问和共享检索关联不会改变主问题。
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const begin = source.indexOf(' function questionPendingStates(){');
const end = source.indexOf(' function paintAnswerState(', begin);
assert(begin >= 0 && end > begin);
function states(questions, quizzes = [], answers = {}) {
  return vm.runInNewContext(source.slice(begin, end) + ';questionPendingStates()', {
    state: { rounds: [{ questions }], inline_practice: { answers } },
    currentQuizzes: () => quizzes
  });
}
const parent = { id: 'parent', answer: '已回答' };
const child = { id: 'child', parent_question_id: 'parent', answer: '已回答' };
const grandchild = { id: 'grandchild', parent_question_id: 'child', answer: null };
let result = states([parent, child, grandchild]);
assert.equal(result.get('parent').questions, false);
assert.equal(result.get('child').questions, false);
assert.equal(result.get('grandchild').questions, true);
const quiz = { id: 'quiz', selected: null, question_ids: ['parent', 'child'], placement: { kind: 'answer', question_id: 'child' } };
result = states([parent, child, grandchild], [quiz]);
assert.equal(result.get('parent').practice, false);
assert.equal(result.get('child').practice, true);
result = states([parent, child], [{ ...quiz, placement: undefined }], { child: { part: ['quiz'] } });
assert.equal(result.get('parent').practice, false);
assert.equal(result.get('child').practice, true);
result = states([parent, child], [{ ...quiz, selected: 0 }]);
assert.equal(result.get('child').practice, false);
result = states([{ ...parent, answer_outdated: true }, child, grandchild]);
assert.equal(result.get('parent').questions, true);
assert.equal(result.get('child').questions, false);
result = states([parent, { ...child, answer: null, needs_answer: false, answer_deleted_at: 'deleted' }]);
assert.equal(result.get('parent').questions, false);
assert.equal(result.get('child').questions, true);
result = states([parent, child], [{ ...quiz, placement: { kind: 'lesson' } }]);
assert.equal(result.get('parent').practice, false);
assert.equal(result.get('child').practice, false);
console.log('Independent question state checks passed');
