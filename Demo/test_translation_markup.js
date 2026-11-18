const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = vm.createContext({window: {}});

for (const file of ['markdown-vendor.js','math-text.js','reader-markdown.js']) {
  const source = fs.readFileSync(path.join(__dirname,'../reader/static',file),'utf8');
  vm.runInContext(source,context,{filename:file});
}

const {tableRows,equationSource} = context.window.ReaderMarkdown;
const plain = tableRows('名称 | 第一列 | 第二列\n读数 | 0 | 32');
assert.equal(plain.header,false);
assert.equal(plain.rows[1].join(','),'读数,0,32');

const protectedCells = tableRows([
  '| 公式 | 代码 | 文字 |',
  '| :--- | ---: | :---: |',
  String.raw`| \(|x|\) | `+'\x60a | b\x60'+String.raw` | a \| b |`,
  '| 2 | | 4 |',
].join('\n'));
assert.equal(protectedCells.header,true);
assert.equal(protectedCells.columns.join(','),'left,right,center');
assert.equal(protectedCells.rows[1].length,3);
assert.equal(protectedCells.rows[1][0],String.raw`\(|x|\)`);
assert.equal(protectedCells.rows[1][1],'\x60a | b\x60');
assert.equal(protectedCells.rows[2][1],'');
assert.equal(tableRows('a | b\ntext'),null);

assert.equal(equationSource('x^2 = 4\n(2)'),String.raw`\[`+'\n'+String.raw`x^2 = 4\tag{2}`+'\n'+String.raw`\]`);
assert.equal(equationSource(String.raw`\[x^2 = 4\]`),String.raw`\[x^2 = 4\]`);
assert.equal(equationSource('公式与正文 $x$'),'公式与正文 $x$');
