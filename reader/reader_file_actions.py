"""文件操作保留原文映射与学习记录；删除先核对，再移入可恢复的回收站。"""
from pathlib import Path
import copy,hashlib,json,os,secrets,shutil,threading,time,uuid
from reader_file_events import EVENTS

_ACTION_LOCK=threading.RLock();_REVIEWS={}

class FileActions:
    def _version(self,path,expected):
        if expected is None or str(path.lstat().st_mtime_ns)!=str(expected):raise ValueError('文件已变化，请重新读取后再操作')

    def _fingerprint(self,path):
        digest=hashlib.sha256();count=0
        for item in [path,*sorted(path.rglob('*'))]if path.is_dir()and not path.is_symlink()else[path]:
            stat=item.lstat();digest.update(str((str(item.relative_to(path.parent)),stat.st_mtime_ns,stat.st_size)).encode());count+=1
        return digest.hexdigest(),count

    def _mapped(self,old,new=None):
        value=self.ensure();changed=False
        for fid,name in list(value['sources'].items()):
            if name==old or name.startswith(old+'/'):
                if new is not None:value['sources'][fid]=new+name[len(old):];changed=True
        if changed:
            temporary=self.config.with_suffix('.next.json');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf8');os.replace(temporary,self.config)

    def trash_items(self):
        folder=self.folder/'project-file-trash'
        return {'items':[json.loads(p.read_text('utf8'))for p in sorted(folder.glob('*/record.json'),reverse=True)]if folder.exists()else[]}

    def action(self,data):
        with _ACTION_LOCK:
            action=data.get('action');root=Path(self.ensure()['root']).resolve();name=data.get('path','')
            if action=='restore':
                key=data.get('trash_id','')
                if not isinstance(key,str)or not key.startswith('trash-')or not key[6:].isalnum():raise ValueError('回收站编号无效')
                folder=self.folder/'project-file-trash'/key;record=json.loads((folder/'record.json').read_text('utf8'));destination=self.target(record['path'],False)
                if destination.exists():raise ValueError('原位置已有同名文件，请先重命名或移走它')
                destination.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(folder/'content'),destination);shutil.rmtree(folder)
                EVENTS.publish(root,{'type':'created','paths':[record['path']]});return {'restored':record['path']}
            path=self.target(name,action not in('mkdir','create'))
            if action in('mkdir','create'):
                if path.exists():raise ValueError('这个位置已有同名文件或文件夹')
                if action=='mkdir':path.mkdir(parents=True)
                else:return self.put({'path':name,'content_base64':''})
            elif action in('move','copy'):
                self._version(path,data.get('expected_modified'));destination=self.target(data.get('destination'),False)
                if destination.exists():raise ValueError('目标位置已有同名内容，请换一个名称')
                if destination.resolve().is_relative_to(path.resolve()):raise ValueError('不能把文件夹放进它自己里面')
                destination.parent.mkdir(parents=True,exist_ok=True)
                if action=='move':shutil.move(str(path),destination);self._mapped(name,data['destination'])
                elif path.is_dir():shutil.copytree(path,destination,symlinks=True)
                else:shutil.copy2(path,destination,follow_symlinks=False)
                EVENTS.publish(root,{'type':'moved'if action=='move'else'created','paths':[name,data['destination']]if action=='move'else[data['destination']]})
                return {'path':data['destination'],'previous_path':name,'action':action}
            elif action=='trash-preview':
                self._version(path,data.get('expected_modified'));fingerprint,count=self._fingerprint(path);review=secrets.token_urlsafe(24)
                for key in list(_REVIEWS):
                    if _REVIEWS[key]['expires']<time.monotonic():_REVIEWS.pop(key,None)
                _REVIEWS[review]={'root':str(root),'path':name,'fingerprint':fingerprint,'expires':time.monotonic()+180}
                return {'review_id':review,'path':name,'entries':count,'requires_confirmation':True}
            elif action=='trash':
                review=_REVIEWS.pop(data.get('review_id',''),None)
                if data.get('confirmed')is not True or not review or review['root']!=str(root)or review['path']!=name or review['expires']<time.monotonic():raise ValueError('删除前请先读取删除预览，再明确确认')
                if self._fingerprint(path)[0]!=review['fingerprint']:raise ValueError('预览后内容已变化，请重新核对再删除')
                key='trash-'+uuid.uuid4().hex;folder=self.folder/'project-file-trash'/key;folder.mkdir(parents=True);record={'id':key,'path':name,'deleted_at':time.time()};(folder/'record.json').write_text(json.dumps(record,ensure_ascii=False),'utf8');shutil.move(str(path),folder/'content');EVENTS.publish(root,{'type':'deleted','paths':[name]});return {'trashed':name,'trash_id':key}
            else:raise ValueError('请选择新建、移动、复制、移入回收站或恢复操作')
            EVENTS.publish(root,{'type':'created','paths':[name]});return {'path':name,'action':action}
