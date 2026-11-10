// SVG 的选择器和片段链接重新编号，颜色与相似编号保持原样。
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict');
const source=fs.readFileSync(path.join(__dirname,'../static/reader-media-view.js'),'utf8');
const code=source.split('\n').filter(l=>l.startsWith(' const escapePattern=')||l.startsWith(' function mediaSelectorMap(')||l.startsWith(' function remapMediaURLs(')).join('\n');
const api=vm.runInNewContext(code+';({mediaSelectorMap,remapMediaURLs})',{CSS:{escape:s=>s.replace(/[.:]/g,c=>'\\'+c)}});
const ids=new Map([['graph-1','media-graph-1'],['arrow','media-arrow'],['a.b','media-a.b']]);
assert.equal(api.mediaSelectorMap(ids)('#graph-1 .label, #graph-10 text, #graph-1:hover, #a\\.b'),'#media-graph-1 .label, #graph-10 text, #media-graph-1:hover, #media-a\\.b');
for(const value of ['url(#arrow)','url("#arrow")',"url('#arrow')",'url( #arrow )'])assert(api.remapMediaURLs(value,ids).includes('#media-arrow'));
for(const value of ['#fff','rgb(255,255,255)','url(#arrow-other)','url(https://example.invalid/#arrow)'])assert.equal(api.remapMediaURLs(value,ids),value);
assert(source.includes('remapMediaStyles(copy,map)'));assert(source.includes("a.name==='aria-labelledby'"));
console.log('MEDIA_REFERENCES_OK');
