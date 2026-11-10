const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../..'),dependencies=process.argv[2]||__dirname;
const locate=name=>require.resolve(name,{paths:[dependencies]});
const esbuild=require(locate('esbuild')),entry=locate('highlight.js');
esbuild.buildSync({stdin:{contents:'self.ReaderHighlighter=require('+JSON.stringify(entry)+')',resolveDir:dependencies},bundle:true,minify:true,platform:'browser',target:'es2020',outfile:path.join(root,'vendor/code-highlight/highlight.min.js'),legalComments:'eof'});
fs.copyFileSync(path.join(path.dirname(entry),'../LICENSE'),path.join(root,'vendor/code-highlight/LICENSE'));
console.log('已重建 highlight.js '+require(locate('highlight.js/package.json')).version+'；全部内置语言在代码线程中按需加载');
