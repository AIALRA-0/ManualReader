/* 代码只在本机线程里解析，不会执行，也不会上传。 */
importScripts('./highlight.min.js');
const H=self.ReaderHighlighter;
const aliases={sv:'verilog',systemverilog:'verilog',svh:'verilog',v:'verilog',vhd:'vhdl',py:'python',js:'javascript',ts:'typescript',jsx:'javascript',tsx:'typescript',sh:'bash',zsh:'bash',ps1:'powershell',yml:'yaml',html:'xml',vue:'xml',svelte:'xml',cs:'csharp',cxx:'cpp','c++':'cpp',h:'cpp',rs:'rust',rb:'ruby',kt:'kotlin',m:'objectivec',dockerfile:'dockerfile'};
const extensions={c:'c',h:'cpp',cc:'cpp',cpp:'cpp',cxx:'cpp',hpp:'cpp',hh:'cpp',cu:'cpp',cuh:'cpp',py:'python',pyw:'python',pyi:'python',js:'javascript',mjs:'javascript',cjs:'javascript',jsx:'javascript',ts:'typescript',tsx:'typescript',java:'java',class:'java',kt:'kotlin',kts:'kotlin',scala:'scala',sc:'scala',groovy:'groovy',cs:'csharp',fs:'fsharp',fsx:'fsharp',vb:'vbnet',go:'go',rs:'rust',swift:'swift',m:'objectivec',mm:'objectivec',rb:'ruby',rake:'ruby',php:'php',pl:'perl',pm:'perl',r:'r',rmd:'r',jl:'julia',lua:'lua',ex:'elixir',exs:'elixir',erl:'erlang',hrl:'erlang',hs:'haskell',lhs:'haskell',clj:'clojure',cljs:'clojure',lisp:'lisp',el:'lisp',lsp:'lisp',scm:'scheme',ss:'scheme',ml:'ocaml',mli:'ocaml',nim:'nim',d:'d',dart:'dart',v:'verilog',sv:'verilog',svh:'verilog',vh:'verilog',vhd:'vhdl',vhdl:'vhdl',tcl:'tcl',xdc:'tcl',sdc:'tcl',qsf:'tcl',sh:'bash',bash:'bash',zsh:'bash',fish:'shell',ps1:'powershell',psm1:'powershell',bat:'dos',cmd:'dos',sql:'sql',graphql:'graphql',gql:'graphql',json:'json',jsonc:'json',jsonl:'json',yaml:'yaml',yml:'yaml',toml:'ini',ini:'ini',cfg:'ini',conf:'ini',properties:'properties',xml:'xml',html:'xml',htm:'xml',xhtml:'xml',svg:'xml',vue:'xml',svelte:'xml',css:'css',scss:'scss',sass:'scss',less:'less',md:'markdown',markdown:'markdown',rst:'plaintext',tex:'latex',sty:'latex',cls:'latex',bib:'bibtex',cmake:'cmake',mk:'makefile',mak:'makefile',dockerfile:'dockerfile',diff:'diff',patch:'diff',s:'x86asm',asm:'x86asm',wat:'wasm',proto:'protobuf',thrift:'thrift',gradle:'gradle',g4:'antlr',f:'fortran',f90:'fortran',f95:'fortran',f03:'fortran',pas:'delphi',pp:'puppet',ps:'postscript',eps:'postscript',st:'smalltalk',sol:'solidity',avdl:'avro',tf:'hcl',tfvars:'hcl',log:'plaintext',txt:'plaintext',csv:'plaintext',tsv:'plaintext'};
const filenames={'makefile':'makefile','gnumakefile':'makefile','dockerfile':'dockerfile','containerfile':'dockerfile','cmakelists.txt':'cmake','jenkinsfile':'groovy','gemfile':'ruby','rakefile':'ruby','.bashrc':'bash','.bash_profile':'bash','.zshrc':'bash','.profile':'bash','.gitignore':'plaintext','.gitattributes':'plaintext','.env':'ini','.npmrc':'ini','.editorconfig':'ini'};
function canonical(value){const name=String(value||'').trim().toLowerCase().replace(/^(?:language-|lang-)/,'');return /^(?:text|txt|plain|plaintext|nohighlight|none)$/.test(name)?'plaintext':aliases[name]||name}
function choose(text,options){
 const explicit=canonical(options.language);if(explicit==='plaintext'||H.getLanguage(explicit))return explicit;
 const name=String(options.path||'').split(/[\\/]/).at(-1).split(/[?#]/)[0].toLowerCase(),extension=name.includes('.')?name.split('.').at(-1):'';
 if(filenames[name])return filenames[name];if(extension==='m')return /(?:#import|@interface|@implementation)\b/.test(text)?'objectivec':'matlab';if(extensions[extension])return extensions[extension];
 const first=text.split('\n',1)[0];if(first.startsWith('#!')){for(const [pattern,language]of [[/\bpython[\d.]*/, 'python'],[/\b(?:ba|z|k)?sh\b/,'bash'],[/\b(?:node|deno|bun)\b/,'javascript'],[/\bperl\b/,'perl'],[/\bruby\b/,'ruby'],[/\b(?:pwsh|powershell)\b/,'powershell']])if(pattern.test(first))return language}
 return ''
}
function identify(text,options){
 const language=choose(text,options);if(language)return {language,method:options.language?'marked':'filename-or-shebang'};
 const sample=text.slice(0,6000);
 // 没有文件名时，先辨认硬件代码等明显结构，再用全部语法判断；中文说明保持原样。
 if(/\b(?:module\s+\w+[\s\S]*?endmodule|always_ff\s*@|always_comb\b)/.test(sample))return {language:'verilog',method:'structure'};
 if(/\bentity\s+\w+\s+is\b/i.test(sample)&&/\barchitecture\b/i.test(sample))return {language:'vhdl',method:'structure'};
 if(/^\s*(?:async\s+)?def\s+\w+\([^\n]*\)\s*(?:->[^:]+)?\s*:|^\s*from\s+[\w.]+\s+import\s/m.test(sample))return {language:'python',method:'structure'};
 if(/\b(?:const|let|var)\s+\w+\s*=|\bfunction\s+\w+\s*\(/.test(sample))return {language:'javascript',method:'structure'};
 if(/^\s*[\[{]/.test(sample)){try{JSON.parse(sample);return {language:'json',method:'structure'}}catch{}}
 if(/^\s*(?:<!doctype\s+html|<\?xml|<[a-z][\w:-]*(?:\s|>))/i.test(sample))return {language:'xml',method:'structure'};
 if(!sample.trim()||((sample.match(/[\u3400-\u9fff]/g)||[]).length>sample.length*.35&&!/[;{}]|=>|^\s*(?:def|class|import)\s/m.test(sample)))return {language:'plaintext',method:'plain'};
 const result=H.highlightAuto(sample);return {language:result.relevance>=3?result.language||'plaintext':'plaintext',method:'automatic'}
}
self.onmessage=({data})=>{const {id,text,options={}}=data,start=performance.now();try{
 const found=identify(text,options),language=found.language;
 const result=language!=='plaintext'&&H.getLanguage(language)?H.highlight(text,{language,ignoreIllegals:true}):null;
 self.postMessage({id,language:result?.language||'plaintext',detected:language,method:found.method,html:result?.value??null,milliseconds:performance.now()-start,languages:H.listLanguages().length})
 }catch{self.postMessage({id,language:'plaintext',html:null,milliseconds:performance.now()-start})}};
