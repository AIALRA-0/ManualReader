// 排列只改变窗口位置，保留内容顺序；旧布局继续按已有 columns 恢复。
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../static/workspace-ui.js'),'utf8');
const line=source.split('\n').find(s=>s.startsWith(' function arrangeColumns('));assert(line);
const arrange=vm.runInNewContext(line+';arrangeColumns');
const current=[['explanation'],['translation','original']],before=JSON.stringify(current);
for(const shape of [[1],[2],[1,1],[2,1],[1,2],[1,1,1]]){
 const value=arrange(current,shape);assert.equal(JSON.stringify(value.map(c=>c.length)),JSON.stringify(shape));
 assert.equal(new Set(value.flat()).size,value.flat().length);assert.equal(value[0][0],'explanation');
 assert.equal(JSON.stringify(current),before);value[0][0]='changed';assert.equal(JSON.stringify(current),before);
}
assert.equal(JSON.stringify(arrange([['translation']],[1,1,1])),JSON.stringify([['translation'],['original'],['explanation']]));
assert.equal(JSON.stringify(arrange([['unknown','original'],['original']],[2,1])),JSON.stringify([['original','translation'],['explanation']]));
for(const label of ['单栏','上下排列','左右双栏','左侧上下','右侧上下','三栏并排'])assert(source.includes("'"+label+"'"));
console.log('READING_LAYOUTS_OK');
