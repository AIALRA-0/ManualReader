// 下载界面时启动读取；主程序领取同一结果后不再保留它。
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict'),path=require('path');
const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8').replace(/\r\n/g,'\n');
const code=html.slice(html.indexOf('(function(){\n const requested='),html.indexOf('</script>',html.indexOf('(function(){\n const requested=')));
async function run(query){
 const calls=[],ReaderBoot={},context={ReaderBoot,URL,location:{href:'http://localhost/'+query},fetch:async(url,options)=>{calls.push({url,options});return{ok:true,json:async()=>url==='/api/session'?{pin:'test-pin'}:{path:url}}}};
 vm.createContext(context);vm.runInContext(code,context);await ReaderBoot.session;
 const pending=ReaderBoot.takePrepared('test');
 if(query){const result=await pending;assert.equal(result.length,2);assert.deepEqual(calls.map(x=>x.url),['/api/session','/api/material','/api/progress']);assert.equal(calls[1].options.headers['X-Reader-Material'],'test')}
 else{assert.equal(pending,null);assert.equal(calls.length,1)}
 assert.equal(ReaderBoot.takePrepared('test'),null);assert.equal('prepared'in ReaderBoot,false);
}
run('?material=test').then(()=>run('')).then(()=>console.log('STARTUP_OVERLAP_OK')).catch(e=>{console.error(e);process.exitCode=1});
