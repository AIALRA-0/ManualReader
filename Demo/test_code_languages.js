const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const folder=path.join(__dirname,'../vendor/code-highlight');let reply;
const scope={console,performance,self:{postMessage:value=>reply=value},importScripts:name=>vm.runInContext(fs.readFileSync(path.join(folder,name),'utf8'),context)};
const context=vm.createContext(scope);vm.runInContext(fs.readFileSync(path.join(folder,'worker.js'),'utf8'),context);
const languages=scope.self.ReaderHighlighter.listLanguages();assert.equal(languages.length,193);
for(const language of languages){scope.self.onmessage({data:{id:language,text:'sample = 42;\n"hello"\n',options:{language}}});assert.equal(reply.detected,language);assert.ok(reply.milliseconds>=0)}
scope.self.onmessage({data:{id:1,text:'<script>globalThis.bad=true</script>',options:{language:'html'}}});assert.ok(!reply.html.includes('<script>'));assert.equal(scope.bad,undefined);
console.log(JSON.stringify({passed:true,languages:languages.length}));
