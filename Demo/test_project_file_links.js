// 运行与网页相同的链接解析器，确认本地引用只指向当前项目。
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const assert = require('node:assert/strict');
const box = { window: {}, URL, location: { origin: 'https://reader.example' } };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/reader-project-files.js'), 'utf8'), box);
const resolve = (href, basePath = '') => box.window.ReaderProjectFiles.resolveLink(href, { origin: box.location.origin, project: 'demo', basePath });
assert.equal(resolve('/api/project-files/raw?path=src%2Fexample.py').path, 'src/example.py');
assert.equal(resolve('https://reader.example/api/project-files/raw?path=src/a.py&material=demo').path, 'src/a.py');
assert.equal(resolve('code.py', 'examples/notes.md').path, 'examples/code.py');
assert.equal(resolve('../%E4%B8%AD%E6%96%87.md', 'examples/notes.md').path, '中文.md');
assert.equal(resolve('https://other.example/code.py'), null);
assert.equal(resolve('#section'), null);
assert.equal(resolve('mailto:someone@example.invalid'), null);
assert.equal(resolve('file:///private/example.py'), null);
for (const href of ['../outside.txt', '/api/project-files/raw?path=../outside', '/api/project-files/raw?path=%2Fetc%2Fpasswd', '/api/project-files/raw?path=a&material=another']) {
  assert.throws(() => resolve(href));
}
console.log('Project file link checks passed');
