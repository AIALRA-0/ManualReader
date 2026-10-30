"""沿用正式材料树的文件夹、移动和回收批次规则，原件与笔记仍按材料 ID 保存。"""
from pathlib import Path
from datetime import datetime, timezone
import copy
import json
import uuid


def catalog(path, materials):
    source=Path(path)
    value=json.loads(source.read_text('utf8')) if source.exists() else {'folders':[],'materials':[]}
    folders=[{'id':'root','name':'根目录','parent_id':None,'deleted':False},*[row for row in value['folders'] if row['id']!='root']]
    saved={row['id']:row for row in value['materials']}
    rows=[{**item,'name':saved.get(item['id'],{}).get('name',item['title']),'folder_id':'root','deleted':False,**saved.get(item['id'],{})} for item in materials]
    return {'folders':folders,'materials':rows}


def save_catalog(path, value):
    target=Path(path);target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix('.next.json')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf8');temporary.replace(target)


def clean_name(value):
    if not isinstance(value,str) or not value.strip() or len(value.strip())>300 or any(ord(c)<32 for c in value):
        raise ValueError('名称请写在一行内，最多 300 字')
    return value.strip()


def descendants(tree, identity):
    ids={identity}
    while True:
        expanded=ids|{row['id'] for row in tree['folders'] if row.get('parent_id') in ids}
        if expanded==ids:return ids
        ids=expanded


def change(path, materials, kind, identity, action, data):
    tree=copy.deepcopy(catalog(path,materials));folders={row['id']:row for row in tree['folders']}
    if kind not in ('folder','material'):raise ValueError('材料树项目类型不正确')
    rows=tree['folders'] if kind=='folder' else tree['materials']
    row=next((item for item in rows if item['id']==identity),None)
    if action=='create' and kind=='folder':
        parent=data.get('parent_id','root')
        if parent not in folders or folders[parent].get('deleted'):raise ValueError('请选择仍在材料树中的文件夹')
        name=clean_name(data.get('name'))
        if any(item['parent_id']==parent and item['name'].casefold()==name.casefold() and not item.get('deleted') for item in tree['folders']):raise ValueError('此位置已经有同名文件夹')
        row={'id':'folder-'+uuid.uuid4().hex[:14],'name':name,'parent_id':parent,'deleted':False};rows.append(row)
    else:
        if not row or row.get('archived'):raise ValueError('没有找到这个材料树项目')
        if identity=='root':raise ValueError('根目录不能修改或回收')
        parent_key='parent_id' if kind=='folder' else 'folder_id'
        if action=='rename':row['name']=clean_name(data.get('name'))
        elif action=='move':
            parent=data.get('parent_id')
            if parent not in folders or folders[parent].get('deleted'):raise ValueError('请选择仍在材料树中的文件夹')
            if row.get('deleted'):raise ValueError('请先恢复，再移动')
            if kind=='folder' and parent in descendants(tree,identity):raise ValueError('文件夹不能移动到自身或自己的子文件夹中')
            row[parent_key]=parent
        elif action=='trash':
            if data.get('confirmed') is not True:raise ValueError('请先确认要移入回收站的名称')
            batch=uuid.uuid4().hex;ids=descendants(tree,identity) if kind=='folder' else set()
            targets=[item for item in tree['folders'] if item['id'] in ids]+[item for item in tree['materials'] if item['folder_id'] in ids] if kind=='folder' else [row]
            for item in targets:
                if not item.get('deleted'):item.update(deleted=True,trash_batch=batch)
        elif action=='restore':
            parent=row.get(parent_key,'root')
            if parent not in folders or folders[parent].get('deleted'):row[parent_key]='root'
            batch=row.get('trash_batch')
            targets=[item for items in (tree['folders'],tree['materials']) for item in items if batch and item.get('trash_batch')==batch] if kind=='folder' else [row]
            for item in targets:item.update(deleted=False,trash_batch=None)
        else:raise ValueError('没有这个材料树操作')
    for group,key in [(tree['folders'],'parent_id')]:
        siblings=[item for item in group if not item.get('deleted')]
        names=[(item.get(key),item['name'].casefold()) for item in siblings]
        if len(names)!=len(set(names)):raise ValueError('此位置已经有同名文件夹')
    save_catalog(path,tree)
    return row


def clear_trash(path, materials, confirmed):
    if confirmed is not True:raise ValueError('请先确认清空回收站')
    tree=catalog(path,materials);items=[row for rows in (tree['folders'],tree['materials']) for row in rows if row.get('deleted') and not row.get('archived')]
    if not items:return {'count':0}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    archive=Path(path).parent/'archive';archive.mkdir(parents=True,exist_ok=True)
    (archive/('catalog-'+stamp+'.json')).write_text(json.dumps({'items':items,'description':'已从回收站列表移除，原件、正文和笔记继续保留在原材料目录'},ensure_ascii=False,indent=2),'utf8')
    for item in items:item['archived']=True
    save_catalog(path,tree)
    return {'count':len(items),'archived':True}
