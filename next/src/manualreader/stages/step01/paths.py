from pathlib import PurePosixPath

OFFICE = {'.doc','.docx','.ppt','.pptx','.xls','.xlsx','.odt','.ods','.odp','.rtf'}
PAGED = {'.pdf','.epub','.mobi','.fb2','.xps','.oxps','.cbz'}
IMAGES = {'.png','.jpg','.jpeg','.gif','.webp','.bmp','.svg','.ico','.avif'}
TEXT = {'.txt','.md','.markdown','.rst','.py','.js','.jsx','.ts','.tsx','.c','.h','.cpp','.hpp','.rs','.go','.java','.cs','.sv','.v','.vh','.vhd','.tcl','.sh','.ps1','.bat','.cmd','.json','.jsonl','.xml','.yaml','.yml','.toml','.ini','.cfg','.conf','.env','.log','.sql','.css','.scss','.html','.htm','.tex','.csv','.tsv','.ipynb','.sdc','.sdf','.lib','.lef','.def'}

def relative(value, empty=False):
    name=str(value or '').replace('\\','/')
    if empty and not name:return ''
    if not name or name.startswith('/') or ':' in name or any(ord(c)<32 for c in name) or len(name)>2000 or any(p in ('','.','..') for p in name.split('/')):
        raise ValueError('请使用项目根目录内的相对路径')
    return PurePosixPath(name).as_posix()
