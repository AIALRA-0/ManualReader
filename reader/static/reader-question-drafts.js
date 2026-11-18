/* 已同步的问题从服务器读取，本机只保存等待同步的输入。 */
window.ReaderQuestionDrafts=(()=>{
 const store='pending-questions',legacy=project=>'reader-question-sync:'+project;
 let opened;
 function database(){
  return opened??=new Promise((resolve,reject)=>{
   try{
    const req=indexedDB.open('reader-pending-inputs',1);
    req.onupgradeneeded=()=>req.result.createObjectStore(store,{keyPath:['material_id','client_id']})
      .createIndex('project','material_id');
    req.onerror=()=>reject(req.error);
    req.onblocked=()=>reject(Error('草稿数据库暂不可用'));
    req.onsuccess=()=>resolve(req.result);
   }catch(error){reject(error)}
  });
 }
 async function transaction(mode,work){
  const db=await database();
  return new Promise((resolve,reject)=>{
   const tx=db.transaction(store,mode);let result;
   tx.oncomplete=()=>resolve(result?.result);
   tx.onabort=()=>reject(tx.error||Error('保存草稿未完成'));
   try{result=work(tx.objectStore(store))}catch(error){tx.abort();reject(error)}
  });
 }
 function pending(row){return !!(row?.material_id&&row?.client_id&&row.version>row.saved_version)}
 function offline(project){
  try{
   const raw=localStorage.getItem(legacy(project));
   const rows=JSON.parse(raw||'[]');
   return Array.isArray(rows)?{raw,rows:rows.filter(row=>pending(row)&&row.material_id===project)}:null;
  }catch{return null}
 }
 function replaceLegacy(project,rows){
  if(rows.length)localStorage.setItem(legacy(project),JSON.stringify(rows));
  else localStorage.removeItem(legacy(project));
 }
 function updateLocal(row){
  const backup=offline(row.material_id);
  if(!backup)throw Error('旧草稿尚未读完');
  const rows=new Map(backup.rows.map(item=>[item.client_id,item]));
  const old=rows.get(row.client_id);
  if(!old||old.version<=row.version){
   if(pending(row))rows.set(row.client_id,row);
   else rows.delete(row.client_id);
   replaceLegacy(row.material_id,[...rows.values()]);
  }
 }
 function saveRows(rows){
  return transaction('readwrite',items=>{
   for(const row of rows){
    const id=[row.material_id,row.client_id],previous=items.get(id);
    previous.onsuccess=()=>{
     if(previous.result?.version>row.version)return;
     if(pending(row))items.put(row);else items.delete(id);
    };
   }
  });
 }
 async function load(project){
  const backup=offline(project);
  try{
   if(backup?.rows.length)await saveRows(backup.rows);
   const rows=await transaction('readonly',items=>items.index('project').getAll(project));
   if(backup)try{if(localStorage.getItem(legacy(project))===backup.raw)replaceLegacy(project,[])}catch{}
   return rows;
  }catch{
   return offline(project)?.rows||[];
  }
 }
 async function save(block){
  const {timer,saving,error,deviceSaved,...row}=block;
  try{await saveRows([row]);return true}catch{
   try{updateLocal(row);return true}catch{return false}
  }
 }
 return {load,save};
})();
