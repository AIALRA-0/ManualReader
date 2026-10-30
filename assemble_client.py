"""保留已有五栏拖动布局，装入本次共用交互组件。"""
from pathlib import Path
ROOT=Path(__file__).resolve().parent
prefix=(ROOT/'client-base.js').read_text('utf8')

tail=(ROOT/'client-tail.js').read_text('utf8').replace("studyKey='',deferredInteraction=false;","studyKey='';")
tail=tail.replace('/* READER_INTERACTION_GROUP */',(ROOT/'client-interaction.js').read_text('utf8'))
(ROOT/'static-next/app.js').write_text(prefix+tail,'utf8')
