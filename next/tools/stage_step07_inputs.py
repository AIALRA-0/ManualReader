"""Stage the accepted STEP-04/06 inputs and optionally replay saved model replies."""
import argparse
import shutil
from pathlib import Path

from manualreader.stages.step05.contracts import digest
from manualreader.artifacts import write_json as write


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('base',type=Path)
    parser.add_argument('root',type=Path)
    parser.add_argument('--replies',type=Path)
    args=parser.parse_args()
    base,root=args.base.resolve(),args.root.resolve()
    if root==base or root.is_relative_to(base) or base.is_relative_to(root):
        parser.error('准备输出需要与上一轮验收目录完全分开')
    root.mkdir(parents=True,exist_ok=True)
    frozen={}
    for name in ('inputs','jobs','media','workspace/src','isolated-data'):
        for path in (base/name).rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and not any(v.endswith('.egg-info') for v in path.parts):
                frozen[path.relative_to(base).as_posix()]=digest(path.read_bytes())
    write(root/'baseline-sha256.json',frozen)
    for name in ('inputs','media'):
        shutil.copytree(base/name,root/name,dirs_exist_ok=True)
    for folder in (base/'jobs').iterdir():
        if not folder.is_dir():
            continue
        prepared=root/'previous'/folder.name
        prepared.mkdir(parents=True,exist_ok=True)
        for path in folder.glob('*.json'):
            shutil.copy2(path,prepared/path.name)
        if args.replies:
            state=args.replies/folder.name/'model/outcome.json'
            target=root/'jobs'/folder.name/'model'
            target.mkdir(parents=True,exist_ok=True)
            if not state.is_file():
                raise FileNotFoundError(state)
            shutil.copy2(state,target/state.name)
    if len(list((root/'previous').iterdir()))!=40:
        raise ValueError('应准备 40 份上一轮任务')
    print('40 份任务准备完成')


if __name__=='__main__':
    main()
