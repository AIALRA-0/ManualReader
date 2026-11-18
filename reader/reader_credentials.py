"""把用户选择记住的项目凭证交给操作系统保存，不写入项目文件。"""
import hashlib, json, os, shutil, subprocess, sys
from urllib.parse import urlsplit

def identity(base,material):
    url=urlsplit(base)
    if url.scheme!='https' or not url.hostname or not material:raise ValueError('记住连接需要 HTTPS 地址和明确的项目编号')
    origin=url.scheme+'://'+url.netloc.lower()
    return 'ManualReader/'+hashlib.sha256((origin+'\n'+material).encode()).hexdigest(),origin

def windows(action,target,token=None):
    import ctypes
    from ctypes import wintypes as w
    class Credential(ctypes.Structure):
        _fields_=[('Flags',w.DWORD),('Type',w.DWORD),('TargetName',w.LPWSTR),('Comment',w.LPWSTR),('LastWritten',w.FILETIME),('CredentialBlobSize',w.DWORD),('CredentialBlob',ctypes.POINTER(ctypes.c_ubyte)),('Persist',w.DWORD),('AttributeCount',w.DWORD),('Attributes',ctypes.c_void_p),('TargetAlias',w.LPWSTR),('UserName',w.LPWSTR)]
    api=ctypes.WinDLL('Advapi32.dll',use_last_error=True)
    if action=='get':
        value=ctypes.POINTER(Credential)();api.CredReadW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.POINTER(ctypes.POINTER(Credential))]
        if not api.CredReadW(target,1,0,ctypes.byref(value)):
            if ctypes.get_last_error()==1168:return None
            raise OSError('操作系统无法读取已保存的阅读器连接')
        try:return ctypes.string_at(value.contents.CredentialBlob,value.contents.CredentialBlobSize).decode('utf8')
        finally:api.CredFree(value)
    if action=='delete':
        api.CredDeleteW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD]
        if not api.CredDeleteW(target,1,0)and ctypes.get_last_error()!=1168:raise OSError('操作系统无法移除阅读器连接')
        return
    data=token.encode('utf8');buffer=(ctypes.c_ubyte*len(data)).from_buffer_copy(data)
    value=Credential(Type=1,TargetName=target,Comment='ManualReader 项目连接',CredentialBlobSize=len(data),CredentialBlob=buffer,Persist=2,UserName='ManualReader')
    api.CredWriteW.argtypes=[ctypes.POINTER(Credential),w.DWORD]
    if not api.CredWriteW(ctypes.byref(value),0):raise OSError('操作系统无法保存阅读器连接')

def access(action,base,material,token=None):
    target,origin=identity(base,material)
    if action=='put'and(not isinstance(token,str)or not 1<=len(token.encode())<=2400):raise ValueError('连接令牌格式不正确')
    if sys.platform=='win32':return windows(action,target,token)
    if sys.platform=='darwin':
        if action=='put':
            # 交互命令从标准输入传入，令牌不出现在进程参数中。
            import shlex
            line='add-generic-password -U -s '+shlex.quote(target)+' -a ManualReader -w '+shlex.quote(token)+'\n'
            result=subprocess.run(['/usr/bin/security','-i'],input=line,text=True,capture_output=True)
        else:result=subprocess.run(['/usr/bin/security','find-generic-password'if action=='get'else'delete-generic-password','-s',target,'-a','ManualReader']+(['-w']if action=='get'else[]),text=True,capture_output=True)
    else:
        program=shutil.which('secret-tool')
        if not program:
            if action=='get':return None
            raise RuntimeError('本机没有系统凭据库客户端 secret-tool；请启用 Secret Service 后再选择记住连接')
        args=[program,'store','--label=ManualReader 项目连接','application','ManualReader','target',target]if action=='put'else[program,'lookup'if action=='get'else'clear','application','ManualReader','target',target]
        result=subprocess.run(args,input=token if action=='put'else None,text=True,capture_output=True)
    if result.returncode:
        if action=='get':return None
        if action!='delete':raise RuntimeError('系统凭据库没有完成保存，请解锁凭据库后重试')
    return result.stdout.strip()if action=='get'and result.returncode==0 else None

def get(base,material):return access('get',base,material)if material else None
def put(base,material,token):return access('put',base,material,token)
def delete(base,material):return access('delete',base,material)
